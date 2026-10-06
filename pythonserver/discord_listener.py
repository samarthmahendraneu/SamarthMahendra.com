"""Always-on Discord bot for questions to Samarth, and the call back scheduler.

Runs next to the Celery worker on the pythonserver service (start_workers.sh).
It stays logged in so posting a question is instant: ask_and_get_reply
connects a fresh client per question and can spend 30s just reaching ready,
which is longer than a caller will hold.

Responsibilities:
  - post queued questions to the channel, remembering which message is which
  - match Samarth's replies to their question by Discord's reply-to link
  - tell whoever asked: a live call speaks the reply, a website chat gets a
    message, and a caller who hung up is rung back if they asked to be
  - have scheduled call backs dialled as they fall due (callback_scheduler.py)
"""

import asyncio
import logging
import os
import re

import discord
from dotenv import load_dotenv

load_dotenv()

import callback_scheduler
import redis_pool
from callbacks import CallbackStore
from events import EventStream, channel_id, channel_kind, valid_channel
from question_store import QuestionStore, clip

logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
POLL_INTERVAL = 0.5
NO_PINGS = discord.AllowedMentions.none()
WHICH_QUESTION = ("More than one person is waiting on an answer. Reply to the question you're "
                  "answering (hover over it and choose Reply) so it reaches the right person.")


def origin_of(record):
    """The conversation that asked. Questions from before the event streams
    carried only a call SID, which older calls left blank."""
    origin = record.get("origin")
    if valid_channel(origin):
        return origin
    call_sid = record.get("call_sid")
    return "call:" + call_sid if call_sid and valid_channel("call:" + call_sid) else None


def enqueue_chat_followup(session_id):
    """Have the worker compose the chat's message; imported late so the
    listener starts even if Celery is misconfigured."""
    from celery_worker import celery_app
    celery_app.send_task("celery_worker.chat_followup", args=[session_id])


class Listener(discord.Client):
    def __init__(self, store, channel_id, *, events=None, callbacks=None,
                 chat_followup=enqueue_chat_followup, answerers=frozenset(), **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.channel_id = channel_id
        # Discord user ids whose words count as Samarth's answers; empty for
        # anyone in the channel.
        self.answerers = answerers
        self.events = events or EventStream(store.redis)
        self.callbacks = callbacks or CallbackStore(store.redis)
        self.chat_followup = chat_followup
        self.channel = None
        self.pump = None
        self.scheduler = None

    async def setup_hook(self):
        # Before the gateway connects: call backs don't wait on Discord.
        self.scheduler = asyncio.create_task(callback_scheduler.run(self.callbacks, self.notify))
        logger.info("Call back scheduler running; %s dials", callback_scheduler.TWILIO_SERVICE_URL)

    async def on_ready(self):
        self.channel = self.get_channel(self.channel_id)
        if self.channel is None:
            logger.error("Discord channel %s not visible to this bot", self.channel_id)
            return
        logger.info("Discord listener ready as %s on channel %s", self.user, self.channel_id)
        if self.pump is None:
            self.pump = asyncio.create_task(self.post_pending())

    async def notify(self, text, about=None):
        """Post to the channel; `about` threads it under a question as a reply,
        and ties it to that question so a reply to this note counts too."""
        if self.channel is None:
            logger.warning("Discord channel not ready; dropped notice: %s", text)
            return
        reference = None
        if about and about.get("discord_message_id"):
            reference = discord.MessageReference(message_id=int(about["discord_message_id"]),
                                                 channel_id=self.channel_id, fail_if_not_exists=False)
        message = await self.channel.send(text, reference=reference, allowed_mentions=NO_PINGS)
        if about:
            await asyncio.to_thread(self.store.remember_post, about["id"], message.id)

    async def post_pending(self):
        """Post questions as they're asked; ring back calls that ended mid-reply."""
        while not self.is_closed():
            try:
                record = await asyncio.to_thread(self.store.pop_for_posting)
                if record is not None:
                    await self.post_question(record)
                question_id = await asyncio.to_thread(self.store.pop_callback)
                if question_id is not None:
                    handed_over = await asyncio.to_thread(self.store.get, question_id)
                    if handed_over is not None:
                        await self.maybe_call_back(handed_over)
                if record is None and question_id is None:
                    await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to post a question or arrange a handed-over callback")
                await asyncio.sleep(POLL_INTERVAL)

    async def post_question(self, record):
        who = record.get("caller_name") or "Someone"
        origin = origin_of(record)
        where = "on the website chat" if origin and channel_kind(origin) == "chat" else "on the phone"
        message = await self.channel.send(
            f"**{who} is {where} and asks:**\n{record['question']}\n"
            "_(Reply to this message to answer. If they're still there, they'll hear it straight away.)_",
            allowed_mentions=NO_PINGS)
        await asyncio.to_thread(self.store.remember_post, record["id"], message.id)
        logger.info("Posted question %s to Discord", record["id"])

    async def on_message(self, message):
        if message.author.id == self.user.id or message.author.bot is True:
            return
        if self.answerers and message.author.id not in self.answerers:
            # Only Samarth's words reach callers, and only his ring them back.
            return
        if message.channel.id == self.channel_id:
            reference = message.reference.message_id if message.reference else None
        elif getattr(message.channel, "parent_id", None) == self.channel_id:
            # A thread started from a question shares that message's id.
            reference = message.channel.id
        else:
            return
        await self.on_reply(message.content, reference)

    async def on_reply(self, content, reference=None):
        """Samarth wrote in the channel: work out which question it answers."""
        question_id = None
        if reference is not None:
            question_id = await asyncio.to_thread(self.store.question_for_post, reference)
        if question_id is None:
            waiting = await asyncio.to_thread(self.store.open_questions)
            if not waiting:
                return          # nothing was waiting; ordinary channel chatter
            people = {origin_of(record) or record["id"] for record in waiting}
            if reference is not None or len(people) > 1:
                # A reply to some other message, or two people waiting: never guess.
                await self.notify(WHICH_QUESTION)
                return
            # One conversation waiting, perhaps on the same thing asked twice:
            # its latest question.
            question_id = waiting[-1]["id"]
        record = await asyncio.to_thread(self.store.answer, question_id, content)
        if record is not None:
            logger.info("Recorded reply for question %s", record["id"])
            await self.deliver(record, "question.answered")
            return
        record = await asyncio.to_thread(self.store.add_followup, question_id, content)
        if record is not None:
            logger.info("Recorded follow-up for question %s", record["id"])
            await self.deliver(record, "question.followup", content)

    async def deliver(self, record, kind, text=None):
        """Get Samarth's words to whoever asked, and say in Discord how."""
        origin = origin_of(record)
        wants_call = (kind == "question.answered"
                      and await asyncio.to_thread(self.store.callback_request, record))
        if origin and channel_kind(origin) == "chat":
            # The chat's open page keeps it live; a visitor who left and asked
            # for a call is rung instead. Booked before the chat hears the
            # answer, so what it tells them can say a call is coming.
            here = await asyncio.to_thread(self.events.is_live, origin)
            if wants_call and not here:
                await self.maybe_call_back(record)
            await asyncio.to_thread(self.events.publish, origin, kind,
                                    question_id=record["id"], text=text)
            try:
                await asyncio.to_thread(self.chat_followup, channel_id(origin))
            except Exception:
                logger.exception("Could not hand the reply to the chat for question %s", record["id"])
                if here:
                    await self.notify("Saved, but I couldn't pass it to their chat just now.", about=record)
                    return
            if wants_call and here:
                await self.notify("They're still in the chat, so they'll see it there; no call needed.",
                                  about=record)
            elif not wants_call:
                booked = None if here else await asyncio.to_thread(self.ride_along, origin, record, text)
                if booked:
                    await self.notify("Sent to their chat window. They've left it, so Luma will also "
                                      f"pass it on when it calls them on {self.callbacks.when_for_samarth(booked)}, "
                                      "as they booked.", about=record)
                else:
                    await self.notify("Sent to their chat window.", about=record)
            return
        if origin:
            await asyncio.to_thread(self.events.publish, origin, kind,
                                    question_id=record["id"], text=text)
        who = record.get("caller_name") or "The caller"
        live = await asyncio.to_thread(self.caller_on_line, origin, record["id"])
        if live:
            # The call's own watch speaks it within a second.
            await self.notify(f"{who} is still on the call, so they'll hear that now.", about=record)
        elif wants_call:
            await self.maybe_call_back(record)
        elif booked := await asyncio.to_thread(self.ride_along, origin, record, text):
            # They booked a call for a time of their own instead of asking to
            # be rung with the answer: that call takes it.
            await self.notify(f"{who} has hung up, but Luma calls them back on "
                              f"{self.callbacks.when_for_samarth(booked)} as they asked, and will pass "
                              "this on.", about=record)
        elif kind == "question.followup":
            await self.notify(f"{who} already had your first answer and has hung up. This is saved.",
                              about=record)
        elif await asyncio.to_thread(self.callbacks.timed_for, origin):
            await self.notify(f"{who} has hung up, and the call back they booked has already gone "
                              "out. The reply is saved.", about=record)
        else:
            await self.notify(f"{who} had already hung up and didn't ask for a call back. "
                              "The reply is saved.", about=record)

    def ride_along(self, origin, record, text=None):
        """Give Samarth's words (an answer, or `text` following one up) to the
        call back this conversation booked for a time of its own, if it hasn't
        rung yet, so the call passes them on: that call back, or None."""
        pending = self.callbacks.pending_for(origin)
        if not pending:
            return None
        booked = pending[0]
        self.callbacks.add_answer(booked["id"], clip(record["question"], 300),
                                  clip(text or record["reply"], 900))
        return booked

    def caller_on_line(self, origin, question_id):
        if origin and self.events.is_live(origin):
            return True
        return self.store.is_live_legacy(question_id)

    async def maybe_call_back(self, record):
        """Ring them back with the answer if they asked; once, however many
        times it's decided (QuestionStore.claim_callback)."""
        try:
            booking = await asyncio.to_thread(self.store.book_answer_call, record["id"], self.callbacks)
        except ValueError as exc:
            logger.warning("Callback for question %s refused: %s", record["id"], exc)
            await self.notify(f"I couldn't book the call back: {exc}. They have not been told.",
                              about=record)
            return
        except Exception:
            logger.exception("Callback booking failed for question %s", record["id"])
            await self.notify("I could not book the call back. They have not been told.", about=record)
            return
        if booking is None:
            return
        record, booked = booking
        name = record["callback_name"]
        logger.info("Callback %s booked for question %s", booked["id"], record["id"])
        if booked["due_at"] <= booked["created_at"] + 1:
            await self.notify(f"Calling {name or 'them'} back now.", about=record)
        else:
            await self.notify(f"It's outside calling hours where they are, so I'll call "
                              f"{name or 'them'} back {self.callbacks.when_for_samarth(booked)}.",
                              about=record)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        raise ValueError("DISCORD_TOKEN and DISCORD_CHANNEL_ID must be set")
    # Two is plenty: the question pump, the call back scheduler and message
    # handlers each hold a connection only for the length of one command.
    client = redis_pool.connect(max_connections=2)
    store = QuestionStore(client)
    answerers = frozenset(int(i) for i in re.findall(r"\d+", os.getenv("DISCORD_ANSWER_USER_IDS", "")))
    if not answerers:
        logger.warning("DISCORD_ANSWER_USER_IDS not set: anyone who can post in the channel "
                       "can answer callers")

    intents = discord.Intents.default()
    intents.messages = True
    intents.guilds = True
    # Required to read reply text, and must also be enabled in the bot's
    # settings in the Discord developer portal.
    intents.message_content = True
    Listener(store, int(DISCORD_CHANNEL_ID),
             events=EventStream(client), callbacks=CallbackStore.from_env(client, os.environ),
             answerers=answerers, intents=intents).run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
