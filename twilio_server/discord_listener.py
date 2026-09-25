"""Always-on Discord bot for live questions to Samarth.

Runs as its own process. It stays logged in so posting a question is instant:
ask_and_get_reply connects a fresh client per question and can spend 30s just
reaching ready, which is longer than a caller will hold.

Responsibilities:
  - post queued questions to the channel
  - record replies against the question they answer
  - ring the caller back when a reply lands on a question that asked for one
"""

import asyncio
import logging
import os

import discord
import redis
from dotenv import load_dotenv

load_dotenv()

from question_store import QuestionStore

logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://twillio-ai-assistant.onrender.com").rstrip("/")
POLL_INTERVAL = 0.5


def place_callback(record, twilio_client, from_number):
    """Ring the caller back with the answer as the call's purpose."""
    from urllib.parse import urlencode
    query = urlencode({
        "script": "2",
        "name": record.get("callback_name") or "",
        "message": (
            f"You asked: {record['question']} Samarth's answer is: {record['reply']}. "
            "Share this answer, then offer to help with anything else."
        ),
    })
    return twilio_client.calls.create(
        to=record["callback_number"], from_=from_number,
        url=f"{PUBLIC_BASE_URL}/incoming-call?{query}",
    )


class Listener(discord.Client):
    def __init__(self, store, channel_id, twilio_client=None, from_number=None, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.channel_id = channel_id
        self.twilio_client = twilio_client
        self.from_number = from_number
        self.channel = None
        self.pump = None

    async def on_ready(self):
        self.channel = self.get_channel(self.channel_id)
        if self.channel is None:
            logger.error("Discord channel %s not visible to this bot", self.channel_id)
            return
        logger.info("Discord listener ready as %s on channel %s", self.user, self.channel_id)
        if self.pump is None:
            self.pump = asyncio.create_task(self.post_pending())

    async def post_pending(self):
        """Drain queued questions onto the channel as they are asked."""
        while not self.is_closed():
            try:
                record = await asyncio.to_thread(self.store.pop_for_posting)
                if record is None:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                who = record.get("caller_name") or "A caller"
                await self.channel.send(
                    f"**{who} is on the phone and asks:**\n{record['question']}\n"
                    f"_(reply here; answering within ~15s reaches them live)_"
                )
                logger.info("Posted question %s to Discord", record["id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to post a queued question")
                await asyncio.sleep(POLL_INTERVAL)

    async def on_message(self, message):
        if message.author.id == self.user.id or message.channel.id != self.channel_id:
            return
        record = await asyncio.to_thread(self.store.answer, message.content)
        if record is None:
            return          # nothing was waiting; ordinary channel chatter
        logger.info("Recorded reply for question %s", record["id"])
        await self.maybe_call_back(record)

    async def maybe_call_back(self, record):
        if record.get("callback_state") != "requested" or not record.get("callback_number"):
            return
        if not self.twilio_client:
            logger.error("Callback requested for %s but Twilio is not configured", record["id"])
            return
        # Claim before dialling: a duplicate reply must not ring twice.
        if not await asyncio.to_thread(self.store.claim_callback, record["id"]):
            return
        try:
            call = await asyncio.to_thread(place_callback, record, self.twilio_client, self.from_number)
            logger.info("Callback placed for question %s call=%s", record["id"], call.sid)
            await self.channel.send(f"Calling {record.get('callback_name') or 'them'} back now.")
        except Exception:
            logger.exception("Callback failed for question %s", record["id"])
            await self.channel.send("I could not place the callback. They have not been told.")


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not DISCORD_TOKEN or not DISCORD_CHANNEL_ID:
        raise ValueError("DISCORD_TOKEN and DISCORD_CHANNEL_ID must be set")
    store = QuestionStore(redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                         socket_connect_timeout=5, socket_timeout=5))
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
             intents=intents).run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
