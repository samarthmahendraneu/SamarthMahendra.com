"""What the worker does for each kind of background job (see jobs.py).

run() is a job's whole life on the worker: claim it, do it, keep the outcome,
and tell the conversation that started it. Handlers take the job's args and
return a small dict, or raise JobError when the work didn't happen; that
error's message is what the person waiting hears, so it is written for them.
"""

import logging
import os
import smtplib
import time
from email.message import EmailMessage

import requests

from events import channel_id, channel_kind
from jobs import JobStore

logger = logging.getLogger(__name__)
DISCORD_API = "https://discord.com/api/v10"
DISCORD_LIMIT = 2000
TWILIO_SERVICE_URL = os.getenv("TWILIO_SERVICE_URL", "https://twillio-ai-assistant.onrender.com").rstrip("/")


class JobError(Exception):
    """The work didn't happen; the message says why, for the person waiting."""


def send_discord(args, env=os.environ, post=requests.post):
    """Post to Samarth's channel through Discord's REST API.

    Unlike discord_tool.send_message_to_channel there is no gateway login per
    message: a post takes a fraction of a second, and a failure is an HTTP
    status instead of a message that silently never appears.
    """
    token, channel = env.get("DISCORD_TOKEN"), env.get("DISCORD_CHANNEL_ID")
    if not token or not channel:
        raise JobError("Discord isn't set up on the worker")
    content = str(args["content"])
    if len(content) > DISCORD_LIMIT:
        content = content[:DISCORD_LIMIT - 1] + "…"
    request = dict(
        # Caller-supplied text must not be able to ping @everyone.
        json={"content": content, "allowed_mentions": {"parse": []}},
        headers={"Authorization": f"Bot {token}"}, timeout=10)
    url = f"{DISCORD_API}/channels/{channel}/messages"
    response = post(url, **request)
    if response.status_code == 429:
        try:
            wait = float(response.json().get("retry_after", 1))
        except ValueError:
            wait = 1
        time.sleep(min(wait, 5))
        response = post(url, **request)
    if response.status_code >= 300:
        raise JobError(f"Discord refused the message (HTTP {response.status_code})")
    return {"status": "sent", "message_id": response.json().get("id")}


def send_email(args, env=os.environ, smtp=smtplib.SMTP):
    host, user, password = env.get("SMTP_HOST"), env.get("SMTP_USER"), env.get("SMTP_PASS")
    if not host:
        raise JobError("email isn't set up on the worker")
    message = EmailMessage()
    message["Subject"] = args["subject"]
    message["From"] = user or "no-reply@samarthmahendra.com"
    message["To"] = args["to"]
    message.set_content(args["body"])
    try:
        with smtp(host, int(env.get("SMTP_PORT", 587)), timeout=20) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.send_message(message)
    except smtplib.SMTPRecipientsRefused:
        raise JobError("the email address was rejected") from None
    except (smtplib.SMTPException, OSError) as exc:
        raise JobError(f"the mail server couldn't send it ({type(exc).__name__})") from None
    return {"status": "sent", "to": args["to"]}


def place_calls(args, post=requests.post, base_url=TWILIO_SERVICE_URL):
    """Have the voice service dial; it spaces the calls out, so allow time."""
    numbers = list(args["numbers"])
    try:
        response = post(f"{base_url}/start-calls", json={
            "numbers": numbers, "name": args.get("name", ""), "message": args.get("message", ""),
            "origin": args.get("origin"),
        }, timeout=30 + 20 * len(numbers))
    except requests.RequestException as exc:
        raise JobError(f"the call service couldn't be reached ({type(exc).__name__})") from None
    if response.status_code >= 300:
        raise JobError(f"the call service refused (HTTP {response.status_code})")
    calls = response.json().get("calls", [])
    placed = [{"to": call.get("to"), "sid": call["sid"]} for call in calls if call.get("sid")]
    failed = [{"to": call.get("to"), "error": call.get("error")} for call in calls if not call.get("sid")]
    if not placed:
        raise JobError("; ".join(f"{c['to']}: {c['error']}" for c in failed) or "no calls were placed")
    return {"status": "placed", "placed": placed, "failed": failed}


HANDLERS = {"discord.send": send_discord, "email.send": send_email, "calls.place": place_calls}


def run(job_id, jobs, events, on_chat_news=None, notify_discord=None, handlers=None):
    """Run one job end to end. Running it again does nothing."""
    handlers = HANDLERS if handlers is None else handlers
    record = jobs.claim(job_id)
    if record is None:
        return None
    handler = handlers.get(record["kind"])
    try:
        if handler is None:
            raise JobError(f"this worker doesn't know how to do {record['kind']}")
        record = jobs.finish(job_id, handler(record["args"]))
    except JobError as exc:
        record = jobs.fail(job_id, str(exc))
    except Exception as exc:
        logger.exception("Job %s kind=%s crashed", job_id, record["kind"])
        record = jobs.fail(job_id, f"it hit an unexpected error ({type(exc).__name__})")
    logger.info("Job %s kind=%s status=%s", job_id, record["kind"], record["status"])
    announce(record, events, on_chat_news, notify_discord)
    return record


def announce(record, events, on_chat_news=None, notify_discord=None):
    """Tell the conversation that started a job how it went, as its policy says."""
    if not JobStore.should_announce(record):
        return
    origin = record["origin"]
    try:
        events.publish(origin, "job.done" if record["status"] == "done" else "job.failed",
                       job=JobStore.summary(record))
    except Exception as exc:
        logger.warning("Could not announce job %s (%s: %s)", record["id"], type(exc).__name__, exc)
        return
    if channel_kind(origin) == "chat":
        if on_chat_news:
            on_chat_news(channel_id(origin))
        return
    # A call that has ended can't hear a failure; tell Samarth instead. Not
    # for a failed Discord post, which would only fail again.
    if (record["status"] == "failed" and notify_discord and record["kind"] != "discord.send"
            and not events.is_live(origin)):
        try:
            notify_discord(f"After a call ended, this didn't work: "
                           f"{record.get('label') or record['kind']}. {record.get('error') or ''}")
        except Exception as exc:
            logger.warning("Could not tell Samarth about job %s (%s)", record["id"], type(exc).__name__)
