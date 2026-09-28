import json
import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import timezones

from callbacks import CallbackStore
from events import EventStream
from memory_redis import MemoryRedis
from worker_modules import load

chat_agent = load("chat_agent")
SESSION = "b" * 32
CHAT = "chat:" + SESSION


def reply(text):
    return SimpleNamespace(output_text=text, output=[{
        "type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}]}])


def calls(*items):
    return SimpleNamespace(output_text="", output=[
        {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name,
         "arguments": json.dumps(args), "status": "completed"}
        for call_id, name, args in items])


class FakeModel:
    """Plays back scripted responses; a callable step sees the request."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        step = self.steps.pop(0)
        return step(kwargs) if callable(step) else step

    def inputs(self, index=-1):
        return self.requests[index]["input"]


def build_agent(model, redis, profile=None, save_meeting=None, enqueue_job=None, verifier=None):
    return chat_agent.ChatAgent(
        SimpleNamespace(responses=model), "gpt-test", redis,
        profile=profile or Mock(return_value={}), save_meeting=save_meeting or Mock(),
        check_password=lambda password: password == "open sesame",
        meeting_url=lambda: "https://meet.jit.si/test", samarth_email="s@example.com",
        enqueue_job=enqueue_job or Mock(), verifier=verifier,
        callbacks=CallbackStore(redis))


class FakeVerifier:
    def __init__(self, code="123456"):
        self.code = code
        self.sent = []

    def send(self, number):
        self.sent.append(number)

    def check(self, number, code):
        return code == self.code


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.redis = MemoryRedis()
        self.enqueue = Mock()
        self.profile = Mock(return_value={"name": "Samarth", "skills": ["Python"]})
        self.save_meeting = Mock(return_value="meeting-1")

    def agent(self, *steps):
        self.model = FakeModel(*steps)
        return build_agent(self.model, self.redis, profile=self.profile,
                           save_meeting=self.save_meeting, enqueue_job=self.enqueue)

    def jobs(self, kind=None):
        records = [json.loads(v) for k, v in self.redis.values.items() if k.startswith("job:")]
        return sorted((r for r in records if kind is None or r["kind"] == kind),
                      key=lambda r: r["created_at"])

    def outputs(self, request_index=-1):
        return {item["call_id"]: json.loads(item["output"])
                for item in self.model.inputs(request_index) if item.get("type") == "function_call_output"}

    def test_a_plain_answer_is_kept_server_side(self):
        agent = self.agent(reply("Hello! How can I help?"))
        result = agent.respond(SESSION, "Hi")
        self.assertEqual((result["output"], result["pending"], result["updates"]),
                         ("Hello! How can I help?", 0, []))
        request = self.model.requests[0]
        self.assertEqual(request["instructions"], chat_agent.SYSTEM_PROMPT)
        self.assertTrue(request["parallel_tool_calls"])
        items = agent.load(SESSION)["items"]
        self.assertEqual([i.get("role") for i in items], ["user", "assistant"])
        # The next turn replays the history, not a copy the browser could edit.
        self.agent(reply("Sure.")).respond(SESSION, "Thanks")
        self.assertEqual(len(self.model.inputs()), 3)

    def test_several_tools_asked_for_at_once_run_at_once(self):
        barrier = threading.Barrier(2, timeout=2)

        def profile():
            barrier.wait()          # only passes if both calls run together
            return {"name": "Samarth"}

        self.profile.side_effect = profile
        agent = self.agent(calls(("c1", "query_profile_info", {}), ("c2", "query_profile_info", {})),
                           reply("He knows Python."))
        self.assertEqual(agent.respond(SESSION, "Is he a fit?")["output"], "He knows Python.")
        self.assertEqual(self.outputs(), {"c1": {"name": "Samarth"}, "c2": {"name": "Samarth"}})
        order = [i["call_id"] for i in self.model.inputs() if i.get("type") == "function_call_output"]
        self.assertEqual(order, ["c1", "c2"])

    def test_the_model_can_keep_going_until_it_has_an_answer(self):
        agent = self.agent(
            calls(("c1", "query_profile_info", {})),
            calls(("c2", "ask_samarth", {"question": "Free Friday for a call?", "visitor_name": "Ann"})),
            reply("I've asked him and will update you here."))
        result = agent.respond(SESSION, "Can he talk Friday?")
        self.assertEqual(len(self.model.requests), 3)
        self.assertEqual(result["pending"], 1)
        question = agent.questions.get(self.outputs()["c2"]["question_id"])
        self.assertEqual((question["origin"], question["caller_name"]), (CHAT, "Ann"))

    def test_a_turn_always_ends_in_words(self):
        def step(request):
            if request.get("tool_choice") == "none":
                return reply("Here's what I found.")
            return calls((f"c{len(self.model.requests)}", "query_profile_info", {}))

        agent = self.agent(*[step] * chat_agent.MAX_STEPS)
        self.assertEqual(agent.respond(SESSION, "Tell me everything")["output"], "Here's what I found.")
        self.assertEqual(len(self.model.requests), chat_agent.MAX_STEPS)

    def test_a_failing_tool_is_reported_to_the_model_not_raised(self):
        self.profile.side_effect = RuntimeError("mongo down")
        agent = self.agent(calls(("c1", "query_profile_info", {})), reply("I couldn't look that up."))
        self.assertEqual(agent.respond(SESSION, "Skills?")["output"], "I couldn't look that up.")
        self.assertEqual(self.outputs()["c1"]["status"], "error")
        self.assertNotIn("mongo", json.dumps(self.outputs()))

    def ask(self, agent):
        agent.respond(SESSION, "Is he free Friday?")
        return next(iter(agent.load(SESSION)["pending"]))

    def test_samarths_reply_becomes_a_message_in_the_chat(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free Friday?", "visitor_name": ""})),
                           reply("I've asked him."), reply("Good news: he's free Friday after 2pm."))
        question_id = self.ask(agent)
        agent.questions.pop_for_posting()
        agent.questions.answer(question_id, "Yes, after 2pm")
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        self.assertEqual(agent.follow_up(SESSION), "Good news: he's free Friday after 2pm.")
        update = self.model.inputs()[-1]
        self.assertEqual(update["role"], "developer")
        self.assertIn('"Yes, after 2pm"', update["content"][0]["text"])
        self.assertIn("not from the visitor", update["content"][0]["text"])
        (message,), _ = agent.messages_after(SESSION)
        self.assertEqual((message["text"], message["pending"]),
                         ("Good news: he's free Friday after 2pm.", 0))
        # News is taken once: running again has nothing to say.
        self.assertIsNone(agent.follow_up(SESSION))
        self.assertEqual(len(self.model.requests), 3)

    def test_the_news_says_when_a_call_back_was_booked(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free?", "visitor_name": ""})),
                           reply("Asked."), reply("He's free; we're calling you."))
        question_id = self.ask(agent)
        agent.questions.request_callback(question_id, "Ann", "+16175550123")
        agent.questions.answer(question_id, "Yes")
        agent.questions.claim_callback(question_id)          # the listener booked it
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        agent.follow_up(SESSION)
        self.assertIn("a call back with his answer has been booked",
                      self.model.inputs()[-1]["content"][0]["text"])

    def test_the_news_says_when_a_call_back_couldnt_be_booked(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free?", "visitor_name": ""})),
                           reply("Asked."), reply("He's free, but I couldn't book the call."))
        question_id = self.ask(agent)
        agent.questions.request_callback(question_id, "Ann", "+16175550123")
        agent.questions.answer(question_id, "Yes")
        with self.assertRaises(ValueError):          # as the listener tried to book it
            agent.questions.book_answer_call(question_id, Mock(schedule=Mock(side_effect=ValueError("no"))))
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        agent.follow_up(SESSION)
        self.assertIn("couldn't be booked", self.model.inputs()[-1]["content"][0]["text"])

    def test_news_not_yet_told_is_part_of_the_next_turn(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free?", "visitor_name": ""})),
                           reply("Asked."), reply("He says yes, and to your question: no."))
        question_id = self.ask(agent)
        agent.questions.answer(question_id, "Yes")
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        agent.respond(SESSION, "Also, is he remote?")
        roles = [i.get("role") for i in self.model.inputs() if i.get("role")]
        self.assertEqual(roles[-2:], ["developer", "user"])

    def test_messages_the_browser_missed_come_back_with_the_next_answer(self):
        agent = self.agent(reply("Anything else?"))
        missed = agent.events.publish(CHAT, "chat.message", text="He replied: yes.", pending=0)
        agent.events.publish(CHAT, "job.done", job={"id": "x"})
        result = agent.respond(SESSION, "Hello?", cursor=chat_agent.START)
        self.assertEqual(result["updates"], [{"id": missed, "type": "message",
                                              "text": "He replied: yes.", "pending": 0}])
        self.assertEqual(result["cursor"], agent.events.latest(CHAT))
        seen = self.agent(reply("Ok.")).respond(SESSION, "Ok", cursor=result["cursor"])
        self.assertEqual(seen["updates"], [])

    def test_meeting_invite_is_sent_in_the_background_and_reported(self):
        args = {"members": ["bob@example.com"], "agenda": "Intro", "user_email": "ann@example.com",
                "timing": "2026-10-02T15:00:00-04:00", "timezone": "America/New_York"}
        agent = self.agent(calls(("c1", "schedule_meeting_on_jitsi", args)), reply("Booked!"))
        result = agent.respond(SESSION, "Book it")
        self.assertEqual(self.outputs()["c1"]["status"], "saved")
        self.save_meeting.assert_called_once_with(
            ["bob@example.com", "samarth@samarthmahendra.com"], "Intro", args["timing"],
            "https://meet.jit.si/test")
        invite, copy, notice = self.jobs()
        self.assertEqual((invite["args"]["to"], invite["origin"], invite["announce"]),
                         ("ann@example.com", CHAT, "always"))
        self.assertEqual((copy["args"]["to"], copy["announce"], notice["announce"]),
                         ("s@example.com", "never", "never"))
        self.assertEqual(result["pending"], 1)

    def test_meeting_details_are_checked_first(self):
        bad = [{"user_email": "not-an-email", "timing": "2026-10-02T15:00:00-04:00"},
               {"user_email": "ann@example.com", "timing": "2026-10-02T15:00:00"},
               {"user_email": "ann@example.com", "timing": "Friday"}]
        for fields in bad:
            with self.subTest(fields=fields):
                args = dict({"members": [], "agenda": "Intro"}, **fields)
                self.agent(calls(("c1", "schedule_meeting_on_jitsi", args)), reply("?")).respond(SESSION, "Book")
                self.assertEqual(self.outputs()["c1"]["status"], "refused")
        self.save_meeting.assert_not_called()

    def test_calls_need_the_password(self):
        args = {"numbers": ["+16175550123"], "name": "Bob", "message": "Hiring?", "password": "guess"}
        self.agent(calls(("c1", "make_calls", args)), reply("Sorry.")).respond(SESSION, "Call Bob")
        self.assertEqual(self.outputs()["c1"]["status"], "refused")
        self.assertEqual(self.jobs(), [])

    def test_calls_report_back_as_they_are_placed_and_answered(self):
        args = {"numbers": ["+16175550123"], "name": "Bob", "message": "Hiring?", "password": "open sesame"}
        agent = self.agent(calls(("c1", "make_calls", args)), reply("Calling now."),
                           reply("The call to Bob is placed."), reply("Bob says yes, two roles."))
        agent.respond(SESSION, "Call Bob")
        (job,) = self.jobs("calls.place")
        self.assertEqual((job["args"]["origin"], job["origin"]), (CHAT, CHAT))
        job.update(status="done", result={"placed": [{"to": "+16175550123", "sid": "CA9"}], "failed": []})
        agent.events.publish(CHAT, "job.done", job=job)
        agent.follow_up(SESSION)
        self.assertEqual(agent.pending_for(SESSION), 1)      # now waiting on Bob's answer
        agent.events.publish(CHAT, "call.response", name="Bob", message="Hiring?",
                             response="Yes, two roles", call_sid="CA9")
        self.assertEqual(agent.follow_up(SESSION), "Bob says yes, two roles.")
        self.assertIn('"Yes, two roles"', self.model.inputs()[-1]["content"][0]["text"])
        self.assertEqual(agent.pending_for(SESSION), 0)

    def test_status_checks_see_only_this_chats_tasks(self):
        agent = self.agent()
        mine = agent.jobs.start("email.send", {}, origin=CHAT, label="emailing the invite")
        other = agent.jobs.start("email.send", {}, origin="chat:" + "c" * 32, label="someone else's")
        theirs = agent.questions.ask("Private?", "Bob", "chat:" + "c" * 32)
        session = agent.load(SESSION)
        self.assertEqual(agent.tool_check({"task_id": mine}, session)["task"], "emailing the invite")
        for task_id in (other, theirs):
            self.assertEqual(agent.tool_check({"task_id": task_id}, session)["status"], "unknown")

    def test_an_answer_whose_event_was_lost_is_still_told_once(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free?", "visitor_name": ""})),
                           reply("Asked."), reply("He says yes."))
        question_id = self.ask(agent)
        self.assertFalse(agent.has_news(SESSION))
        agent.questions.answer(question_id, "Yes")          # no event published
        self.assertTrue(agent.has_news(SESSION))
        self.assertEqual(agent.follow_up(SESSION), "He says yes.")
        self.assertEqual(agent.pending_for(SESSION), 0)
        # The event turning up late doesn't tell it a second time.
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        self.assertIsNone(agent.follow_up(SESSION))
        self.assertFalse(agent.has_news(SESSION))
        self.assertEqual(len(self.model.requests), 3)

    def test_times_default_to_the_visitors_browser_zone(self):
        meeting = {"members": [], "agenda": "Intro", "timing": "2026-11-05T14:00", "timezone": "",
                   "user_email": "ann@example.com"}
        agent = self.agent(calls(("c1", "get_current_time", {"timezone": ""}),
                                 ("c2", "schedule_meeting_on_jitsi", meeting)), reply("Booked."))
        agent.respond(SESSION, "Book 2pm on November 5", timezone="America/Los_Angeles")
        outputs = self.outputs()
        self.assertEqual(outputs["c1"]["timezone"], "America/Los_Angeles")
        self.assertEqual(self.save_meeting.call_args.args[2], "2026-11-05T14:00:00-08:00")
        self.assertEqual(outputs["c2"]["samarth_time"], "Thursday 2:00 PM PST (5:00 PM EST), November 5")
        self.assertIn("browser is set to America/Los_Angeles", self.model.requests[0]["instructions"])

    def test_a_meeting_time_without_a_zone_is_refused_when_none_is_known(self):
        meeting = {"members": [], "agenda": "Intro", "timing": "2026-11-05T14:00", "timezone": "",
                   "user_email": "ann@example.com"}
        agent = self.agent(calls(("c1", "schedule_meeting_on_jitsi", meeting)), reply("Which zone?"))
        agent.respond(SESSION, "Book it", timezone="Nowhere/Land")     # nonsense is ignored
        self.assertEqual(self.outputs()["c1"]["status"], "refused")
        self.assertIn("timezone", self.outputs()["c1"]["message"])
        self.assertNotIn("browser is set", self.model.requests[0]["instructions"])

    @staticmethod
    def tomorrow_at(hour, zone_name="America/Los_Angeles"):
        day = datetime.now(ZoneInfo(zone_name)) + timedelta(days=1)
        return day.replace(hour=hour, minute=0, second=0, microsecond=0,
                           tzinfo=None).isoformat(timespec="minutes")

    def callback_args(self, **fields):
        return dict({"visitor_name": "Ann", "phone_number": "+14155550123",
                     "when": self.tomorrow_at(15), "timezone": "", "reason": "the internship"}, **fields)

    def test_a_visitor_can_book_a_call_at_a_time_on_their_clock(self):
        args = self.callback_args()
        agent = self.agent(calls(("c1", "schedule_callback", args)), reply("Booked."))
        agent.respond(SESSION, "Call me tomorrow at 3pm", timezone="America/Los_Angeles")
        result = self.outputs()["c1"]
        self.assertEqual((result["status"], result["timezone"]), ("scheduled", "America/Los_Angeles"))
        moment = timezones.resolve(args["when"], "America/Los_Angeles")
        self.assertEqual(result["when"], timezones.readable(moment))
        record = agent.callbacks.get(result["callback_id"])
        self.assertEqual((record["origin"], record["source"], record["due_at"]),
                         (CHAT, "chat", moment.timestamp()))
        self.assertIn("the internship", record["purpose"])
        (notice,) = self.jobs("discord.send")
        # Samarth sees the visitor's time and his own.
        self.assertIn(timezones.also_in(moment, "America/New_York"), notice["args"]["content"])
        self.assertRegex(notice["args"]["content"], r"3:00 PM P[DS]T \(6:00 PM E[DS]T\)")

    def test_a_call_back_when_samarth_answers_rides_on_the_question(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free?", "visitor_name": "Ann"})),
                           reply("Asked."))
        agent.respond(SESSION, "Ask him", timezone="America/Chicago")
        question_id = next(iter(agent.load(SESSION)["pending"]))
        self.agent(calls(("c2", "request_callback", {
            "question_id": question_id, "visitor_name": "Ann", "phone_number": "+16175550123",
            "timezone": ""})), reply("Sure.")).respond(SESSION, "I have to go, call me")
        self.assertEqual(self.outputs()["c2"]["status"], "callback_requested")
        record = agent.questions.get(question_id)
        self.assertEqual((record["callback_number"], record["callback_timezone"], record["callback_state"]),
                         ("+16175550123", "America/Chicago", "requested"))

    def answered_question(self):
        agent = self.agent(calls(("c1", "ask_samarth", {"question": "Free for a call?", "visitor_name": "John"})),
                           reply("Asked."))
        question_id = self.ask(agent)
        agent.questions.answer(question_id, "Yeah I am available")
        agent.events.publish(CHAT, "question.answered", question_id=question_id)
        return question_id

    def late_request(self, question_id):
        return self.agent(calls(("c2", "request_callback", {
            "question_id": question_id, "visitor_name": "John", "phone_number": "+16175550123",
            "timezone": "America/New_York"})), reply("Done."))

    def test_a_call_back_asked_for_once_he_has_answered_rings_a_visitor_who_left(self):
        # What happened: the model waited for his reply before asking for the
        # call, and the tool used to refuse because the question was answered.
        question_id = self.answered_question()
        self.late_request(question_id).follow_up(SESSION)
        self.assertEqual(self.outputs()["c2"]["status"], "calling")
        (booked,) = [json.loads(v) for k, v in self.redis.values.items() if k.startswith("callback:")]
        self.assertEqual((booked["to"], booked["question_id"], booked["origin"]),
                         ("+16175550123", question_id, CHAT))
        self.assertIn("Yeah I am available", booked["purpose"])
        (notice,) = self.jobs("discord.send")
        self.assertIn("Booked from the website chat", notice["args"]["content"])
        # Asked again, it doesn't ring twice.
        self.late_request(question_id).respond(SESSION, "Call me")
        self.assertEqual(self.outputs()["c2"]["status"], "already_booked")

    def test_a_late_call_back_request_for_a_visitor_still_reading_is_answered_here(self):
        question_id = self.answered_question()
        EventStream(self.redis).mark_live(CHAT)
        self.late_request(question_id).follow_up(SESSION)
        result = self.outputs()["c2"]
        self.assertEqual((result["status"], result["reply"]), ("answered", "Yeah I am available"))
        self.assertEqual([k for k in self.redis.values if k.startswith("callback:")], [])

    def test_a_call_back_asked_for_before_the_question_covers_it(self):
        agent = self.agent(calls(("c1", "request_callback", {
            "question_id": "", "visitor_name": "John", "phone_number": "+16175550123", "timezone": ""})),
            reply("I'll ask him."))
        agent.respond(SESSION, "Call me when he replies")
        self.assertEqual(self.outputs()["c1"]["status"], "callback_requested")
        question_id = self.ask(self.agent(calls(("c2", "ask_samarth", {"question": "Free?", "visitor_name": "John"})),
                                          reply("Asked.")))
        wanted = agent.questions.callback_request(agent.questions.get(question_id))
        self.assertEqual(wanted["number"], "+16175550123")

    def test_samarths_own_number_has_no_chat_limit(self):
        for hour in range(chat_agent.MAX_CALLBACKS_PER_CHAT + 2):
            args = self.callback_args(when=self.tomorrow_at(10 + hour, "America/New_York"),
                                      phone_number="+18577071671", timezone="America/New_York")
            self.agent(calls(("c1", "schedule_callback", args)), reply("Ok.")).respond(SESSION, "Call me")
            self.assertEqual(self.outputs()["c1"]["status"], "scheduled")

    def test_call_backs_are_limited_to_this_chat_and_real_numbers(self):
        other = self.agent().questions.ask("Private?", "Bob", "chat:" + "c" * 32)
        cases = [("request_callback", {"question_id": other, "visitor_name": "", "phone_number": "+16175550123",
                                       "timezone": ""}),
                 ("schedule_callback", self.callback_args(phone_number="+442079460958")),
                 ("schedule_callback", self.callback_args(phone_number="+18005550123"))]   # zone unknown
        for name, args in cases:
            with self.subTest(name=name, args=args):
                self.agent(calls(("c1", name, args)), reply("No.")).respond(SESSION, "Call me")
                self.assertEqual(self.outputs()["c1"]["status"], "refused")
        self.assertEqual([k for k in self.redis.values if k.startswith("callback:")], [])

    def test_a_chat_can_only_book_a_few_calls(self):
        for hour in range(chat_agent.MAX_CALLBACKS_PER_CHAT + 1):
            args = self.callback_args(when=self.tomorrow_at(10 + hour),
                                      phone_number=f"+1415555012{hour}")
            self.agent(calls(("c1", "schedule_callback", args)), reply("Ok.")).respond(
                SESSION, "Call me", timezone="America/Los_Angeles")
        self.assertEqual(self.outputs()["c1"]["status"], "refused")
        self.assertIn("as many call backs", self.outputs()["c1"]["message"])

    def test_a_number_is_checked_by_text_before_it_can_be_rung(self):
        verifier = FakeVerifier()
        args = self.callback_args()

        def turn(*steps):
            self.model = FakeModel(*steps)
            build_agent(self.model, self.redis, verifier=verifier).respond(
                SESSION, "Call me", timezone="America/Los_Angeles")
            return self.outputs()

        self.assertEqual(turn(calls(("c1", "schedule_callback", args)), reply("Code?"))["c1"]["status"],
                         "code_needed")
        turn(calls(("c1", "schedule_callback", args)), reply("Code?"))
        self.assertEqual(verifier.sent, ["+14155550123"])           # not texted twice in a minute
        wrong = {"phone_number": "+14155550123", "code": "000000"}
        self.assertEqual(turn(calls(("c1", "verify_phone", wrong)), reply("Hm."))["c1"]["status"], "wrong_code")
        right = {"phone_number": "+14155550123", "code": "123 456"}
        self.assertEqual(turn(calls(("c1", "verify_phone", right)), reply("Ok."))["c1"]["status"], "verified")
        self.assertEqual(turn(calls(("c1", "schedule_callback", args)), reply("Booked."))["c1"]["status"],
                         "scheduled")

    def test_without_verification_codes_never_come_up(self):
        agent = self.agent(calls(("c1", "schedule_callback", self.callback_args())), reply("Booked."))
        agent.respond(SESSION, "Call me tomorrow at 3pm", timezone="America/Los_Angeles")
        self.assertEqual(self.outputs()["c1"]["status"], "scheduled")       # no code step
        request = self.model.requests[0]
        self.assertNotIn("verify_phone", [tool["name"] for tool in request["tools"]])
        self.assertNotIn("texted", request["instructions"])
        self.assertNotIn("verify_phone", request["instructions"])
        # A stray call still gets a sensible answer.
        stray = self.agent(calls(("c1", "verify_phone", {"phone_number": "+14155550123", "code": "1"})),
                           reply("Ok."))
        stray.respond(SESSION, "Here's my code")
        self.assertEqual(self.outputs()["c1"]["status"], "not_needed")

    def test_with_verification_the_code_step_is_explained_and_offered(self):
        self.model = FakeModel(reply("Hi."))
        build_agent(self.model, self.redis, verifier=FakeVerifier()).respond(SESSION, "Hi")
        request = self.model.requests[0]
        self.assertIn("verify_phone", [tool["name"] for tool in request["tools"]])
        self.assertIn("check it with verify_phone", request["instructions"])

    def test_one_turn_at_a_time(self):
        agent = self.agent()
        with agent.locked(SESSION):
            with self.assertRaises(chat_agent.Busy):
                with agent.locked(SESSION, wait=0.1):
                    pass
        with agent.locked(SESSION, wait=0.1):
            pass

    def test_session_ids(self):
        self.assertEqual(chat_agent.session_id_for(SESSION), SESSION)
        legacy = chat_agent.session_id_for(None, "user_ab12cd34")
        self.assertEqual(legacy, chat_agent.session_id_for("not-hex", "user_ab12cd34"))
        self.assertRegex(legacy, r"^[0-9a-f]{32}$")
        self.assertNotEqual(chat_agent.session_id_for(), chat_agent.session_id_for())

    def test_history_is_trimmed_at_the_start_of_a_turn(self):
        items = ([{"role": "user", "content": []}]
                 + [{"type": "function_call_output", "call_id": str(i), "output": "{}"} for i in range(5)]
                 + [{"role": "user", "content": []}, {"type": "message", "role": "assistant"}])
        trimmed = chat_agent.trim(items, limit=4)
        self.assertEqual(trimmed[0]["role"], "user")
        self.assertEqual(len(trimmed), 2)

    def test_server_sent_event_frames(self):
        self.assertEqual(chat_agent.sse_frame("message", "1-2", {"text": "hi"}),
                         'event: message\nid: 1-2\ndata: {"text": "hi"}\n\n')
        self.assertNotIn("id:", chat_agent.sse_frame("status", None, {"pending": 0}))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    """/chat/events: the visitor's own stream writes the follow-up message."""

    def setUp(self):
        self.redis = MemoryRedis()

    async def connected(self):
        return False

    async def frames(self, agent, lifetime=0.3):
        return [frame async for frame in agent.stream(SESSION, "0-0", self.connected,
                                                       lifetime=lifetime, tick=0.02)]

    def asked(self, *follow_ups):
        self.model = FakeModel(calls(("c1", "ask_samarth", {"question": "Zoom or Meet?", "visitor_name": ""})),
                               reply("I've asked him."), *follow_ups)
        agent = build_agent(self.model, self.redis)
        agent.respond(SESSION, "Ask which platform he prefers")
        return agent, next(iter(agent.load(SESSION)["pending"]))

    async def test_news_is_told_on_the_stream_without_the_worker(self):
        agent, question_id = self.asked(reply("He prefers Zoom."))
        agent.questions.answer(question_id, "Zoom")
        agent.events.publish(CHAT, "question.answered", question_id=question_id)   # as the listener does
        frames = await self.frames(agent)
        self.assertTrue(frames[1].startswith("event: status"))
        self.assertIn('"pending": 1', frames[1])
        (message,) = [f for f in frames if f.startswith("event: message")]
        self.assertIn('"text": "He prefers Zoom."', message)
        self.assertIn('"pending": 0', message)
        self.assertEqual(len(self.model.requests), 3)

    async def test_a_failing_follow_up_does_not_end_the_stream_or_spin(self):
        def fail(request):
            raise RuntimeError("model unavailable")

        agent, question_id = self.asked(fail, fail, fail)
        agent.questions.answer(question_id, "Zoom")
        with self.assertLogs("chat_agent", level="ERROR"):
            frames = await self.frames(agent, lifetime=0.4)
        self.assertEqual(len(self.model.requests), 3)       # tried once, then backed off
        self.assertTrue(frames[0].startswith("retry:"))
        # Nothing was lost: the next attempt still has the news to tell.
        self.assertTrue(agent.has_news(SESSION))

    async def test_an_open_chat_counts_as_live(self):
        agent, _ = self.asked()
        self.assertFalse(agent.events.is_live(CHAT))
        await self.frames(agent, lifetime=0.1)
        self.assertTrue(agent.events.is_live(CHAT))

    async def test_a_turn_in_progress_is_left_to_tell_it(self):
        agent, question_id = self.asked(reply("He prefers Zoom."))
        agent.questions.answer(question_id, "Zoom")
        with agent.locked(SESSION):
            during = await self.frames(agent, lifetime=0.2)
        self.assertFalse(any(f.startswith("event: message") for f in during))
        after = await self.frames(agent)                    # the turn has finished
        self.assertTrue(any("He prefers Zoom." in f for f in after))


if __name__ == "__main__":
    unittest.main()
