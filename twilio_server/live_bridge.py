"""Bidirectional Twilio μ-law audio bridge for the GPT-Live WebSocket protocol.

No SDK or service clients are imported here so the wire protocol can be tested
with in-memory sockets. Tool execution runs independently of both audio readers.
"""

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


def event_id():
    return uuid.uuid4().hex


def has_speech(payload):
    """A conservative μ-law noise gate used only to drain a goodbye before hangup.

    This is not input VAD: GPT-Live handles turn taking and interruptions itself.
    Python 3.13 removed audioop, so decode G.711 samples directly.
    """
    for byte in base64.b64decode(payload, validate=True):
        value = (~byte) & 0xFF
        magnitude = (((value & 15) << 3) + 132) << ((value & 112) >> 4)
        if magnitude - 132 > 500:
            return True
    return False


@dataclass
class ResponseBatch:
    delegation_id: str
    response_id: str
    calls: dict = field(default_factory=dict)


class LiveBridge:
    def __init__(self, twilio, live, stream_sid, config, greeting, execute_tool,
                 *, startup_timeout=10, close_timeout=15, tool_timeout=15,
                 goodbye_grace=2, goodbye_quiet=1, goodbye_timeout=10,
                 mark_timeout=3, max_catchup=0.5,
                 greeting_grace=0.5, greeting_quiet=0.6, greeting_timeout=12,
                 greeting_start_timeout=5):
        self.twilio = twilio
        self.live = live
        self.stream_sid = stream_sid
        self.config = config
        self.greeting = greeting
        self.execute_tool = execute_tool
        self.startup_timeout = startup_timeout
        self.close_timeout = close_timeout
        self.tool_timeout = tool_timeout
        self.goodbye_grace = goodbye_grace
        self.goodbye_quiet = goodbye_quiet
        self.goodbye_timeout = goodbye_timeout
        self.mark_timeout = mark_timeout
        self.max_catchup = max_catchup
        self.greeting_grace = greeting_grace
        self.greeting_quiet = greeting_quiet
        self.greeting_timeout = greeting_timeout
        self.greeting_start_timeout = greeting_start_timeout
        self.input_muted = False
        self.greeting_task = None
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.hangup = asyncio.Event()
        self.mark_played = asyncio.Event()
        self.audio = asyncio.Queue(maxsize=250)
        self.dropped_audio_frames = 0
        self.send_lock = asyncio.Lock()
        self.tool_lock = asyncio.Lock()
        self.batches = {}
        self.current_responses = {}
        self.completed_responses = set()
        self.tool_results = {}
        self.workers = set()
        self.end_task = None
        self.closing = False
        self.accept_tools = True
        self.playback_finished = False
        self.last_speech = 0
        self.final_usage = None
        self.close_reason = None
        self.session_id = None
        self.end_mark = "live_goodbye_" + event_id()

    async def send(self, event):
        async with self.send_lock:
            await self.live.send(json.dumps(event))

    async def read_twilio(self):
        async for raw in self.twilio.iter_text():
            event = json.loads(raw)
            kind = event.get("event")
            if kind == "media" and not self.closing:
                media = event["media"]
                if media.get("track", "inbound") == "inbound":
                    # Keep recent audio if startup or transport stalls. Blocking
                    # here would also prevent reading Twilio's mark/stop events.
                    if self.audio.full():
                        self.audio.get_nowait()
                        self.dropped_audio_frames += 1
                        if self.dropped_audio_frames == 1:
                            logger.warning(
                                "Twilio input audio buffer full stream=%s; dropping oldest frames",
                                self.stream_sid,
                            )
                    self.audio.put_nowait(media["payload"])
            elif kind == "mark" and event.get("mark", {}).get("name") == self.end_mark:
                self.mark_played.set()
            elif kind == "stop":
                return

    async def send_audio(self):
        await self.ready.wait()
        next_frame = time.monotonic()
        while not self.closing:
            payload = await self.audio.get()
            if self.input_muted:
                # Discard rather than queue: this audio is not conversation and
                # would arrive at Live in a burst the moment input resumes.
                continue
            duration = len(base64.b64decode(payload, validate=True)) / 8000
            # Twilio already delivers in real time, so pacing a queue that is
            # never empty just holds the backlog: whatever accumulated during
            # startup is added to every later caller turn for the rest of the
            # call. Drain at twice the sample rate while frames are waiting.
            pace = duration / 2 if self.audio.qsize() else duration
            await asyncio.sleep(max(0, next_frame - time.monotonic()))
            if self.closing:
                return
            now = time.monotonic()
            if now - next_frame > self.max_catchup:
                # Resync only after a stall too long to drain smoothly.
                next_frame = now
            # Advance the sample clock, not send completion time. Small sleep
            # overruns and send overhead must not add latency on every frame.
            next_frame += pace
            await self.send({"type": "session.input_audio.append", "audio": payload})

    async def read_live(self):
        async for raw in self.live:
            event = json.loads(raw)
            kind = event.get("type")
            if kind == "session.started":
                if not self.ready.is_set():
                    self.session_id = event["session"]["id"]
                    # Mute input first: line noise or a caller's "hello" before
                    # the greeting reads as taking the floor, and Luma yields.
                    await self.send({"type": "session.input_audio.mute",
                                     "event_id": event_id()})
                    self.input_muted = True
                    await self.send({
                        "type": "session.instructions.append", "event_id": event_id(),
                        "delegation_id": None, "content": self.greeting,
                    })
                    self.ready.set()
                    self.greeting_task = asyncio.create_task(self.finish_greeting())
            elif kind == "session.output_audio.delta":
                if not self.closing and not self.playback_finished:
                    payload = event["delta"]
                    if has_speech(payload):
                        self.last_speech = time.monotonic()
                    await self.twilio.send_json({
                        "event": "media", "streamSid": self.stream_sid,
                        "media": {"payload": payload},
                    })
            elif kind == "response.event":
                self.handle_response(event)
            elif kind == "session.closed":
                self.final_usage = event.get("usage")
                self.close_reason = event.get("reason")
                self.closed.set()
                return
            elif kind == "error":
                error = event.get("error", {})
                # No transcripts, audio, tool arguments, or credentials in logs.
                # The API's own message names the rejected field and is needed to
                # diagnose a rejection at all; it does not echo caller speech.
                logger.error("Live error code=%s command=%s param=%s message=%s",
                             error.get("code"), error.get("client_event_id"),
                             error.get("param"), error.get("message"))
                # A rejected tool result/continuation can strand a call. Do not
                # retry side effects or silently leave the caller waiting.
                raise RuntimeError("OpenAI rejected a Live command")

    def handle_response(self, envelope):
        inner = envelope["event"]
        kind = inner.get("type")
        delegation_id = envelope["delegation_id"]
        if kind == "response.created":
            response_id = inner["response"]["id"]
            self.current_responses[delegation_id] = response_id
            self.batches.setdefault((delegation_id, response_id),
                                    ResponseBatch(delegation_id, response_id))
        elif kind == "response.output_item.done":
            item = inner["item"]
            if item.get("type") != "function_call":
                return
            response_id = self.current_responses.get(delegation_id)
            if not response_id:
                raise RuntimeError("Function call received without a backend response ID")
            self.batches[(delegation_id, response_id)].calls[item["call_id"]] = item
        elif kind in {"response.completed", "response.failed", "response.incomplete", "response.cancelled"}:
            response = inner["response"]
            key = (delegation_id, response["id"])
            if key in self.completed_responses:
                return
            self.completed_responses.add(key)
            batch = self.batches.pop(key, None)
            logger.info("Backend response=%s status=%s usage=%s", response["id"],
                        kind, response.get("usage"))
            if kind == "response.completed" and batch and batch.calls and self.accept_tools:
                task = asyncio.create_task(self.run_tools(batch))
                self.workers.add(task)
                task.add_done_callback(self.worker_done)
            elif kind != "response.completed":
                logger.warning("Backend did not complete: response=%s", response["id"])

    def worker_done(self, task):
        self.workers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Tool result delivery failed (%s)", type(task.exception()).__name__)
            self.hangup.set()

    async def run_tools(self, batch):
        # Keep a batch's results together; audio continues on the other tasks.
        async with self.tool_lock:
            for call_id, item in batch.calls.items():
                if self.closing:
                    return
                if call_id not in self.tool_results:
                    try:
                        args = json.loads(item["arguments"])
                        if not isinstance(args, dict):
                            raise ValueError("Function arguments must be an object")
                        result = await self.execute_tool(item["name"], call_id, args)
                    except Exception as exc:
                        logger.warning("Tool failed name=%s error=%s", item["name"], type(exc).__name__)
                        result = {"status": "error", "message": (
                            "The operation could not be confirmed. Do not claim success or retry "
                            "automatically; explain that it needs to be checked."
                        )}
                    self.tool_results[call_id] = result
                result = self.tool_results[call_id]
                if self.closing:
                    return
                await self.send({
                    "type": "response.item.create", "event_id": event_id(),
                    "item": {"type": "function_call_output", "call_id": call_id,
                             "output": json.dumps(result)},
                })
                if (self.accept_tools and item["name"] == "end_call"
                        and result.get("status") == "ending" and self.end_task is None):
                    self.end_task = asyncio.create_task(self.finish_goodbye())
            if not self.closing:
                # These commands deliberately have no invented delegation/response
                # fields: Live correlates function results by their original call_id.
                await self.send({"type": "response.create", "event_id": event_id()})

    async def finish_greeting(self):
        """Hold input muted until the greeting has been spoken and gone quiet."""
        started = time.monotonic()
        try:
            while time.monotonic() - started < self.greeting_timeout:
                await asyncio.sleep(0.05)
                now = time.monotonic()
                if self.last_speech <= started:
                    # Never leave the caller muted waiting on a greeting that is
                    # not coming; a silent assistant is better than a deaf one.
                    if now - started >= self.greeting_start_timeout:
                        logger.warning("Greeting did not begin; restoring caller input")
                        return
                    continue
                if now - started >= self.greeting_grace and now - self.last_speech >= self.greeting_quiet:
                    return
        finally:
            # Always restore input, including on timeout or cancellation, or the
            # caller would be unable to speak for the rest of the call.
            if self.input_muted and not self.closing:
                self.input_muted = False
                try:
                    await self.send({"type": "session.input_audio.unmute",
                                     "event_id": event_id()})
                except Exception as exc:
                    logger.warning("Input unmute failed (%s)", type(exc).__name__)

    async def finish_goodbye(self):
        started = time.monotonic()
        while time.monotonic() - started < self.goodbye_timeout:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            if now - started >= self.goodbye_grace and now - self.last_speech >= self.goodbye_quiet:
                break
        # Live has no audio-done event. Drain the audio already queued at Twilio
        # using its mark acknowledgment, including any goodbye spoken meanwhile.
        self.playback_finished = True
        try:
            await self.twilio.send_json({"event": "mark", "streamSid": self.stream_sid,
                                        "mark": {"name": self.end_mark}})
            await asyncio.wait_for(self.mark_played.wait(), self.mark_timeout)
        except TimeoutError:
            logger.warning("Goodbye playback acknowledgment timed out")
        finally:
            self.hangup.set()

    async def run(self):
        tasks = []
        reader = None
        try:
            await self.send({"type": "session.start", "event_id": event_id(), "session": self.config})
            reader = asyncio.create_task(self.read_live())
            receiver = asyncio.create_task(self.read_twilio())
            sender = asyncio.create_task(self.send_audio())
            hangup = asyncio.create_task(self.hangup.wait())
            ready = asyncio.create_task(asyncio.wait_for(self.ready.wait(), self.startup_timeout))
            tasks = [reader, receiver, sender, hangup, ready]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if ready in done:
                ready.result()
                done, _ = await asyncio.wait(tasks[:-1], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.accept_tools = False
            self.playback_finished = True
            # Stop input immediately when the phone disconnects, but allow an
            # in-flight database operation to return before finalizing Live.
            for task in tasks:
                if task is not reader:
                    task.cancel()
            if self.end_task:
                self.end_task.cancel()
            if self.greeting_task:
                self.greeting_task.cancel()
            if self.workers:
                _, pending = await asyncio.wait(self.workers, timeout=self.tool_timeout)
                for task in pending:
                    task.cancel()
                if pending:
                    logger.warning("Call ended with unconfirmed tool work; automatic retry disabled")
            self.closing = True
            if reader and not reader.done() and self.ready.is_set() and not self.closed.is_set():
                try:
                    await self.send({"type": "session.close", "event_id": event_id()})
                    # The reader stays alive to receive the authoritative final usage.
                    await asyncio.wait_for(asyncio.shield(reader), self.close_timeout)
                except Exception as exc:
                    logger.warning("Live finalization incomplete (%s)", type(exc).__name__)
            if reader:
                reader.cancel()
            cleanup = (tasks + list(self.workers)
                       + [t for t in (self.end_task, self.greeting_task) if t])
            await asyncio.gather(*cleanup, return_exceptions=True)
            if self.dropped_audio_frames:
                logger.warning("Twilio input audio dropped stream=%s frames=%d",
                               self.stream_sid, self.dropped_audio_frames)
            if self.closed.is_set():
                logger.info("Live session=%s reason=%s final_usage=%s", self.session_id,
                            self.close_reason, self.final_usage)
            else:
                logger.warning("Live final usage unconfirmed session=%s", self.session_id)
