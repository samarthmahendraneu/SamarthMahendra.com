"""Places scheduled call backs as they fall due (see callbacks.py).

Runs inside the Discord listener, which is always up, where the voice service
may be asleep between calls. The voice service dials them, through the same
/start-calls the chat's calls use: it holds the Twilio credentials, and is
sent only a call back's id, never a number. Twilio reports how each call went
to the voice service's /callback-status, which books a retry when one was
missed.
"""

import asyncio
import logging

import requests

from job_handlers import TWILIO_SERVICE_URL, call_service_refusal, voice_headers

logger = logging.getLogger(__name__)
INTERVAL = 5
# Long enough for a voice service on a free plan to wake up.
PLACE_TIMEOUT = 90


class NotPlaced(Exception):
    pass


def place(record, post=requests.post, base_url=TWILIO_SERVICE_URL):
    """Have the voice service dial a claimed call back. Returns the call's sid.

    `numbers` goes empty so a voice service from before call backs went this
    way dials nobody, rather than the number it rings by default.
    """
    try:
        response = post(f"{base_url}/start-calls", json={"callback_id": record["id"], "numbers": []},
                        headers=voice_headers(), timeout=PLACE_TIMEOUT)
    except requests.RequestException as exc:
        raise NotPlaced(f"the call service couldn't be reached ({type(exc).__name__})") from None
    if response.status_code >= 300:
        raise NotPlaced(call_service_refusal(response.status_code))
    calls = response.json().get("calls") or []
    if not calls:
        raise NotPlaced("the call service placed no call; it may need redeploying")
    if not calls[0].get("sid"):
        raise NotPlaced(calls[0].get("error") or "the call service placed no call")
    return calls[0]["sid"]


def went_out(store, callback_id):
    """Whether the voice service dialled it, whatever became of its reply."""
    record = store.get(callback_id)
    return record is not None and record["state"] != "dialing"


async def tick(store, notify=None, placer=place):
    """Dial whatever is due now; a call that isn't placed counts as a miss."""
    try:
        due = await asyncio.to_thread(store.claim_due)
    except Exception as exc:
        logger.warning("Callback scheduler could not read due calls (%s: %s)",
                       type(exc).__name__, exc)
        return
    for record in due:
        try:
            sid = await asyncio.to_thread(placer, record)
            logger.info("Callback %s dialled call=%s attempt=%d", record["id"], sid, record["attempts"])
        except Exception as exc:
            reason = getattr(exc, "msg", None) or str(exc) or type(exc).__name__
            if await asyncio.to_thread(went_out, store, record["id"]):
                # Dialled; only the answer to the request was lost. Trying
                # again would ring them twice.
                logger.warning("Callback %s: no reply from the call service (%s), but it dialled",
                               record["id"], reason)
                continue
            logger.warning("Callback %s could not be placed (%s)", record["id"], reason)
            record, outcome = await asyncio.to_thread(store.dial_failed, record["id"])
            if notify and record:
                later = (f" Trying again {store.when_for_samarth(record)}."
                         if outcome == "retrying" else " I've stopped trying.")
                await notify(f"Couldn't place the call back to {record['name'] or 'the caller'} "
                             f"({record['to']}): {reason}.{later}")


async def run(store, notify=None, interval=INTERVAL, placer=place):
    while True:
        try:
            await tick(store, notify, placer)
        except Exception:
            # One bad record, or Discord being down for the notice, must not
            # stop every later call back.
            logger.exception("Callback scheduler tick failed")
        await asyncio.sleep(interval)
