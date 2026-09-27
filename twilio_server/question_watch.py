"""Deliver Samarth's Discord replies into the call that asked for them.

The listener records a reply in Redis, but nothing tells the call directly.
So for as long as a call is live this polls the questions that call asked,
speaks a reply as soon as one lands, and keeps each question's live key fresh
so the listener knows the caller is still on the line and must not be rung
back. When the call ends, any reply that arrived too late to be spoken is
handed to the listener as a callback.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)
POLL_INTERVAL = 1.0


# Commentary accepts up to 500 tokens, and the bridge treats most rejections
# as fatal, so the parts that come from Discord are capped well below that
# (roughly 4 characters a token, leaving room for the fixed wording).
MAX_QUESTION_CHARS = 240
MAX_REPLY_CHARS = 900


def clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def reply_commentary(record):
    """The result for GPT-Live to paraphrase to the caller.

    A statement, not an instruction: session.commentary.append is spoken as a
    result, so the Discord text is relayed rather than obeyed.
    """
    said = (f'Samarth has replied to the question "{clip(record["question"], MAX_QUESTION_CHARS)}": '
            f'{clip(record["reply"], MAX_REPLY_CHARS)}')
    if record.get("callback_state") == "requested":
        said += " Since he answered while they are still on the call, the call back they asked for is no longer needed."
    return said


async def watch_questions(bridge, asked, store, interval=POLL_INTERVAL):
    """Run for the length of a call; the caller cancels it when the call ends."""
    delivered = set()
    while not bridge.closing:
        for question_id in list(asked):
            if question_id in delivered:
                continue
            try:
                await asyncio.to_thread(store.mark_live, question_id)
                record = await asyncio.to_thread(store.get, question_id)
            except Exception as exc:
                # A Redis blip must not end the call; try again next tick.
                logger.warning("Question watch read failed (%s: %s)", type(exc).__name__, exc)
                continue
            if record and record["status"] == "answered" and await bridge.say(reply_commentary(record)):
                await asyncio.to_thread(store.mark_delivered, question_id)
                delivered.add(question_id)
                logger.info("Delivered Samarth's reply live question=%s", question_id)
        await asyncio.sleep(interval)


async def finish_questions(asked, store):
    """At call end: stop counting as live, and hand unspoken replies to the listener."""
    for question_id in asked:
        try:
            await asyncio.to_thread(store.clear_live, question_id)
            record = await asyncio.to_thread(store.get, question_id)
            if (record and record["status"] == "answered" and not record.get("delivered_live")
                    and record.get("callback_state") == "requested"):
                # Answered in the call's last moments: after the listener saw the
                # caller as live, before this watch spoke it. The listener rings back.
                await asyncio.to_thread(store.queue_callback, question_id)
                logger.info("Reply landed as the call ended; queued callback question=%s", question_id)
        except Exception as exc:
            logger.warning("Question finish failed question=%s (%s: %s)",
                           question_id, type(exc).__name__, exc)
