"""Endpoint and tool-contract tests. Every external side effect is replaced."""

import asyncio
import importlib
import json
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from fastapi.testclient import TestClient


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.expirations[key] = ttl

    def eval(self, script, key_count, key):
        return self.values.pop(key, None)


mongo = ModuleType("mongo_tool")
mongo.mongo_save_message = Mock(return_value="message-1")
mongo.save_voice_mail_message = Mock(return_value="voicemail-1")
mongo.insert_meeting = Mock(return_value="meeting-1")
worker = ModuleType("celery_worker")
worker.tool_call_fn = Mock()
memory = MemoryRedis()

# Never import the real Mongo/Celery modules: they create clients at import time.
with patch.dict(sys.modules, {"mongo_tool": mongo, "celery_worker": worker}), \
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "MODEL": "gpt-live-1",
                               "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
                               "TWILIO_AUTH_TOKEN": "test-token"}, clear=True), \
        patch("dotenv.load_dotenv"), patch("redis.from_url", return_value=memory):
    main = importlib.import_module("main")


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        mongo.mongo_save_message.reset_mock()
        mongo.save_voice_mail_message.reset_mock()
        mongo.insert_meeting.reset_mock()
        worker.tool_call_fn.reset_mock()
        worker.tool_call_fn.delay.side_effect = None

    async def test_voicemail_uses_existing_database_contract(self):
        args = {"caller_name": "Alice", "message": "Please call back", "phone_no": "+15555550100"}
        result = await main.make_tool_executor({}, True)("save_voice_mail_message", "call-1", args)
        mongo.save_voice_mail_message.assert_called_once_with("call-1", args)
        self.assertEqual(result, {"status": "saved", "message_id": "voicemail-1"})

    async def test_caller_response_uses_response_field_and_own_call_context(self):
        alice = main.make_tool_executor({"name": "Alice", "message": "Hiring"})
        bob = main.make_tool_executor({"name": "Bob", "message": "Interview"})
        await asyncio.gather(
            alice("save_reponse_from_caller", "a", {"response": "Yes"}),
            bob("save_reponse_from_caller", "b", {"response": "Tomorrow"}),
        )
        mongo.mongo_save_message.assert_any_call("Alice", "Hiring", "Yes")
        mongo.mongo_save_message.assert_any_call("Bob", "Interview", "Tomorrow")

    async def test_meeting_preserves_database_and_notification_workflow(self):
        args = {"name": "Alice", "agenda": "Interview", "timing": "2026-10-01T14:00:00-04:00",
                "user_email": "alice@example.com"}
        result = await main.make_tool_executor({})("schedule_meeting_on_jitsi", "m1", args)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["notifications"], "queued")
        mongo.insert_meeting.assert_called_once_with("Alice", "Interview", args["timing"], result["meeting_url"])
        self.assertEqual(worker.tool_call_fn.delay.call_count, 3)

    async def test_partial_notification_failure_does_not_lose_saved_meeting(self):
        worker.tool_call_fn.delay.side_effect = RuntimeError("broker unavailable")
        args = {"name": "Alice", "agenda": "Interview", "timing": "2026-10-01T14:00:00-04:00",
                "user_email": "alice@example.com"}
        result = await main.make_tool_executor({})("schedule_meeting_on_jitsi", "m1", args)
        self.assertEqual(result["status"], "saved")
        self.assertIn("incomplete", result["notifications"])
        self.assertEqual(mongo.insert_meeting.call_count, 1)

    async def test_invalid_or_unavailable_tools_cannot_run_side_effects(self):
        execute = main.make_tool_executor({}, True)
        with self.assertRaises(ValueError):
            await execute("schedule_meeting_on_jitsi", "call-1", {})
        with self.assertRaises(ValueError):
            await execute("save_voice_mail_message", "call-1", {"message": "Only text"})
        mongo.save_voice_mail_message.assert_not_called()
        mongo.insert_meeting.assert_not_called()


class EndpointTests(unittest.TestCase):
    def setUp(self):
        memory.values.clear()
        memory.expirations.clear()
        self.client = TestClient(main.app)

    def test_health_reports_live_model(self):
        self.assertEqual(self.client.get("/").json()["model"], "gpt-live-1")

    def test_twiml_uses_unique_context_tokens_without_stream_query_string(self):
        tokens = []
        for name in ["Alice", "Bob"]:
            response = self.client.post("/incoming-call", params={"name": name, "message": "x" * 1000})
            self.assertEqual(response.status_code, 200)
            xml = ElementTree.fromstring(response.text)
            stream = xml.find("./Connect/Stream")
            self.assertEqual(stream.attrib["url"], "wss://testserver/media-stream")
            self.assertIsNotNone(xml.find("Hangup"))
            parameter = stream.find("Parameter")
            self.assertEqual(parameter.attrib["name"], "context_id")
            tokens.append(parameter.attrib["value"])
        self.assertNotEqual(*tokens)
        self.assertEqual(main.contexts.take(tokens[0])["name"], "Alice")
        self.assertEqual(main.contexts.take(tokens[1])["name"], "Bob")
        self.assertTrue(all(ttl == 300 for ttl in memory.expirations.values()))
        with self.assertRaises(ValueError):
            main.contexts.take(tokens[0])

    def test_voicemail_endpoint_preserves_route(self):
        response = self.client.get("/voice-mail")
        xml = ElementTree.fromstring(response.text)
        self.assertEqual(xml.find("./Connect/Stream").attrib["url"], "wss://testserver/media-stream-voicemail")

    def test_outbound_call_uses_preserved_endpoint_and_encoded_context(self):
        with patch.object(main.twilio_client.calls, "create", return_value=SimpleNamespace(sid="CA-test")) as create:
            response = self.client.post("/start-calls", json={"numbers": ["+15555550100"],
                                                            "name": "Alice & Bob", "message": "Hiring?"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["calls"][0]["sid"], "CA-test")
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs["to"], "+15555550100")
        query = parse_qs(urlsplit(kwargs["url"]).query)
        self.assertEqual(query, {"script": ["2"], "name": ["Alice & Bob"], "message": ["Hiring?"]})


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_waits_for_twilio_start_and_connects_to_live(self):
        token = main.contexts.put({"script": "1", "name": "Alice", "message": ""})
        start = {"streamSid": "MZ-test", "customParameters": {"context_id": token},
                 "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}

        async def incoming():
            yield json.dumps({"event": "connected"})
            yield json.dumps({"event": "start", "start": start})

        phone = SimpleNamespace(accept=AsyncMock(), close=AsyncMock(), iter_text=incoming)
        connection = AsyncMock()
        with patch.object(main.websockets, "connect", return_value=connection) as connect, \
                patch.object(main, "LiveBridge") as bridge:
            bridge.return_value.run = AsyncMock()
            await main.handle_stream(phone, voicemail=True)
        self.assertEqual(connect.call_args.args[0], "wss://api.openai.com/v1/live/sessions")
        self.assertEqual(connect.call_args.kwargs["extra_headers"], {"Authorization": "Bearer test-key"})
        config = bridge.call_args.args[3]
        self.assertEqual(config["model"], "gpt-live-1")
        tools = config["delegation"]["responses"]["tools"]
        self.assertEqual(tools[0]["name"], "save_voice_mail_message")
        bridge.return_value.run.assert_awaited_once()
        phone.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
