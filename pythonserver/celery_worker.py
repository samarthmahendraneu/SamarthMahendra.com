# celery_worker.py

import os
import sys
import logging
from celery import Celery

# `celery -A celery_worker` puts the working directory on sys.path only while
# it imports this module, then removes it, so a task importing one of this
# folder's modules later (chat_agent) failed with ModuleNotFoundError. Add the
# folder even though it is on the path right now: Celery removes one copy
# when it is done, and this one stays for as long as the worker runs.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mongo_tool
import discord_tool
import asyncio

# Set up detailed logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(processName)s %(message)s',
)
logger = logging.getLogger(__name__)

logger.info("[Celery Worker] Starting celery_worker.py")

CELERY_BROKER_URL = os.getenv("REDIS_URL")

CELERY_BROKER_URL = os.getenv("REDIS_URL")
logger.info(f"[Celery Worker] Using broker URL: {CELERY_BROKER_URL}")

celery_app = Celery(
    "celery_worker",
    broker=CELERY_BROKER_URL,
    backend=CELERY_BROKER_URL  # optional, if you want to use Redis for result backend too
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
)
logger.info(f"[Celery Worker] Celery configuration: {celery_app.conf}")

from discord_tool import send_message_to_channel


@celery_app.task(bind=True)
def tool_call_fn(self, tool_name, call_id, args):
    logger.info(f"[Celery Worker] Received task: tool_call_fn with tool_name={tool_name}, call_id={call_id}, args={args}")
    try:
        if tool_name == "send_discord_message":
            logger.info("[Celery Worker] Relaying a caller message to Discord")
            # Send and disconnect. Unlike talk_to_samarth_discord this never
            # waits for a reply: the caller is on the phone while it runs.
            asyncio.run(send_message_to_channel(args["content"]))
            result = {"status": "sent"}
        elif tool_name == "talk_to_samarth_discord":
            logger.info("[Celery Worker] Calling discord_tool.ask_and_get_reply")
            result = discord_tool.ask_and_get_reply(args["message"]["content"])
        elif tool_name == "query_profile_info":
            logger.info("[Celery Worker] Calling mongo_tool.query_mongo_db_for_candidate_profile")
            result = mongo_tool.query_mongo_db_for_candidate_profile()
        elif tool_name == "send_meeting_email":
            logger.info("[Celery Worker] Sending meeting email to %s", args.get("email"))
            import smtplib
            from email.message import EmailMessage
            smtp_host = os.getenv("SMTP_HOST")
            smtp_port = int(os.getenv("SMTP_PORT", 587))
            smtp_user = os.getenv("SMTP_USER")
            smtp_pass = os.getenv("SMTP_PASS")
            sender = smtp_user or "no-reply@samarthmahendra.com"
            recipient = args.get("email")
            meeting_url = args.get("meeting_url")
            subject = "Your Meeting Link with Samarth"
            body = f"Hello,\n\nHere is your Jitsi meeting link: {meeting_url}\n\nSee you there!\n\nRegards,\nSamarth Mahendra"
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = recipient
            msg.set_content(body)
            try:
                with smtplib.SMTP(smtp_host, smtp_port) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.send_message(msg)
                logger.info(f"[Celery Worker] Email sent to {recipient}")
                result = {"status": "sent", "recipient": recipient}
            except Exception as e:
                logger.error(f"[Celery Worker] Failed to send email: {e}")
                result = {"status": "error", "error": str(e)}
        else:
            # Do not report success for work that never happened. This is what a
            # worker running older code than the service that queued it looks
            # like, and it is the failure that silently dropped Discord relays.
            logger.error("[Celery Worker] Unknown tool_name: %s - this worker may be "
                         "running older code than the service that queued it", tool_name)
            raise ValueError(f"Unknown tool_name: {tool_name}")

        logger.info(f"[Celery Worker] Task result: {result}")
        if call_id:
            mongo_tool.save_tool_message(call_id, tool_name, args, result)
        logger.info(f"[Celery Worker] Saved tool message for call_id={call_id}")
        return result
    except Exception as e:
        logger.error(f"[Celery Worker] Error in tool_call_fn: {e}", exc_info=True)
        raise


# ---- background jobs and chat news (jobs.py, job_handlers.py, chat_agent.py) ----
# tool_call_fn above stays for work queued by services not yet redeployed.

import redis as _redis

import job_handlers
from events import EventStream
from jobs import JobStore

_store = _redis.from_url(CELERY_BROKER_URL or "redis://localhost:6379/0",
                         socket_connect_timeout=5, socket_timeout=10)
events = EventStream(_store)
jobs = JobStore(_store, enqueue=lambda job_id: run_job.delay(job_id))


@celery_app.task(name="celery_worker.run_job")
def run_job(job_id):
    """Run a background job and tell the conversation that started it."""
    record = job_handlers.run(
        job_id, jobs, events,
        on_chat_news=lambda session_id: chat_followup.delay(session_id),
        notify_discord=lambda text: job_handlers.send_discord({"content": text}))
    return record and record["status"]


@celery_app.task(name="celery_worker.chat_followup", bind=True, max_retries=3)
def chat_followup(self, session_id):
    """News landed on a website chat's stream: have the assistant say so."""
    import chat_agent
    try:
        return chat_agent.agent().follow_up(session_id)
    except chat_agent.Busy as exc:
        # A long turn of the visitor's still holds the session; try again
        # once it has finished.
        raise self.retry(exc=exc, countdown=5)
