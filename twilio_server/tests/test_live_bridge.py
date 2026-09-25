import asyncio
import base64
import json
import unittest
from unittest.mock import AsyncMock

from live_bridge import LiveBridge, has_speech
from live_config import LIVE_URL, LiveSettings, greeting, session_config


SILENCE = base64.b64encode(b"\xff" * 160).decode()
SPEECH = base64.b64encode(b"\x20" * 160).decode()


class Socket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.auto_close = True

    def feed(self, event):
        self.incoming.put_nowait(json.dumps(event) if event is not None else None)

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.incoming.get()
        if event is None:
            raise StopAsyncIteration
        return event

    def iter_text(self):
        return self

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.close" and self.auto_close:
            self.feed({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 12}})

    async def send_json(self, event):
        self.sent.append(event)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


def response_events(calls, delegation="d1", response_id="r1"):
    events = [{"type": "response.created", "response": {"id": response_id}}]
    for call_id, name, args in calls:
        events.append({"type": "response.output_item.done", "item": {
            "type": "function_call", "call_id": call_id, "name": name,
            "arguments": json.dumps(args),
        }})
    # Live deliberately omits output items from the completion envelope.
    events.append({"type": "response.completed", "response": {"id": response_id, "output": []}})
    return [{"type": "response.event", "delegation_id": delegation, "event": event} for event in events]


class ConfigTests(unittest.TestCase):
    def test_live_session_and_voicemail_tools(self):
        settings = LiveSettings.from_env({})
        self.assertEqual(settings.model, "gpt-live-1")
        self.assertEqual(settings.voice, "marin")
        self.assertEqual(LIVE_URL, "wss://api.openai.com/v1/live/sessions")
        normal = session_config(settings, {"name": "Alice"})
        voicemail = session_config(settings, {"name": "Bob"}, True)
        self.assertEqual(normal["audio"]["format"], {"type": "audio/pcmu", "rate": 8000})
        self.assertFalse(normal["delegation"]["responses"]["parallel_tool_calls"])
        vm_backend = voicemail["delegation"]["responses"]
        self.assertEqual([t["name"] for t in vm_backend["tools"]], ["save_voice_mail_message", "end_call"])
        self.assertIn("Bob", vm_backend["instructions"])
        self.assertNotIn("Alice", vm_backend["instructions"])
        self.assertNotIn("output_modalities", normal)
        self.assertIn("unavailable", greeting({}, True))

    def test_stale_model_fails_with_migration_instruction(self):
        with self.assertRaisesRegex(ValueError, "MODEL=gpt-live-1"):
            LiveSettings.from_env({"MODEL": "gpt-realtime-2.1"})

    def test_mulaw_gate_distinguishes_speech_and_silence(self):
        self.assertFalse(has_speech(SILENCE))
        self.assertFalse(has_speech(base64.b64encode(b"\x7f" * 160).decode()))
        self.assertTrue(has_speech(SPEECH))


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.twilio = Socket()
        self.live = Socket()
        self.execute = AsyncMock(return_value={"status": "saved"})
        self.bridge = LiveBridge(
            self.twilio, self.live, "MZ-test", session_config(LiveSettings(), {}),
            greeting({}), self.execute, startup_timeout=0.3, close_timeout=0.03,
            tool_timeout=0.1, goodbye_grace=0.01, goodbye_quiet=0.01,
            goodbye_timeout=0.1, mark_timeout=0.1,
        )
        self.task = asyncio.create_task(self.bridge.run())
        await until(lambda: self.live.sent)

    async def asyncTearDown(self):
        if not self.task.done():
            self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def start(self):
        self.live.feed({"type": "session.started", "session": {"id": "live-test"}})
        await self.bridge.ready.wait()

    async def stop(self):
        self.twilio.feed({"event": "stop"})
        await asyncio.wait_for(self.task, 1)

    def types(self):
        return [event["type"] for event in self.live.sent]

    async def test_start_gates_audio_and_greeting_then_forwards_exact_mulaw(self):
        self.twilio.feed({"event": "media", "media": {"payload": SILENCE}})
        await asyncio.sleep(0.01)
        self.assertEqual(self.types(), ["session.start"])
        await self.start()
        await until(lambda: "session.input_audio.append" in self.types())
        self.assertEqual(self.types()[:3], ["session.start", "session.instructions.append", "session.input_audio.append"])
        self.assertEqual(self.live.sent[2]["audio"], SILENCE)
        self.live.feed({"type": "session.output_audio.delta", "delta": SPEECH})
        await until(lambda: self.twilio.sent)
        self.assertEqual(self.twilio.sent[0], {"event": "media", "streamSid": "MZ-test", "media": {"payload": SPEECH}})
        await self.stop()
        self.assertEqual(self.bridge.final_usage, {"seconds": 12})
        self.assertTrue(self.bridge.closed.is_set())

    async def test_all_tool_results_precede_one_backend_continuation(self):
        await self.start()
        for event in response_events([("c1", "save_reponse_from_caller", {"response": "Yes"}),
                                      ("c2", "save_reponse_from_caller", {"response": "Thanks"})]):
            self.live.feed(event)
        await until(lambda: "response.create" in self.types())
        tool_events = [e for e in self.live.sent if e["type"].startswith("response.")]
        self.assertEqual([e["type"] for e in tool_events], ["response.item.create", "response.item.create", "response.create"])
        self.assertEqual([e["item"]["call_id"] for e in tool_events[:2]], ["c1", "c2"])
        self.assertEqual(self.execute.await_count, 2)
        await self.stop()

    async def test_audio_continues_while_tool_waits_and_duplicate_call_is_not_reexecuted(self):
        release = asyncio.Event()

        async def slow_tool(*args):
            await release.wait()
            return {"status": "saved"}

        self.execute.side_effect = slow_tool
        await self.start()
        events = response_events([("c1", "save_reponse_from_caller", {"response": "Yes"})])
        for event in events:
            self.live.feed(event)
        await until(lambda: self.execute.await_count == 1)
        self.live.feed({"type": "session.output_audio.delta", "delta": SPEECH})
        await until(lambda: self.twilio.sent)
        self.assertNotIn("response.create", self.types())
        release.set()
        await until(lambda: "response.create" in self.types())
        self.live.feed(events[-1])
        for event in response_events([("c1", "save_reponse_from_caller", {"response": "Yes"})], response_id="r2"):
            self.live.feed(event)
        await until(lambda: self.types().count("response.create") == 2)
        self.assertEqual(self.execute.await_count, 1)
        await self.stop()

    async def test_tool_error_is_returned_without_claiming_success(self):
        self.execute.side_effect = RuntimeError("private service detail")
        await self.start()
        for event in response_events([("c1", "save_reponse_from_caller", {"response": "Yes"})]):
            self.live.feed(event)
        await until(lambda: "response.create" in self.types())
        output = next(e["item"]["output"] for e in self.live.sent if e["type"] == "response.item.create")
        self.assertEqual(json.loads(output)["status"], "error")
        self.assertNotIn("private service detail", output)
        await self.stop()

    async def test_failed_response_never_executes_collected_tools(self):
        await self.start()
        events = response_events([("c1", "end_call", {})])
        events[-1]["event"]["type"] = "response.failed"
        for event in events:
            self.live.feed(event)
        await asyncio.sleep(0.01)
        self.execute.assert_not_awaited()
        await self.stop()

    async def test_end_call_waits_for_twilio_playback_mark(self):
        self.execute.return_value = {"status": "ending"}
        await self.start()
        self.live.feed({"type": "session.output_audio.delta", "delta": SPEECH})
        for event in response_events([("c1", "end_call", {})]):
            self.live.feed(event)
        await until(lambda: any(e["event"] == "mark" for e in self.twilio.sent))
        self.assertFalse(self.task.done())
        self.assertNotIn("session.close", self.types())
        self.twilio.feed({"event": "mark", "mark": {"name": self.bridge.end_mark}})
        await asyncio.wait_for(self.task, 1)
        self.assertTrue(self.bridge.closed.is_set())

    async def test_missing_close_ack_has_bounded_cleanup(self):
        self.live.auto_close = False
        await self.start()
        await self.stop()
        self.assertIsNone(self.bridge.final_usage)
        self.assertFalse(self.bridge.closed.is_set())

    async def test_disconnect_drains_inflight_tool_without_writing_to_closed_phone(self):
        release = asyncio.Event()

        async def slow_tool(*args):
            await release.wait()
            return {"status": "saved"}

        self.execute.side_effect = slow_tool
        await self.start()
        for event in response_events([("c1", "save_reponse_from_caller", {"response": "Yes"})]):
            self.live.feed(event)
        await until(lambda: self.execute.await_count == 1)
        self.twilio.feed({"event": "stop"})
        await until(lambda: not self.bridge.accept_tools)
        self.twilio.send_json = AsyncMock(side_effect=RuntimeError("Phone already disconnected"))
        self.live.feed({"type": "session.output_audio.delta", "delta": SPEECH})
        release.set()
        await asyncio.wait_for(self.task, 1)
        self.twilio.send_json.assert_not_awaited()
        self.assertTrue(self.bridge.closed.is_set())
        self.assertLess(self.types().index("response.item.create"), self.types().index("session.close"))

    async def test_startup_timeout_cleans_up_without_audio(self):
        self.twilio.feed({"event": "media", "media": {"payload": SILENCE}})
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(self.task, 1)
        self.assertEqual(self.types(), ["session.start"])

    async def test_startup_error_does_not_send_audio_or_greeting(self):
        self.live.feed({"type": "error", "error": {"code": "invalid_model"}})
        with self.assertRaises(RuntimeError):
            await asyncio.wait_for(self.task, 1)
        self.assertEqual(self.types(), ["session.start"])

    async def test_phone_disconnect_before_live_ready_does_not_hang(self):
        await self.stop()
        self.assertEqual(self.types(), ["session.start"])


if __name__ == "__main__":
    unittest.main()
