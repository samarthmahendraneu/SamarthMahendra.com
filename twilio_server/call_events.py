"""Tell a caller about background work the moment it finishes.

Samarth's Discord replies and finished background jobs arrive as events on
the call's stream (events.py). For as long as the call lasts this reads the
stream, has the assistant say each one, and keeps the call's live key fresh so
the listener knows the caller is still on the line and must not be rung back.
When the call ends, a reply that arrived too late to be spoken is handed to
the listener as a call back, if the caller asked for one.
"""

import asyncio
import logging

from events import START

logger = logging.getLogger(__name__)
POLL_INTERVAL = 0.5

# Commentary accepts up to 500 tokens, and the bridge treats most rejections
# as fatal, so the parts that come from outside are capped well below that
# (roughly 4 characters a token, leaving room for the fixed wording).
MAX_QUESTION_CHARS = 240
MAX_REPLY_CHARS = 900
MAX_DETAIL_CHARS = 300


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


def followup_commentary(record, text):
    return (f'Samarth has added to his answer about "{clip(record["question"], MAX_QUESTION_CHARS)}": '
            f'{clip(text, MAX_REPLY_CHARS)}')


def job_commentary(job):
    label = clip(job.get("label") or "a background task", MAX_DETAIL_CHARS)
    if job.get("status") == "done":
        return f"This is now done: {label}."
    error = clip(job.get("error") or "no reason was given", MAX_DETAIL_CHARS)
    return (f"This did not work: {label}. The reason given was: {error}. "
            "The caller may be able to help put it right, for example by spelling an email address again.")


def commentary_for(kind, data, questions):
    """What the assistant should say about an event, and the question that
    saying it delivers, if any. (None, None) for events a caller needn't hear."""
    if kind in ("question.answered", "question.followup"):
        try:
            record = questions.get(data.get("question_id") or "")
        except ValueError:
            return None, None
        if record is None:
            return None, None
        if kind == "question.answered":
            return reply_commentary(record), record["id"]
        return followup_commentary(record, data.get("text", "")), None
    if kind in ("job.done", "job.failed"):
        return job_commentary(data.get("job") or {}), None
    return None, None


async def watch_call(bridge, channel, events, questions, interval=POLL_INTERVAL):
    """Run for the length of a call; the caller cancels it when the call ends."""
    cursor = START
    while not bridge.closing:
        try:
            await asyncio.to_thread(events.mark_live, channel)
            batch = await asyncio.to_thread(events.read, channel, cursor)
        except Exception as exc:
            # A Redis blip must not end the call; try again next tick.
            logger.warning("Call event read failed (%s: %s)", type(exc).__name__, exc)
            batch = []
        for event_id, kind, data in batch:
            try:
                said, question_id = await asyncio.to_thread(commentary_for, kind, data, questions)
            except Exception as exc:
                logger.warning("Call event lookup failed (%s: %s)", type(exc).__name__, exc)
                break
            if said is not None and not await bridge.say(said):
                break          # not started yet, or closing: this event again next tick
            cursor = event_id
            if question_id:
                try:
                    await asyncio.to_thread(questions.mark_delivered, question_id)
                except Exception as exc:
                    logger.warning("Could not mark reply delivered question=%s (%s)",
                                   question_id, type(exc).__name__)
                logger.info("Delivered Samarth's reply live question=%s", question_id)
            elif said is not None:
                logger.info("Told the caller about %s", kind)
        await asyncio.sleep(interval)


async def finish_call(channel, asked, events, questions):
    """At call end: stop counting as live, and hand unspoken replies to the listener."""
    try:
        await asyncio.to_thread(events.clear_live, channel)
    except Exception as exc:
        logger.warning("Could not clear live key channel=%s (%s)", channel, type(exc).__name__)
    for question_id in asked:
        try:
            record = await asyncio.to_thread(questions.get, question_id)
            if (record and record["status"] == "answered" and not record.get("delivered_live")
                    and record.get("callback_state") == "requested"):
                # Answered in the call's last moments: after the listener saw the
                # caller as live, before this watch spoke it. The listener rings back.
                await asyncio.to_thread(questions.queue_callback, question_id)
                logger.info("Reply landed as the call ended; queued callback question=%s", question_id)
        except Exception as exc:
            logger.warning("Question finish failed question=%s (%s: %s)",
                           question_id, type(exc).__name__, exc)
