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
  - place scheduled call backs as they fall due (callback_scheduler.py)
"""

import asyncio
import logging
import os

import discord
from dotenv import load_dotenv

load_dotenv()

import callback_scheduler
import redis_pool
from callbacks import CallbackStore
from events import EventStream, channel_id, channel_kind, valid_channel
from question_store import QuestionStore

logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://twillio-ai-assistant.onrender.com").rstrip("/")
POLL_INTERVAL = 0.5
NO_PINGS = discord.AllowedMentions.none()
WHICH_QUESTION = ("More than one person is waiting on an answer. Reply to the question you're "
                  "answering (hover over it and choose Reply) so it reaches the right person.")


def clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


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
    def __init__(self, store, channel_id, twilio_client=None, from_number=None, *,
                 events=None, callbacks=None, chat_followup=enqueue_chat_followup, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.channel_id = channel_id
        self.twilio_client = twilio_client
        self.from_number = from_number
        self.events = events or EventStream(store.redis)
        self.callbacks = callbacks or CallbackStore(store.redis)
        self.chat_followup = chat_followup
        self.channel = None
        self.pump = None
        self.scheduler = None

    async def setup_hook(self):
        # Before the gateway connects: call backs don't wait on Discord.
        if self.twilio_client is None:
            logger.warning("Twilio not configured; scheduled call backs will not be placed")
            return
        self.scheduler = asyncio.create_task(callback_scheduler.run(
            self.callbacks, self.twilio_client, self.from_number, PUBLIC_BASE_URL, self.notify))
        logger.info("Call back scheduler running from %s", self.from_number)

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
        if message.author.id == self.user.id:
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
            if reference is not None or len(waiting) > 1:
                # A reply to some other message, or ambiguous: never guess.
                await self.notify(WHICH_QUESTION)
                return
            question_id = waiting[0]["id"]
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
        who = record.get("caller_name") or "They"
        origin = origin_of(record)
        if origin:
            await asyncio.to_thread(self.events.publish, origin, kind,
                                    question_id=record["id"], text=text)
        if origin and channel_kind(origin) == "chat":
            # The chat's open page keeps it live; a visitor who left and asked
            # for a call is rung instead.
            here = await asyncio.to_thread(self.events.is_live, origin)
            try:
                await asyncio.to_thread(self.chat_followup, channel_id(origin))
            except Exception:
                logger.exception("Could not hand the reply to the chat for question %s", record["id"])
                if here:
                    await self.notify("Saved, but I couldn't pass it to their chat just now.", about=record)
                    return
            wants_call = kind == "question.answered" and record.get("callback_state") == "requested"
            if wants_call and not here:
                await self.maybe_call_back(record)
            elif wants_call:
                await self.notify("They're still in the chat, so they'll see it there; no call needed.",
                                  about=record)
            else:
                await self.notify("Sent to their chat window.", about=record)
            return
        live = await asyncio.to_thread(self.caller_on_line, origin, record["id"])
        if live:
            # The call's own watch speaks it within a second.
            await self.notify(f"{who} is still on the call, so they'll hear that now.", about=record)
        elif kind == "question.answered" and record.get("callback_state") == "requested":
            await self.maybe_call_back(record)
        elif kind == "question.followup":
            await self.notify(f"{who} already had your first answer and has hung up. This is saved.",
                              about=record)
        else:
            await self.notify(f"{who} had already hung up and didn't ask for a call back. "
                              "The reply is saved.", about=record)

    def caller_on_line(self, origin, question_id):
        if origin and self.events.is_live(origin):
            return True
        return self.store.is_live_legacy(question_id)

    async def maybe_call_back(self, record):
        if record.get("callback_state") != "requested" or not record.get("callback_number"):
            return
        # Claim before booking: a duplicate reply must not ring twice.
        if not await asyncio.to_thread(self.store.claim_callback, record["id"]):
            return
        name = record.get("callback_name") or ""
        question, reply = clip(record["question"], 300), clip(record["reply"], 900)
        try:
            booked = await asyncio.to_thread(
                self.callbacks.schedule, record["callback_number"], name,
                purpose=f"You asked: {question} Samarth's answer is: {reply}",
                voicemail=(f"{'Hi ' + name if name else 'Hi'}, this is Luma, Samarth Mahendra's AI assistant, calling back "
                           f"with his answer to your question. You asked: {question}. "
                           f"He says: {reply}. To talk it through, call this number back. Goodbye."),
                source="question", question_id=record["id"], origin=origin_of(record),
                tz=record.get("callback_timezone"))
        except ValueError as exc:
            logger.warning("Callback for question %s refused: %s", record["id"], exc)
            await self.notify(f"I couldn't book the call back: {exc}. They have not been told.",
                              about=record)
            return
        except Exception:
            logger.exception("Callback booking failed for question %s", record["id"])
            await self.notify("I could not book the call back. They have not been told.", about=record)
            return
        logger.info("Callback %s booked for question %s", booked["id"], record["id"])
        if self.scheduler is None:
            # Booked, and placed once the worker can dial, but not by this one.
            await self.notify("I booked the call back, but this worker can't place calls: set "
                              "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN on it.", about=record)
        elif booked["due_at"] <= booked["created_at"] + 1:
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
    twilio_client = None
    if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"):
        from twilio.rest import Client
        twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    else:
        logger.warning("Twilio not configured; callbacks will be recorded but not placed")

    intents = discord.Intents.default()
    intents.messages = True
    intents.guilds = True
    # Required to read reply text, and must also be enabled in the bot's
    # settings in the Discord developer portal.
    intents.message_content = True
    Listener(store, int(DISCORD_CHANNEL_ID), twilio_client,
             os.getenv("TWILIO_FROM_NUMBER", "+18339703274"),
             events=EventStream(client), callbacks=CallbackStore.from_env(client, os.environ),
             intents=intents).run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
