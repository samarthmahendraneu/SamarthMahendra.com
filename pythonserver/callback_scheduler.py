"""Places scheduled call backs as they fall due (see callbacks.py).

Runs inside the Discord listener, which is always up, where the voice service
may be asleep between calls. Twilio reports how each call went to the voice
service's /callback-status, which books a retry when one was missed.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)
INTERVAL = 5


def place(record, twilio_client, from_number, base_url):
    return twilio_client.calls.create(
        to=record["to"], from_=from_number,
        url=f"{base_url}/callback-call?cb={record['id']}",
        status_callback=f"{base_url}/callback-status?cb={record['id']}",
        status_callback_event=["completed"], status_callback_method="POST",
        # Waits out a voicemail greeting, so a message left lands after the beep.
        machine_detection="DetectMessageEnd",
    )


async def tick(store, twilio_client, from_number, base_url, notify=None):
    """Dial whatever is due now; a call Twilio won't place counts as a miss."""
    try:
        due = await asyncio.to_thread(store.claim_due)
    except Exception as exc:
        logger.warning("Callback scheduler could not read due calls (%s: %s)",
                       type(exc).__name__, exc)
        return
    for record in due:
        try:
            call = await asyncio.to_thread(place, record, twilio_client, from_number, base_url)
            await asyncio.to_thread(store.dialed, record["id"], call.sid)
            logger.info("Callback %s dialled call=%s attempt=%d",
                        record["id"], call.sid, record["attempts"])
        except Exception as exc:
            reason = getattr(exc, "msg", None) or str(exc) or type(exc).__name__
            logger.warning("Callback %s could not be placed (%s)", record["id"], reason)
            record, outcome = await asyncio.to_thread(store.dial_failed, record["id"])
            if notify and record:
                later = (f" Trying again {store.when_text(record['due_at'])}."
                         if outcome == "retrying" else " I've stopped trying.")
                await notify(f"Couldn't place the call back to {record['name'] or 'the caller'} "
                             f"({record['to']}): {reason}.{later}")


async def run(store, twilio_client, from_number, base_url, notify=None, interval=INTERVAL):
    while True:
        try:
            await tick(store, twilio_client, from_number, base_url, notify)
        except Exception:
            # One bad record, or Discord being down for the notice, must not
            # stop every later call back.
            logger.exception("Callback scheduler tick failed")
        await asyncio.sleep(interval)
