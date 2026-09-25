import asyncio
import base64
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from live_bridge import LiveBridge, has_speech
from live_config import DEFAULT_VOICE_STYLE, LIVE_URL, LiveSettings, greeting, session_config


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

    def test_operator_style_reaches_voice_sessions_without_changing_backend_or_voice(self):
        style = "Use a relaxed American delivery; treat {pauses} as phrasing cues."
        settings = LiveSettings.from_env({"VOICE_STYLE": style})
        for voicemail in (False, True):
            with self.subTest(voicemail=voicemail):
                config = session_config(settings, {}, voicemail)
                default = session_config(LiveSettings(), {}, voicemail)
                self.assertIn(style, config["instructions"])
                self.assertNotIn(DEFAULT_VOICE_STYLE, config["instructions"])
                for policy in ("AI personal assistant", "Backchannel policy:",
                               "Interruption policy:", "Delegation policy:"):
                    self.assertIn(policy, config["instructions"])
                self.assertEqual(config["delegation"], default["delegation"])
                self.assertEqual(config["audio"]["output"]["voice"], "marin")
                self.assertNotIn("style", config)
                if voicemail:
                    self.assertIn("Samarth is unavailable", config["instructions"])

    def test_missing_or_blank_style_keeps_reviewed_default(self):
        for env in ({}, {"VOICE_STYLE": ""}, {"VOICE_STYLE": "  \n  "}):
            with self.subTest(env=env):
                config = session_config(LiveSettings.from_env(env), {})
                self.assertIn(DEFAULT_VOICE_STYLE, config["instructions"])

    def test_mulaw_gate_distinguishes_speech_and_silence(self):
        self.assertFalse(has_speech(SILENCE))
        self.assertFalse(has_speech(base64.b64encode(b"\x7f" * 160).decode()))
        self.assertTrue(has_speech(SPEECH))


class AudioPacingTests(unittest.IsolatedAsyncioTestCase):
    async def simulate_audio(self, durations, *, send_delay=0, wakeup_delay=0,
                             stall_frame=None, stall_duration=0):
        bridge = LiveBridge(Socket(), Socket(), "MZ-test", {}, "", AsyncMock())
        bridge.ready.set()
        now = 100.0
        sent_at = []

        def enqueue(index):
            bridge.audio.put_nowait(base64.b64encode(
                b"\xff" * round(durations[index] * 8000)).decode())

        async def sleep(delay):
            nonlocal now
            now += delay + wakeup_delay

        async def send(event):
            nonlocal now
            sent_at.append(now)
            now += send_delay
            if len(sent_at) == stall_frame:
                now += stall_duration
            if len(sent_at) == len(durations):
                bridge.closing = True
            else:
                enqueue(len(sent_at))

        enqueue(0)
        bridge.send = send
        # Patch only the bridge's clock and sleep, leaving the event loop's
        # real clock intact. Simulate long calls without wall-clock waits.
        with patch("live_bridge.time", SimpleNamespace(monotonic=lambda: now)), \
                patch("live_bridge.asyncio", SimpleNamespace(sleep=sleep)):
            await bridge.send_audio()
        return sent_at

    async def test_send_overhead_and_wakeup_jitter_do_not_accumulate(self):
        durations = [0.02, 0.04, 0.01] * 2000
        sent_at = await self.simulate_audio(durations, send_delay=0.003, wakeup_delay=0.002)
        expected = sent_at[0]
        for sent, duration in zip(sent_at, durations):
            self.assertAlmostEqual(sent, expected, places=6)
            expected += duration

    async def test_buffered_audio_is_paced_at_sample_rate(self):
        sent_at = await self.simulate_audio([0.02, 0.04, 0.01, 0.02])
        for sent, expected in zip(sent_at, [100, 100.02, 100.06, 100.07]):
            self.assertAlmostEqual(sent, expected, places=6)

    async def test_long_send_stall_does_not_cause_a_catchup_burst(self):
        sent_at = await self.simulate_audio([0.02] * 10, stall_frame=2, stall_duration=1)
        self.assertAlmostEqual(sent_at[2] - sent_at[1], 1, places=6)
        for previous, current in zip(sent_at[2:], sent_at[3:]):
            self.assertAlmostEqual(current - previous, 0.02, places=6)


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
        # Audio must already be flowing when the greeting instruction lands, or
        # the model never speaks first.
        await until(lambda: "session.instructions.append" in self.types())
        types = self.types()
        self.assertLess(types.index("session.input_audio.append"),
                        types.index("session.instructions.append"))
        self.assertEqual(self.live.sent[1]["audio"], SILENCE)
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

    async def simulate_backlog(self, count, duration=0.02):
        """Drain a queue that is already full when the session opens."""
        bridge = LiveBridge(Socket(), Socket(), "MZ-test", {}, "", AsyncMock())
        bridge.ready.set()
        now = 100.0
        sent_at = []
        frame = base64.b64encode(b"\xff" * round(duration * 8000)).decode()
        for _ in range(count):
            bridge.audio.put_nowait(frame)

        async def sleep(delay):
            nonlocal now
            now += delay

        async def send(event):
            sent_at.append(now)
            if len(sent_at) == count:
                bridge.closing = True

        bridge.send = send
        with patch("live_bridge.time", SimpleNamespace(monotonic=lambda: now)), \
                patch("live_bridge.asyncio", SimpleNamespace(sleep=sleep)):
            await bridge.send_audio()
        return sent_at

    async def test_startup_backlog_drains_instead_of_delaying_every_turn(self):
        # Twilio already delivers in real time, so pacing a queue that never
        # empties holds the backlog forever: audio buffered while the session
        # was opening would be added to every later caller turn.
        sent_at = await self.simulate_backlog(10)
        self.assertAlmostEqual(sent_at[-1] - sent_at[0], 0.09, places=6)
        self.assertLess(sent_at[-1] - sent_at[0], 9 * 0.02)

    async def test_startup_overflow_is_bounded_and_still_starts(self):
        capacity = self.bridge.audio.maxsize
        self.twilio.feed({"event": "media", "media": {"payload": SPEECH}})
        for _ in range(capacity):
            self.twilio.feed({"event": "media", "media": {"payload": SILENCE}})
        with self.assertLogs("live_bridge", level="WARNING") as logs:
            await until(lambda: self.twilio.incoming.empty())
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(self.bridge.audio.qsize(), capacity)
        self.assertFalse(self.task.done())
        self.assertEqual(self.types(), ["session.start"])
        await self.start()
        await until(lambda: "session.input_audio.append" in self.types())
        self.assertEqual(self.live.sent[1]["audio"], SILENCE)
        await self.stop()
        self.assertTrue(self.bridge.closed.is_set())

    async def test_overflow_during_stalled_send_preserves_controls_and_recovers(self):
        release = asyncio.Event()
        sending = asyncio.Event()
        original_send = self.live.send

        async def stalled_send(raw):
            if json.loads(raw)["type"] == "session.input_audio.append" and not sending.is_set():
                sending.set()
                await release.wait()
            await original_send(raw)

        self.live.send = stalled_send
        await self.start()
        self.twilio.feed({"event": "media", "media": {"payload": SILENCE}})
        await asyncio.wait_for(sending.wait(), 1)
        capacity = self.bridge.audio.maxsize
        for _ in range(capacity):
            self.twilio.feed({"event": "media", "media": {"payload": SILENCE}})
        for _ in range(capacity):
            self.twilio.feed({"event": "media", "media": {"payload": SPEECH}})
        self.twilio.feed({"event": "mark", "mark": {"name": self.bridge.end_mark}})
        with self.assertLogs("live_bridge", level="WARNING") as logs:
            await asyncio.wait_for(self.bridge.mark_played.wait(), 1)
        self.assertEqual(len(logs.output), 1)
        self.assertFalse(self.task.done())
        self.assertEqual(self.bridge.audio.qsize(), capacity)
        release.set()
        await until(lambda: self.types().count("session.input_audio.append") >= 2)
        audio = [e["audio"] for e in self.live.sent if e["type"] == "session.input_audio.append"]
        self.assertEqual(audio[:2], [SILENCE, SPEECH])
        await self.stop()
        self.assertEqual(self.types().count("session.close"), 1)
        self.assertEqual(self.bridge.final_usage, {"seconds": 12})

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
