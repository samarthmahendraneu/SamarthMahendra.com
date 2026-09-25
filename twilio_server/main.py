"""Twilio phone assistant using GPT-Live 1 with Responses delegation."""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from urllib.parse import urlencode

import redis
import websockets
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.rest import Client
from twilio.twiml.voice_response import Connect, VoiceResponse

load_dotenv()

from celery_worker import tool_call_fn
import mongo_tool
from live_bridge import LiveBridge
from live_config import LIVE_URL, TOOLS, VOICEMAIL_TOOLS, LiveSettings, greeting, session_config

logger = logging.getLogger(__name__)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SETTINGS = LiveSettings.from_env(os.environ)
PORT = int(os.getenv("PORT", "5050"))
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "+18339703274")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "https://twillio-ai-assistant.onrender.com").rstrip("/")

if not OPENAI_API_KEY:
    raise ValueError("Missing OPENAI_API_KEY. Set it in the server environment.")

twilio_client = Client(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
app = FastAPI()


class CallContextStore:
    """Short-lived, single-use context tokens; no shared caller/name/script keys."""

    def __init__(self):
        self.redis = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                    socket_connect_timeout=5, socket_timeout=5)

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


def generate_jitsi_meeting_url(user_name="samarth"):
    return f"https://meet.jit.si/{user_name}-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"


def schedule_meeting(args):
    meeting_url = generate_jitsi_meeting_url()
    meeting_id = mongo_tool.insert_meeting(args["name"], args["agenda"], args["timing"], meeting_url)
    try:
        for email in (args["user_email"], "samarth.mahendragowda@gmail.com"):
            tool_call_fn.delay("send_meeting_email", None, {"email": email, "meeting_url": meeting_url})
        tool_call_fn.delay("talk_to_samarth_discord", None, {
            "action": "send", "message": {"content": (
                f"Meeting scheduled with {args['name']}, {args['user_email']} on "
                f"{args['timing']} for {args['agenda']}. Meeting link: {meeting_url}"
            )},
        })
    except Exception:
        # Saving succeeded: don't tell the model to retry and create a duplicate.
        logger.warning("Meeting saved but notification enqueue was incomplete")
        return {"status": "saved", "meeting_id": meeting_id, "meeting_url": meeting_url,
                "notifications": "incomplete; delivery must be checked"}
    return {"status": "saved", "meeting_id": meeting_id, "meeting_url": meeting_url,
            "notifications": "queued"}


def make_tool_executor(context, voicemail=False):
    schemas = {tool["name"]: tool["parameters"] for tool in (VOICEMAIL_TOOLS if voicemail else TOOLS)}

    async def execute(name, call_id, args):
        schema = schemas.get(name)
        if schema is None:
            raise ValueError("Tool is not available for this call")
        if set(args) != set(schema["required"]) or any(
            not isinstance(value, str) or not value.strip() for value in args.values()
        ):
            raise ValueError("Missing or invalid tool arguments")
        if name == "end_call":
            return {"status": "ending"}
        if name == "schedule_meeting_on_jitsi":
            timing = datetime.fromisoformat(args["timing"].replace("Z", "+00:00"))
            if timing.utcoffset() is None or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args["user_email"]):
                raise ValueError("Meeting needs a timezone and valid email")
            return await asyncio.to_thread(schedule_meeting, args)
        if name == "save_reponse_from_caller":
            message_id = await asyncio.to_thread(
                mongo_tool.mongo_save_message, context.get("name", ""),
                context.get("message", ""), args["response"],
            )
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
    try:
        start = await asyncio.wait_for(wait_for_stream(websocket), timeout=10)
        token = start.get("customParameters", {}).get("context_id", "")
        context = await asyncio.to_thread(contexts.take, token)
        async with websockets.connect(
            LIVE_URL, extra_headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            open_timeout=10, close_timeout=5, max_size=2**22,
        ) as live:
            bridge = LiveBridge(
                websocket, live, start["streamSid"], session_config(SETTINGS, context, voicemail),
                greeting(context, voicemail), make_tool_executor(context, voicemail),
            )
            await bridge.run()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error("Call bridge failed (%s)", type(exc).__name__)
    finally:
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
    query = urlencode({"script": "2", "name": body.get("name", ""), "message": body.get("message", "")})
    url = f"{PUBLIC_BASE_URL}/incoming-call?{query}"
    results = []
    for index, number in enumerate(numbers):
        try:
            call = await asyncio.to_thread(twilio_client.calls.create, to=number,
                                           from_=TWILIO_FROM_NUMBER, url=url)
            results.append({"to": number, "sid": call.sid})
            if index < len(numbers) - 1:
                await asyncio.sleep(15)
        except Exception:
            results.append({"to": number, "error": "Call could not be started"})
    return {"status": "done", "calls": results}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
