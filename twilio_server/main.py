"""Twilio phone assistant using GPT-Live 1 with Responses delegation."""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlencode

import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.websockets import WebSocketDisconnect
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

load_dotenv()

import mongo_tool
import worker_client
from call_events import finish_call, watch_call
from callbacks import MISSED_STATUSES, CallbackStore
from events import EventStream, channel_id, valid_channel
from jobs import JobStore
import timezones
from live_bridge import LiveBridge
from question_store import QuestionStore
import redis_pool
from live_config import LIVE_URL, TOOLS, VOICEMAIL_TOOLS, LiveSettings, greeting, session_config

logger = logging.getLogger(__name__)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SETTINGS = LiveSettings.from_env(os.environ)
PORT = int(os.getenv("PORT", "5050"))
# The account's own number is US, so a bare 10-digit number is taken as US.
DEFAULT_COUNTRY_CODE = "1"


def to_e164(raw):
    """Normalise a typed phone number to E.164, or None if it can't be.

    Twilio rejects anything else with error 13223. Numbers reach /start-calls
    as the chatbot's model transcribed them, e.g. "857-707-1671".
    """
    if not isinstance(raw, str):
        return None
    number = re.sub(r"[\s().\-]", "", raw)
    if not number.startswith("+"):
        if len(number) == 10:
            number = "+" + DEFAULT_COUNTRY_CODE + number
        elif len(number) == 11 and number.startswith(DEFAULT_COUNTRY_CODE):
            number = "+" + number
    return number if re.fullmatch(r"\+[1-9]\d{7,14}", number) else None


_from_number = os.getenv("TWILIO_FROM_NUMBER", "+18339703274")
TWILIO_FROM_NUMBER = to_e164(_from_number) or _from_number
if TWILIO_FROM_NUMBER != _from_number or not to_e164(_from_number):
    logger.warning("TWILIO_FROM_NUMBER %r is not E.164; using %r",
                   _from_number, TWILIO_FROM_NUMBER)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://twillio-ai-assistant.onrender.com").rstrip("/")

if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY. Set it in the server environment.")

twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
app = FastAPI()


class CallContextStore:
    """Short-lived, single-use context tokens; no shared caller/name/script keys."""

    def __init__(self):
        self.redis = redis_pool.connect(max_connections=4)

    def put(self, context):
        token = uuid.uuid4().hex
        self.redis.setex("live:call:" + token, 300, json.dumps(context))
        return token

    def take(self, token):
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise ValueError("Missing or invalid call context")
        # Atomic consume, also compatible with Redis versions before GETDEL.
        value = self.redis.eval(
            "local v = redis.call('GET', KEYS[1]); redis.call('DEL', KEYS[1]); return v",
            1, "live:call:" + token,
        )
        if value is None:
            raise ValueError("Call context expired or already consumed")
        return json.loads(value)


contexts = CallContextStore()
questions = QuestionStore(contexts.redis)
events = EventStream(contexts.redis)
# Looked up on each call, not bound here, so tests can swap the client.
jobs = JobStore(contexts.redis, enqueue=lambda job_id: worker_client.enqueue_job(job_id))
callbacks = CallbackStore.from_env(contexts.redis, os.environ)
SAMARTH_EMAIL = "samarth.mahendragowda@gmail.com"
# Spoken to an answering machine on a scheduled call back.
VOICEMAIL_VOICE = "Polly.Joanna-Neural"


def generate_jitsi_meeting_url(user_name="samarth"):
    return f"https://meet.jit.si/{user_name}-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"


def start_job(kind, args, channel=None, announce="failure", label=""):
    """Start a background job (jobs.py); the task id, or None if it couldn't be queued."""
    try:
        return jobs.start(kind, args, origin=channel, announce=announce, label=label)
    except Exception as exc:
        logger.warning("Could not queue %s job (%s: %s)", kind, type(exc).__name__, exc)
        return None


def schedule_meeting(args, moment, channel=None):
    """Book a meeting at `moment`, an aware datetime in the caller's zone."""
    meeting_url = generate_jitsi_meeting_url()
    meeting_id = mongo_tool.insert_meeting(args["name"], args["agenda"], moment.isoformat(), meeting_url)
    when = f"{timezones.readable(moment)} ({timezones.offset_text(moment)})"
    for_samarth = timezones.also_in(moment, timezones.SAMARTH_ZONE)
    invite = start_job("email.send", {
        "to": args["user_email"], "subject": "Your meeting with Samarth Mahendra",
        "body": (f"Hello {args['name']},\n\nYour meeting with Samarth Mahendra is booked.\n\n"
                 f"What: {args['agenda']}\nWhen: {when}\nJoin: {meeting_url}\n\n"
                 "See you there!\n\nLuma, Samarth's AI assistant"),
    }, channel, announce="always", label=f"emailing the meeting invite to {args['user_email']}")
    # Samarth's own copies, in his time too: bookkeeping the caller needn't hear about.
    copies = [
        start_job("email.send", {
            "to": SAMARTH_EMAIL, "subject": f"Meeting booked with {args['name']}",
            "body": (f"{args['name']} ({args['user_email']}) booked a meeting by phone.\n\n"
                     f"What: {args['agenda']}\nWhen: {for_samarth}, {moment:%B} {moment.day}\n"
                     f"Join: {meeting_url}"),
        }, announce="never", label="emailing Samarth his copy of the meeting"),
        start_job("discord.send", {"content": (
            f"Meeting scheduled with {args['name']}, {args['user_email']} on {for_samarth}, "
            f"{moment:%B} {moment.day} for {args['agenda']}. Meeting link: {meeting_url}")},
            announce="never", label="telling Samarth about the meeting on Discord"),
    ]
    if invite is None or None in copies:
        # Saving succeeded: don't tell the model to retry and create a duplicate.
        logger.warning("Meeting saved but notification enqueue was incomplete")
        return {"status": "saved", "meeting_id": meeting_id, "meeting_url": meeting_url,
                "notifications": "incomplete; delivery must be checked"}
    return {"status": "saved", "meeting_id": meeting_id, "meeting_url": meeting_url,
            "when": timezones.readable(moment), "notifications": "queued", "task_id": invite,
            "message": "The invite email is on its way; you will be told once it has been sent."}


def queue_discord_message(content, channel=None):
    """Post to Samarth's Discord in the background; False if it can't be queued.

    Only the enqueue is guarded. Building the message stays outside the try so
    a bug there fails loudly instead of being reported as a broker problem. A
    failed post is announced on `channel`, so the caller hears it didn't go.
    """
    return start_job("discord.send", {"content": content}, channel, announce="failure",
                     label="passing the message on to Samarth on Discord") is not None


def relay_status(message_id, relayed):
    # Saving already succeeded: don't tell the model to retry and send it twice.
    return {"status": "saved", "message_id": message_id,
            "relay": "queued" if relayed else "incomplete; delivery must be checked"}


def relay_message_to_samarth(call_id, args, channel=None):
    message_id = mongo_tool.save_relayed_message(call_id, args)
    content = f"Phone message from {args['caller_name'] or 'a caller'}: {args['message']}"
    return relay_status(message_id, queue_discord_message(content, channel))


def relay_caller_response(context, response):
    """Tell Samarth what a caller answered, alongside what they were asked."""
    who = context.get("name") or "A caller"
    asked = context.get("message")
    about = f' to "{asked}"' if asked else ""
    return queue_discord_message(f"Reply from {who}{about}: {response}", context.get("channel"))


def report_to_origin(context, kind, **data):
    """Tell the conversation that started an outbound call how it went.

    A chat that asked for the call hears the answer in the chat; best effort,
    since the answer has already been saved and relayed to Samarth.
    """
    origin = context.get("origin")
    if not valid_channel(origin):
        return
    try:
        events.publish(origin, kind, name=context.get("name", ""),
                       message=context.get("message", ""), call_sid=context.get("call_sid", ""), **data)
        if origin.startswith("chat:"):
            worker_client.enqueue_chat_followup(channel_id(origin))
    except Exception as exc:
        logger.warning("Could not report %s to %s (%s: %s)", kind, origin, type(exc).__name__, exc)


def describe_reply(question_id, channel=None):
    """Report only what the store actually holds; never guess Samarth's answer.
    Another conversation's question is treated as unknown."""
    record = questions.get(question_id)
    if record is None or (record.get("origin") and record["origin"] != channel):
        return {"status": "unknown", "message": "That question is no longer tracked."}
    waited = round(questions.waiting_for(record))
    if record["status"] == "answered":
        # The caller hears it now; the call's end mustn't ring them back with it.
        questions.mark_delivered(question_id)
        return {"status": "answered", "reply": record["reply"], "waited_seconds": waited}
    return {"status": "waiting", "waited_seconds": waited,
            "message": ("Samarth has not answered yet. Offer a call back if this has "
                        "been going on for more than about fifteen seconds.")}


def describe_task(task_id, channel=None):
    try:
        record = jobs.get(task_id)
    except ValueError:
        record = None
    if record is None or record.get("origin") != channel:
        return {"status": "unknown", "message": "That task is not recognised or no longer tracked."}
    return jobs.describe(record)


def book_callback(args, channel=None):
    """schedule_callback: a call at the caller's chosen time, on their clock."""
    zone_name, source = callbacks.callee_zone(args["phone_number"], args["timezone"])
    if source == "default":
        raise ValueError("Their timezone isn't known; ask the caller which one they are in")
    moment = timezones.resolve(args["when"], zone_name)
    name, reason = args["caller_name"], args["reason"] or "their earlier call"
    record = callbacks.schedule(
        args["phone_number"], name,
        purpose=f"They asked to be called back at this time about: {reason}",
        voicemail=(f"{greeting_to(name)} this is Luma, Samarth Mahendra's AI assistant, calling you "
                   f"back as you asked, about {reason}. Please call this number back when it suits "
                   "you. Goodbye."),
        when=moment.timestamp(), source="request", origin=channel, tz=zone_name)
    queue_discord_message(f"Luma will call {name or 'a caller'} ({args['phone_number']}) back "
                          f"on {callbacks.when_for_samarth(record)}, {moment:%B} {moment.day} "
                          f"about: {reason}")
    booked = {"status": "scheduled", "callback_id": record["id"], "when": timezones.readable(moment),
              "timezone": zone_name, "message": "Booked. If they miss it, it is tried again later."}
    if source == "number":
        booked["note"] = f"{zone_name} is the timezone of their phone number; confirm it with them."
    return booked


def current_time(zone_name, context):
    """get_current_time: the time in a zone, by default the caller's best-known one."""
    return timezones.now_in(zone_name or context.get("caller_timezone") or "")


def greeting_to(name):
    return f"Hi {name}," if name else "Hi,"


# A caller needn't give their name, or a number for a voicemail, a call back
# can be about nothing in particular, and a timezone can often be worked out
# from their number; every other argument is needed.
MAY_BE_EMPTY = {"caller_name", "phone_no", "reason", "timezone"}


def make_tool_executor(context, voicemail=False, asked=None):
    """asked, when given, collects the ids of questions this call puts to
    Samarth, so the call can speak his reply the moment it lands. The call's
    event channel is context["channel"]: background results are told there."""
    schemas = {tool["name"]: tool["parameters"] for tool in (VOICEMAIL_TOOLS if voicemail else TOOLS)}
    channel = context.get("channel")

    async def execute(name, call_id, args):
        schema = schemas.get(name)
        if schema is None:
            raise ValueError("Tool is not available for this call")
        missing = [field for field in schema["required"] if not isinstance(args.get(field), str)
                   or (not args[field].strip() and field not in MAY_BE_EMPTY)]
        if missing or set(args) - set(schema["required"]):
            # Say which: a bare "invalid arguments" left the model unable to
            # fix its call, and nothing has happened yet, so retrying is safe.
            logger.warning("Tool refused name=%s missing=%s", name, ",".join(missing) or "-")
            return {"status": "invalid", "message": (
                f"Nothing was done: {', '.join(missing) or 'unexpected fields'} missing or empty. "
                "Ask the caller for it, then call the tool again.")}
        args = {key: value.strip() for key, value in args.items()}
        if name == "end_call":
            return {"status": "ending"}
        if name == "get_current_time":
            try:
                return current_time(args["timezone"], context)
            except ValueError as exc:
                return {"status": "invalid", "message": str(exc)}
        if name == "schedule_meeting_on_jitsi":
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args["user_email"]):
                return {"status": "invalid", "message": "That email address doesn't look right; check it."}
            if not args["timezone"]:
                return {"status": "invalid", "message": "Ask the caller which timezone the time is in."}
            try:
                moment = timezones.resolve(args["timing"], args["timezone"])
            except ValueError as exc:
                return {"status": "invalid", "message": str(exc)}
            return await asyncio.to_thread(schedule_meeting, args, moment, channel)
        if name == "ask_samarth":
            question_id = await asyncio.to_thread(
                questions.ask, args["question"], args["caller_name"], channel)
            if asked is not None:
                asked.append(question_id)
            logger.info("Asked Samarth question=%s", question_id)
            return {"status": "asked", "question_id": question_id,
                    "message": ("Posted to Samarth. You will be told as soon as he replies; "
                                "keep the conversation going meanwhile.")}
        if name == "check_samarth_reply":
            return await asyncio.to_thread(describe_reply, args["question_id"], channel)
        if name == "check_task":
            return await asyncio.to_thread(describe_task, args["task_id"], channel)
        if name == "request_callback":
            try:
                callbacks.check_number(args["phone_number"])
                zone_name = timezones.zone(args["timezone"]).key if args["timezone"] else None
            except ValueError as exc:
                return {"status": "refused", "message": str(exc)}
            try:
                record = await asyncio.to_thread(questions.get, args["question_id"])
            except ValueError:
                record = None
            if record is None or (record.get("origin") and record["origin"] != channel):
                return {"status": "unknown", "message": "That question is no longer tracked."}
            # For every question this call asks, not just this one.
            await asyncio.to_thread(questions.request_callback, args["question_id"],
                                    args["caller_name"], args["phone_number"], zone_name)
            if record["status"] == "answered":
                # Already answered: say it now rather than promising a call.
                await asyncio.to_thread(questions.mark_delivered, args["question_id"])
                return {"status": "already_answered", "reply": record["reply"]}
            logger.info("Callback requested question=%s", args["question_id"])
            return {"status": "callback_requested", "message": (
                "They will be called back with Samarth's answer to anything this call asked him, "
                "unless they are still on the line when he replies.")}
        if name == "schedule_callback":
            try:
                return await asyncio.to_thread(book_callback, args, channel)
            except ValueError as exc:
                return {"status": "refused", "message": str(exc)}
        if name == "send_messages_to_samarth":
            return await asyncio.to_thread(relay_message_to_samarth, call_id, args, channel)
        if name == "save_reponse_from_caller":
            message_id = await asyncio.to_thread(
                mongo_tool.mongo_save_message, context.get("name", ""),
                context.get("message", ""), args["response"],
            )
            # Not relay_message_to_samarth: that expects caller_name/message
            # and would store a second, empty copy of this response.
            relayed = await asyncio.to_thread(relay_caller_response, context, args["response"])
            await asyncio.to_thread(report_to_origin, context, "call.response",
                                    response=args["response"])
            return relay_status(message_id, relayed)
        else:
            # mongo_tool expects (call_id, args), not three positional strings.
            message_id = await asyncio.to_thread(mongo_tool.save_voice_mail_message, call_id, args)
        return {"status": "saved", "message_id": message_id}

    return execute


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!", "model": SETTINGS.model}


async def call_twiml(request, voicemail=False):
    context = {
        "script": request.query_params.get("script", "1"),
        "name": request.query_params.get("name", ""),
        "message": request.query_params.get("message", ""),
    }
    # An outbound call a chat asked for reports its answer back there.
    origin = request.query_params.get("origin", "")
    if valid_channel(origin):
        context["origin"] = origin
    # Who is on the other end, and so which clock they are probably on.
    fields = await twilio_fields(request)
    outbound = fields.get("Direction", "").startswith("outbound")
    add_caller(context, fields.get("To") if outbound else fields.get("From"))
    return await stream_twiml(request, context, voicemail)


def add_caller(context, raw_number):
    number = to_e164(raw_number or "")
    if number:
        context["caller_number"] = number
        zone_name = timezones.zone_for_number(number)
        if zone_name:
            context["caller_timezone"] = zone_name


async def stream_twiml(request, context, voicemail=False):
    token = await asyncio.to_thread(contexts.put, context)
    response = VoiceResponse()
    connect = Connect()
    endpoint = "media-stream-voicemail" if voicemail else "media-stream"
    stream = connect.stream(url=f"wss://{request.url.netloc}/{endpoint}")
    # Twilio Stream URLs cannot carry query parameters. Keep long call context
    # in Redis and send only a small opaque token in start.customParameters.
    stream.parameter(name="context_id", value=token)
    response.append(connect)
    response.hangup()
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    return await call_twiml(request)


@app.api_route("/voice-mail", methods=["GET", "POST"])
async def handle_incoming_call_voicemail(request: Request):
    return await call_twiml(request, voicemail=True)


async def wait_for_stream(websocket):
    async for raw in websocket.iter_text():
        event = json.loads(raw)
        if event.get("event") == "start":
            start = event["start"]
            audio_format = start.get("mediaFormat", {})
            if audio_format != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}:
                raise ValueError("Expected Twilio mono 8 kHz mu-law audio")
            return start
        if event.get("event") == "stop":
            break
    raise ValueError("Twilio stream ended before start")


async def handle_stream(websocket, voicemail=False):
    await websocket.accept()
    # Record how far setup got: a failure before the bridge starts looks
    # identical in the logs otherwise, and each stage has a different cause.
    stage, stream_sid, started = "await_twilio_start", "unknown", time.monotonic()
    try:
        start = await asyncio.wait_for(wait_for_stream(websocket), timeout=10)
        stream_sid = start.get("streamSid", "unknown")
        logger.info("Call started stream=%s call=%s voicemail=%s", stream_sid,
                    start.get("callSid"), voicemail)
        stage = "call_context"
        token = start.get("customParameters", {}).get("context_id", "")
        context = await asyncio.to_thread(contexts.take, token)
        # The model sees only what the call is about; the rest is plumbing.
        prompt_context = {key: context[key] for key in
                          ("script", "name", "message", "caller_number", "caller_timezone")
                          if key in context}
        call_sid = start.get("callSid") or stream_sid
        channel = "call:" + call_sid if valid_channel("call:" + call_sid) else None
        call = dict(context, call_sid=call_sid, channel=channel)
        stage = "live_connect"
        async with websockets.connect(
            LIVE_URL, extra_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            open_timeout=10, close_timeout=5, max_size=2**22,
            # mu-law barely compresses; deflating 160-byte frames 50x a second
            # in each direction costs CPU and a per-message flush for nothing.
            compression=None,
        ) as live:
            logger.info("Live socket open stream=%s in=%.2fs", stream_sid,
                        time.monotonic() - started)
            stage = "bridge"
            asked = []
            bridge = LiveBridge(
                websocket, live, start["streamSid"], session_config(SETTINGS, prompt_context, voicemail),
                greeting(prompt_context, voicemail), make_tool_executor(call, voicemail, asked),
            )
            watch = asyncio.create_task(watch_call(bridge, channel, events, questions)) if channel else None
            try:
                await bridge.run()
            finally:
                if watch:
                    watch.cancel()
                    await asyncio.gather(watch, return_exceptions=True)
                    await finish_call(channel, asked, events, questions)
        stage = "done"
    except WebSocketDisconnect:
        logger.info("Twilio disconnected stream=%s stage=%s after=%.1fs",
                    stream_sid, stage, time.monotonic() - started)
    except TimeoutError:
        logger.error("Call setup timed out stream=%s stage=%s after=%.1fs",
                     stream_sid, stage, time.monotonic() - started)
    except ValueError as exc:
        # Expected and self-explanatory (expired/replayed context token, wrong
        # audio format); a stack trace here is noise, the reason is not.
        logger.warning("Call rejected stream=%s stage=%s reason=%s",
                       stream_sid, stage, exc)
    except Exception as exc:
        logger.error("Call bridge failed stream=%s stage=%s after=%.1fs (%s: %s)",
                     stream_sid, stage, time.monotonic() - started,
                     type(exc).__name__, exc, exc_info=True)
    finally:
        logger.info("Call closed stream=%s stage=%s duration=%.1fs",
                    stream_sid, stage, time.monotonic() - started)
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            pass


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    await handle_stream(websocket)


@app.websocket("/media-stream-voicemail")
async def handle_media_stream_voicemail(websocket: WebSocket):
    await handle_stream(websocket, voicemail=True)


@app.post("/start-calls")
async def start_calls(request: Request):
    body = await request.json()
    numbers = body.get("numbers", ["+18577071671"])
    if isinstance(numbers, str):
        # One number sent as a string would otherwise be dialled per character.
        numbers = [numbers]
    params = {"script": "2", "name": body.get("name", ""), "message": body.get("message", "")}
    # A chat that asks for calls names itself so each answer is told back there.
    origin = body.get("origin")
    status_callback = {}
    if valid_channel(origin):
        params["origin"] = origin
        status_callback = {
            "status_callback": f"{PUBLIC_BASE_URL}/call-status?"
                               + urlencode({"origin": origin, "name": params["name"]}),
            "status_callback_event": ["completed"], "status_callback_method": "POST",
        }
    url = f"{PUBLIC_BASE_URL}/incoming-call?{urlencode(params)}"
    results = []
    for index, raw in enumerate(numbers):
        number = to_e164(raw)
        if number is None:
            logger.warning("Outbound call skipped: %r is not a phone number Twilio can dial", raw)
            results.append({"to": raw, "error": (
                "Not a valid phone number. Include the country code, e.g. +16175550123.")})
            continue
        try:
            call = await asyncio.to_thread(twilio_client.calls.create, to=number,
                                           from_=TWILIO_FROM_NUMBER, url=url, **status_callback)
            results.append({"to": number, "sid": call.sid})
            if index < len(numbers) - 1:
                await asyncio.sleep(15)
        except Exception as exc:
            # Twilio's own code and message say what's wrong; pass them on.
            code, msg = getattr(exc, "code", None), getattr(exc, "msg", None) or str(exc)
            logger.warning("Outbound call failed to=%s code=%s (%s)", number, code, msg)
            results.append({"to": number, "error": f"Call could not be started: {msg}",
                            "twilio_code": code})
    return {"status": "done", "calls": results}


async def twilio_fields(request):
    """A Twilio webhook's parameters: its form body, then the query string.

    Parsed by hand so the service needs no multipart library for them.
    """
    fields = {key: values[0] for key, values in
              parse_qs((await request.body()).decode("utf-8", "replace")).items()}
    return {**dict(request.query_params), **fields}


@app.post("/call-status")
async def outbound_call_status(request: Request):
    """How an outbound call a chat asked for ended, when nobody answered it."""
    fields = await twilio_fields(request)
    if fields.get("CallStatus") in MISSED_STATUSES:
        context = {"origin": fields.get("origin", ""), "name": fields.get("name", ""),
                   "call_sid": fields.get("CallSid", "")}
        await asyncio.to_thread(report_to_origin, context, "call.status",
                                status=fields["CallStatus"])
    return Response(status_code=204)


# ---- scheduled call backs (callbacks.py; placed by the worker's scheduler) ----

def callback_notice(record, outcome):
    who = f"{record['name'] or 'the caller'} ({record['to']})"
    if outcome == "answered":
        return f"Called {who} back; they picked up."
    if outcome == "voicemail":
        return f"Called {who} back and left a voicemail."
    if outcome == "retrying":
        return f"Couldn't reach {who}. Trying again {callbacks.when_for_samarth(record)}."
    return f"Couldn't reach {who} after {record['attempts']} tries, so I've stopped trying."


@app.api_route("/callback-call", methods=["GET", "POST"])
async def callback_call(request: Request):
    """TwiML for a scheduled call back, once Twilio knows who answered."""
    fields = await twilio_fields(request)
    callback_id = fields.get("cb", "")
    try:
        record = await asyncio.to_thread(callbacks.get, callback_id)
    except ValueError:
        record = None
    response = VoiceResponse()
    if record is None or record["state"] not in ("dialing", "ringing"):
        response.hangup()
        return HTMLResponse(content=str(response), media_type="application/xml")
    answered_by = fields.get("AnsweredBy", "")
    if answered_by.startswith("machine") or answered_by == "fax":
        # Machine detection waited for the greeting to end, so this lands
        # after the beep.
        await asyncio.to_thread(callbacks.note_answered_by, callback_id, answered_by)
        if answered_by != "fax":
            response.say(record["voicemail"], voice=VOICEMAIL_VOICE)
        response.hangup()
        return HTMLResponse(content=str(response), media_type="application/xml")
    context = {"script": "3", "name": record["name"], "message": record["purpose"],
               "callback_id": callback_id, "caller_number": record["to"]}
    if record.get("timezone"):
        context["caller_timezone"] = record["timezone"]
    return await stream_twiml(request, context)


@app.post("/callback-status")
async def callback_status(request: Request):
    """Twilio's report on how a call back ended; a missed one is retried."""
    fields = await twilio_fields(request)
    try:
        record, outcome = await asyncio.to_thread(
            callbacks.finished, fields.get("cb", ""), fields.get("CallSid", ""),
            fields.get("CallStatus", ""), fields.get("AnsweredBy"))
    except ValueError:
        return Response(status_code=204)
    if outcome:
        logger.info("Callback %s outcome=%s", record["id"], outcome)
        queue_discord_message(callback_notice(record, outcome))
    return Response(status_code=204)


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
