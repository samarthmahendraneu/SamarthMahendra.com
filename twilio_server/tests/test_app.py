"""Endpoint and tool-contract tests. Every external side effect is replaced."""

import asyncio
import importlib
import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from fastapi.testclient import TestClient

from memory_redis import MemoryRedis

mongo = ModuleType("mongo_tool")
mongo.mongo_save_message = Mock(return_value="message-1")
mongo.save_voice_mail_message = Mock(return_value="voicemail-1")
mongo.insert_meeting = Mock(return_value="meeting-1")
mongo.save_relayed_message = Mock(return_value="relay-1")
worker = ModuleType("worker_client")
worker.enqueue_job = Mock()
worker.enqueue_chat_followup = Mock()
memory = MemoryRedis()

# Never import the real Mongo/Celery modules: they create clients at import time.
with patch.dict(sys.modules, {"mongo_tool": mongo, "worker_client": worker}), \
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "MODEL": "gpt-live-1",
                               "TWILIO_ACCOUNT_SID": "AC" + "0" * 32,
                               "TWILIO_AUTH_TOKEN": "test-token"}, clear=True), \
        patch("dotenv.load_dotenv"), patch("redis.from_url", return_value=memory):
    main = importlib.import_module("main")

CHANNEL = "call:CA" + "1" * 32


def started_jobs(kind=None):
    records = [json.loads(v) for k, v in memory.values.items() if k.startswith("job:")]
    return sorted((r for r in records if kind is None or r["kind"] == kind),
                  key=lambda r: r["created_at"])


def reset():
    memory.__init__()
    for mock in (mongo.mongo_save_message, mongo.save_voice_mail_message,
                 mongo.insert_meeting, mongo.save_relayed_message,
                 worker.enqueue_job, worker.enqueue_chat_followup):
        mock.reset_mock()
    worker.enqueue_job.side_effect = None


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()

    def executor(self, context=None, voicemail=False, asked=None):
        return main.make_tool_executor(dict({"channel": CHANNEL}, **(context or {})), voicemail, asked)

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

    async def test_meeting_is_saved_and_the_invite_reports_back_to_the_call(self):
        args = {"name": "Alice", "agenda": "Interview", "timing": "2026-10-01T14:00:00-04:00",
                "user_email": "alice@example.com"}
        result = await self.executor()("schedule_meeting_on_jitsi", "m1", args)
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["notifications"], "queued")
        mongo.insert_meeting.assert_called_once_with("Alice", "Interview", args["timing"], result["meeting_url"])
        invite, copy, notice = started_jobs()
        self.assertEqual(result["task_id"], invite["id"])
        # The caller hears when their invite has gone; Samarth's copies are silent.
        self.assertEqual((invite["kind"], invite["args"]["to"], invite["origin"], invite["announce"]),
                         ("email.send", "alice@example.com", CHANNEL, "always"))
        self.assertIn("Thursday, October 1 at 2:00 PM (UTC-04:00)", invite["args"]["body"])
        self.assertIn(result["meeting_url"], invite["args"]["body"])
        self.assertEqual((copy["announce"], notice["kind"], notice["announce"]),
                         ("never", "discord.send", "never"))
        self.assertEqual(worker.enqueue_job.call_count, 3)

    async def test_partial_notification_failure_does_not_lose_saved_meeting(self):
        worker.enqueue_job.side_effect = RuntimeError("broker unavailable")
        args = {"name": "Alice", "agenda": "Interview", "timing": "2026-10-01T14:00:00-04:00",
                "user_email": "alice@example.com"}
        result = await self.executor()("schedule_meeting_on_jitsi", "m1", args)
        self.assertEqual(result["status"], "saved")
        self.assertIn("incomplete", result["notifications"])
        self.assertEqual(mongo.insert_meeting.call_count, 1)
        # An unqueued job is recorded as failed, not left looking in progress.
        self.assertTrue(all(job["status"] == "failed" for job in started_jobs()))

    async def test_caller_response_is_relayed_to_discord_with_its_question(self):
        execute = self.executor({"name": "Hrushank", "message": "Coming to the party?"})
        result = await execute("save_reponse_from_caller", "r1", {"response": "Yes"})
        self.assertEqual(result["relay"], "queued")
        (job,) = started_jobs("discord.send")
        for part in ("Hrushank", "Coming to the party?", "Yes"):
            self.assertIn(part, job["args"]["content"])
        # A failed relay is told to the call, so the caller isn't misled.
        self.assertEqual((job["origin"], job["announce"]), (CHANNEL, "failure"))
        # One copy of the response, not a second empty one in messages_relayed.
        mongo.save_relayed_message.assert_not_called()

    async def test_relay_failure_is_logged_with_its_cause(self):
        worker.enqueue_job.side_effect = RuntimeError("broker down")
        with self.assertLogs("main", level="WARNING") as logs:
            result = await self.executor()("save_reponse_from_caller", "r1", {"response": "Yes"})
        self.assertIn("incomplete", result["relay"])
        self.assertIn("RuntimeError: broker down", logs.output[0])

    async def test_relayed_message_is_saved_and_queued_for_discord(self):
        args = {"caller_name": "Alice", "message": "Call me about the offer"}
        result = await self.executor()("send_messages_to_samarth", "r1", args)
        mongo.save_relayed_message.assert_called_once_with("r1", args)
        self.assertEqual(result, {"status": "saved", "message_id": "relay-1", "relay": "queued"})
        # A background job, never a wait for Samarth's reply.
        (job,) = started_jobs("discord.send")
        self.assertIn("Alice", job["args"]["content"])
        self.assertIn("Call me about the offer", job["args"]["content"])

    async def test_relay_failure_does_not_lose_the_saved_message(self):
        worker.enqueue_job.side_effect = RuntimeError("broker unavailable")
        result = await self.executor()(
            "send_messages_to_samarth", "r1", {"caller_name": "Alice", "message": "Hi"})
        self.assertEqual(result["status"], "saved")
        self.assertIn("incomplete", result["relay"])
        self.assertEqual(mongo.save_relayed_message.call_count, 1)

    async def test_relay_is_not_offered_during_voicemail(self):
        with self.assertRaises(ValueError):
            await main.make_tool_executor({}, True)(
                "send_messages_to_samarth", "r1", {"caller_name": "A", "message": "Hi"})
        mongo.save_relayed_message.assert_not_called()

    async def test_invalid_or_unavailable_tools_cannot_run_side_effects(self):
        execute = main.make_tool_executor({}, True)
        with self.assertRaises(ValueError):
            await execute("schedule_meeting_on_jitsi", "call-1", {})
        result = await execute("save_voice_mail_message", "call-1", {"message": "Only text"})
        self.assertEqual(result["status"], "invalid")
        mongo.save_voice_mail_message.assert_not_called()
        mongo.insert_meeting.assert_not_called()

    async def test_a_caller_who_gave_no_name_can_still_be_helped(self):
        # The tool tells the model to send "" when no name was given; that
        # used to be rejected, failing every ask, message and call back.
        question = await self.executor()("ask_samarth", "q1", {"question": "Free Friday?",
                                                               "caller_name": ""})
        self.assertEqual(question["status"], "asked")
        relay = await self.executor()("send_messages_to_samarth", "r1",
                                      {"caller_name": " ", "message": "Running late"})
        self.assertEqual(relay["status"], "saved")
        self.assertIn("Phone message from a caller: Running late",
                      started_jobs("discord.send")[0]["args"]["content"])
        when = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        booked = await self.executor()("schedule_callback", "s1", {
            "caller_name": "", "phone_number": "+16175550123", "when": when, "reason": ""})
        self.assertEqual(booked["status"], "scheduled")
        self.assertTrue(main.callbacks.get(booked["callback_id"])["voicemail"].startswith("Hi, this is Luma"))

    async def test_a_missing_argument_is_named_so_the_model_can_ask_for_it(self):
        result = await self.executor()("send_messages_to_samarth", "r1",
                                       {"caller_name": "Alice", "message": "  "})
        self.assertEqual(result["status"], "invalid")
        self.assertIn("message", result["message"])
        self.assertIn("Ask the caller", result["message"])
        mongo.save_relayed_message.assert_not_called()

    async def test_question_remembers_which_call_asked(self):
        asked = []
        result = await self.executor(asked=asked)(
            "ask_samarth", "q1", {"question": "Free Friday?", "caller_name": "Alice"})
        self.assertEqual(asked, [result["question_id"]])
        self.assertEqual(main.questions.get(result["question_id"])["origin"], CHANNEL)

    async def test_check_task_reports_this_calls_own_background_job(self):
        job_id = main.jobs.start("email.send", {"to": "a@example.com"}, origin=CHANNEL,
                                 label="emailing the invite")
        result = await self.executor()("check_task", "t1", {"task_id": job_id})
        self.assertEqual((result["status"], result["task"]), ("queued", "emailing the invite"))
        other = main.jobs.start("email.send", {"to": "b@example.com"}, origin="call:CAother",
                                label="emailing someone else")
        for task_id in (other, "not-a-task"):
            result = await self.executor()("check_task", "t2", {"task_id": task_id})
            self.assertEqual(result["status"], "unknown")
            self.assertNotIn("someone else", json.dumps(result))

    async def test_another_calls_question_is_not_revealed(self):
        other = main.questions.ask("Private?", "Bob", "call:CAother")
        main.questions.answer(other, "Secret answer")
        result = await self.executor()("check_samarth_reply", "q1", {"question_id": other})
        self.assertEqual(result["status"], "unknown")
        mine = await self.executor()("ask_samarth", "q2", {"question": "Free?", "caller_name": "A"})
        result = await self.executor()("check_samarth_reply", "q3", {"question_id": mine["question_id"]})
        self.assertEqual(result["status"], "waiting")

    async def test_callbacks_only_go_to_us_and_canadian_numbers(self):
        question = await self.executor()("ask_samarth", "q1", {"question": "Free?", "caller_name": "A"})
        for number in ("+442079460958", "+18765550123"):
            with self.subTest(number=number):
                result = await self.executor()("request_callback", "c1", {
                    "question_id": question["question_id"], "caller_name": "A", "phone_number": number})
                self.assertEqual(result["status"], "refused")
        self.assertIsNone(main.questions.get(question["question_id"])["callback_state"])

    async def test_callback_is_booked_for_the_time_the_caller_chose(self):
        when = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
        result = await self.executor()("schedule_callback", "s1", {
            "caller_name": "Alice", "phone_number": "+16175550123",
            "when": when.isoformat(), "reason": "the interview slot"})
        self.assertEqual(result["status"], "scheduled")
        record = main.callbacks.get(result["callback_id"])
        self.assertEqual(record["due_at"], when.timestamp())
        self.assertIn("the interview slot", record["purpose"])
        self.assertIn("the interview slot", record["voicemail"])
        self.assertIn("Alice", started_jobs("discord.send")[0]["args"]["content"])

    async def test_callback_times_are_checked(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        cases = {"past": past, "no timezone": "2030-01-01T10:00:00"}
        for label, when in cases.items():
            with self.subTest(label):
                result = await self.executor()("schedule_callback", "s1", {
                    "caller_name": "A", "phone_number": "+16175550123", "when": when, "reason": "x"})
                self.assertEqual(result["status"], "refused")
                self.assertTrue(result["message"])

    async def test_outbound_call_answer_is_told_to_the_chat_that_asked(self):
        chat = "chat:" + "a" * 32
        execute = self.executor({"name": "Bob", "message": "Hiring?", "origin": chat,
                                 "call_sid": "CA9"})
        await execute("save_reponse_from_caller", "r1", {"response": "Yes, two roles"})
        ((_, kind, data),) = main.events.read(chat)
        self.assertEqual((kind, data["name"], data["response"], data["call_sid"]),
                         ("call.response", "Bob", "Yes, two roles", "CA9"))
        worker.enqueue_chat_followup.assert_called_once_with("a" * 32)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        reset()
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

    def context_of(self, response):
        token = ElementTree.fromstring(response.text).find("./Connect/Stream/Parameter").attrib["value"]
        return main.contexts.take(token)

    def test_only_a_well_formed_origin_reaches_the_call_context(self):
        chat = "chat:" + "a" * 32
        good = self.client.post("/incoming-call", params={"script": "2", "origin": chat})
        bad = self.client.post("/incoming-call", params={"script": "2", "origin": "events:../x"})
        self.assertEqual(self.context_of(good)["origin"], chat)
        self.assertNotIn("origin", self.context_of(bad))

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
        self.assertNotIn("status_callback", kwargs)

    def test_outbound_call_for_a_chat_reports_back_to_it(self):
        chat = "chat:" + "a" * 32
        with patch.object(main.twilio_client.calls, "create", return_value=SimpleNamespace(sid="CA-1")) as create:
            self.client.post("/start-calls", json={"numbers": ["+15555550100"], "name": "Bob",
                                                  "origin": chat})
        kwargs = create.call_args.kwargs
        self.assertEqual(parse_qs(urlsplit(kwargs["url"]).query)["origin"], [chat])
        status = parse_qs(urlsplit(kwargs["status_callback"]).query)
        self.assertEqual((status["origin"], status["name"]), ([chat], ["Bob"]))
        self.assertEqual(kwargs["status_callback_event"], ["completed"])

    def test_unanswered_outbound_call_is_told_to_its_chat(self):
        chat = "chat:" + "a" * 32
        for status in ("no-answer", "completed"):
            self.client.post("/call-status", params={"origin": chat, "name": "Bob"},
                             data={"CallSid": "CA1", "CallStatus": status})
        ((_, kind, data),) = main.events.read(chat)
        self.assertEqual((kind, data["status"], data["name"]), ("call.status", "no-answer", "Bob"))
        worker.enqueue_chat_followup.assert_called_once_with("a" * 32)

    def test_typed_numbers_are_normalised_to_e164(self):
        cases = {
            "857-707-1671": "+18577071671",
            "(857) 707 1671": "+18577071671",
            "857.707.1671": "+18577071671",
            "1 857 707 1671": "+18577071671",
            "+44 20 7946 0958": "+442079460958",
            "+18577071671": "+18577071671",
        }
        for typed, expected in cases.items():
            self.assertEqual(main.to_e164(typed), expected, typed)
        for bad in ("707-1671", "12345", "call me", "", None, 8577071671):
            self.assertIsNone(main.to_e164(bad), bad)

    def test_outbound_call_dials_a_formatted_number_in_e164(self):
        with patch.object(main.twilio_client.calls, "create", return_value=SimpleNamespace(sid="CA-1")) as create:
            self.client.post("/start-calls", json={"numbers": ["(857) 707-1671"], "name": "A"})
        self.assertEqual(create.call_args.kwargs["to"], "+18577071671")

    def test_a_single_number_string_is_one_call_not_one_per_character(self):
        with patch.object(main.twilio_client.calls, "create", return_value=SimpleNamespace(sid="CA-1")) as create:
            response = self.client.post("/start-calls", json={"numbers": "+18577071671", "name": "A"})
        self.assertEqual(create.call_count, 1)
        self.assertEqual(response.json()["calls"][0]["sid"], "CA-1")

    def test_an_invalid_number_is_rejected_before_reaching_twilio(self):
        with patch.object(main.twilio_client.calls, "create") as create:
            response = self.client.post("/start-calls", json={"numbers": ["707-1671"], "name": "A"})
        create.assert_not_called()
        self.assertIn("country code", response.json()["calls"][0]["error"])

    def test_twilio_rejection_is_reported_with_its_reason(self):
        rejected = Exception("HTTP 400")
        rejected.code, rejected.msg = 13223, "Invalid phone number format"
        with patch.object(main.twilio_client.calls, "create", side_effect=rejected), \
                self.assertLogs("main", level="WARNING") as logs:
            response = self.client.post("/start-calls", json={"numbers": ["+18577071671"], "name": "A"})
        result = response.json()["calls"][0]
        self.assertEqual(result["twilio_code"], 13223)
        self.assertIn("Invalid phone number format", result["error"])
        self.assertIn("code=13223", logs.output[0])


class CallbackEndpointTests(unittest.TestCase):
    def setUp(self):
        reset()
        self.client = TestClient(main.app)
        self.record = main.callbacks.schedule(
            "+16175550123", "Alice", purpose="You asked: Free Friday? Samarth's answer is: Yes",
            voicemail="Hi Alice, Samarth says yes.", now=time.time())
        (self.record,) = main.callbacks.claim_due(now=time.time() + 86400)
        main.callbacks.dialed(self.record["id"], "CA1")

    def test_a_person_answering_is_connected_to_the_agent_with_the_reason(self):
        response = self.client.post("/callback-call", params={"cb": self.record["id"]},
                                    data={"AnsweredBy": "human"})
        token = ElementTree.fromstring(response.text).find("./Connect/Stream/Parameter").attrib["value"]
        context = main.contexts.take(token)
        self.assertEqual((context["script"], context["name"], context["callback_id"]),
                         ("3", "Alice", self.record["id"]))
        self.assertIn("Samarth's answer is: Yes", context["message"])

    def test_a_machine_gets_the_voicemail_instead(self):
        response = self.client.post("/callback-call", params={"cb": self.record["id"]},
                                    data={"AnsweredBy": "machine_end_beep"})
        xml = ElementTree.fromstring(response.text)
        self.assertEqual(xml.find("Say").text, "Hi Alice, Samarth says yes.")
        self.assertIsNone(xml.find("Connect"))
        self.assertEqual(main.callbacks.get(self.record["id"])["answered_by"], "machine_end_beep")

    def test_an_unknown_call_back_just_hangs_up(self):
        for cb in ("f" * 32, "../../etc"):
            xml = ElementTree.fromstring(self.client.post("/callback-call", params={"cb": cb}).text)
            self.assertIsNone(xml.find("Connect"))
            self.assertIsNotNone(xml.find("Hangup"))

    def test_a_missed_call_back_is_retried_and_samarth_is_told(self):
        self.client.post("/callback-status", params={"cb": self.record["id"]},
                         data={"CallSid": "CA1", "CallStatus": "no-answer"})
        record = main.callbacks.get(self.record["id"])
        self.assertEqual((record["state"], record["outcome"]), ("scheduled", "retrying"))
        (notice,) = started_jobs("discord.send")
        self.assertIn("Trying again", notice["args"]["content"])
        # Twilio repeating the same report changes nothing.
        self.client.post("/callback-status", params={"cb": self.record["id"]},
                         data={"CallSid": "CA1", "CallStatus": "no-answer"})
        self.assertEqual(len(started_jobs("discord.send")), 1)

    def test_an_answered_call_back_is_done(self):
        self.client.post("/callback-status", params={"cb": self.record["id"]},
                         data={"CallSid": "CA1", "CallStatus": "completed", "AnsweredBy": "human"})
        record = main.callbacks.get(self.record["id"])
        self.assertEqual((record["state"], record["outcome"]), ("done", "answered"))
        self.assertIn("picked up", started_jobs("discord.send")[0]["args"]["content"])


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def phone(self, token, call_sid="CA" + "2" * 32):
        start = {"streamSid": "MZ-test", "callSid": call_sid,
                 "customParameters": {"context_id": token},
                 "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}

        async def incoming():
            yield json.dumps({"event": "connected"})
            yield json.dumps({"event": "start", "start": start})

        return SimpleNamespace(accept=AsyncMock(), close=AsyncMock(), iter_text=incoming)

    async def test_route_waits_for_twilio_start_and_connects_to_live(self):
        token = main.contexts.put({"script": "1", "name": "Alice", "message": ""})
        phone = self.phone(token)
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

    async def test_each_call_gets_its_own_event_channel_kept_out_of_the_prompt(self):
        chat = "chat:" + "a" * 32
        token = main.contexts.put({"script": "2", "name": "Bob", "message": "Hiring?", "origin": chat})
        with patch.object(main.websockets, "connect", return_value=AsyncMock()), \
                patch.object(main, "LiveBridge") as bridge, \
                patch.object(main, "make_tool_executor") as executor, \
                patch.object(main, "watch_call", new=AsyncMock()) as watch, \
                patch.object(main, "finish_call", new=AsyncMock()) as finish:
            bridge.return_value.run = AsyncMock()
            await main.handle_stream(self.phone(token))
        channel = "call:CA" + "2" * 32
        context = executor.call_args.args[0]
        self.assertEqual((context["channel"], context["origin"]), (channel, chat))
        self.assertEqual(watch.call_args.args[1], channel)
        finish.assert_awaited_once()
        instructions = bridge.call_args.args[3]["delegation"]["responses"]["instructions"]
        self.assertIn("Hiring?", instructions)
        self.assertNotIn(chat, instructions)


if __name__ == "__main__":
    unittest.main()
