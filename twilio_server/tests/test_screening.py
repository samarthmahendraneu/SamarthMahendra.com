"""Spam and spoofing screening: webhook signals, reverse lookup, verdicts and the Discord report."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from xml.etree import ElementTree

import call_screening as cs
from memory_redis import MemoryRedis
import test_app
from test_app import CHANNEL, caller_client, main, memory, mongo, reset, started_jobs

OURS = "+18339703274"


class FakeLookup:
    """Twilio's client.lookups.v2.phone_numbers(n).fetch(fields=...), counting calls."""

    def __init__(self, line_type="mobile", carrier="Verizon Wireless", name="JANE DOE", valid=True, fail=False):
        self.calls = []
        self.info = SimpleNamespace(valid=valid, country_code="US", national_format="(617) 555-0123",
                                    line_type_intelligence={"type": line_type, "carrier_name": carrier},
                                    caller_name={"caller_name": name, "caller_type": "CONSUMER"})
        self.fail = fail
        self.lookups = SimpleNamespace(v2=SimpleNamespace(phone_numbers=self.phone_numbers))

    def phone_numbers(self, number):
        def fetch(fields):
            self.calls.append((number, fields))
            if self.fail:
                raise RuntimeError("lookup down")
            return self.info
        return SimpleNamespace(fetch=fetch)


class WebhookTests(unittest.TestCase):
    def test_clean_verified_mobile(self):
        s = cs.screen_webhook({"From": "+16175550123", "StirVerstat": "TN-Validation-Passed-A",
                               "FromCity": "BOSTON", "FromState": "MA"}, [OURS])
        self.assertEqual((s["number"], cs.level_for(s["score"]), s["spoofing"]), ("+16175550123", "low", []))
        self.assertEqual(s["location"], "Boston, MA")

    def test_withheld_caller_id(self):
        for raw in ("+266696687", "Anonymous", ""):
            s = cs.screen_webhook({"From": raw}, [OURS])
            self.assertIsNone(s["number"])
            self.assertIn("Caller ID withheld", s["reasons"][0])

    def test_failed_attestation_is_spoofing(self):
        s = cs.screen_webhook({"From": "+16175550123", "StirVerstat": "TN-Validation-Failed-B"}, [OURS])
        self.assertEqual(cs.level_for(s["score"]), "medium")
        self.assertTrue(any("STIR/SHAKEN" in x for x in s["spoofing"]))

    def test_our_own_number_and_neighbour_spoofing(self):
        own = cs.screen_webhook({"From": OURS}, [OURS])
        self.assertEqual(cs.level_for(own["score"]), "high")
        neighbour = cs.screen_webhook({"From": "+18339700000"}, [OURS])
        self.assertIn("neighbour spoofing pattern", neighbour["spoofing"])

    def test_spam_caller_name_and_bad_number(self):
        self.assertGreaterEqual(cs.screen_webhook({"From": "+16175550123", "CallerName": "SPAM LIKELY"})["score"], 40)
        self.assertIn("isn't a valid phone number", cs.screen_webhook({"From": "12345"})["reasons"][0])


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.redis = MemoryRedis()

    def test_twilio_lookup_is_cached(self):
        twilio = FakeLookup()
        screener = cs.CallScreener(self.redis, twilio)
        first = screener.lookup("(617) 555-0123")
        second = screener.lookup("+16175550123")
        self.assertEqual(first, second)
        self.assertEqual(len(twilio.calls), 1)
        self.assertEqual((first["line_type"], first["carrier"], first["registered_name"]),
                         ("mobile", "Verizon Wireless", "JANE DOE"))

    def test_ipqs_reputation_fills_gaps(self):
        get = Mock(return_value=Mock(json=Mock(return_value={
            "success": True, "fraud_score": 95, "spammer": True, "recent_abuse": True, "active": False,
            "line_type": "VOIP", "carrier": "Bandwidth", "name": "N/A"})))
        result = cs.CallScreener(self.redis, None, ipqs_key="k", http_get=get).lookup("+16175550124")
        self.assertEqual((result["line_type"], result["carrier"], result["reputation"]["fraud_score"]),
                         ("voip", "Bandwidth", 95))
        self.assertIn("16175550124", get.call_args.args[0])
        verdict = cs.assess({}, result)
        self.assertEqual(verdict["level"], "high")
        self.assertIn("caller ID belongs to an inactive number", verdict["spoofing"]["signals"])

    def test_failures_and_switches(self):
        self.assertEqual(cs.CallScreener(self.redis, FakeLookup(fail=True)).lookup("+16175550123")["status"],
                         "unavailable")
        self.assertEqual(cs.CallScreener(self.redis, FakeLookup(), lookup_enabled=False)
                         .lookup("+16175550123")["status"], "disabled")
        self.assertEqual(cs.CallScreener(self.redis, FakeLookup()).lookup("not a number")["status"], "invalid")


class VerdictTests(unittest.TestCase):
    SCAM = {"caller_name": "Officer Daniel Ross", "organization": "IRS", "department": "Criminal Investigation",
            "official_id": "CI-4471", "case_number": "TX-2026-88123", "callback_number": "+12025550199",
            "category": "government", "reason": "Unpaid back taxes",
            "demands": "Pay $2,400 in Google Play gift cards today or a warrant will be issued for your arrest",
            "other_details": "Refused to give an office address"}

    def test_government_gift_card_scam(self):
        screening = cs.screen_webhook({"From": "+16175550123", "StirVerstat": "TN-Validation-Passed-C"}, [OURS])
        lookup = {"status": "ok", "line_type": "nonFixedVoip", "carrier": "Bandwidth"}
        verdict = cs.assess(screening, lookup, self.SCAM)
        self.assertEqual(verdict["level"], "high")
        self.assertEqual(verdict["agency"][0], "IRS")
        for flag in ("asked for gift cards", "threatened arrest, deportation or suspension", "pressed for urgency"):
            self.assertIn(flag, verdict["red_flags"])
        self.assertIn("The callback number they gave differs from their caller ID", verdict["reasons"])
        text = cs.report_text(screening, verdict, lookup, self.SCAM, call_sid="CA123")
        for part in ("+16175550123", "Officer Daniel Ross", "IRS / Criminal Investigation", "CI-4471",
                     "TX-2026-88123", "+12025550199", "gift cards", "800-829-1040", "google.com/search", "CA123"):
            self.assertIn(part, text)
        self.assertLess(len(text), 2000)

    def test_recruiter_stays_low(self):
        screening = cs.screen_webhook({"From": "+14155550123", "StirVerstat": "TN-Validation-Passed-A"}, [OURS])
        claims = {"caller_name": "Priya", "organization": "Stripe", "category": "other",
                  "reason": "Scheduling a new grad interview"}
        self.assertEqual(cs.assess(screening, {"status": "ok", "line_type": "mobile"}, claims)["level"], "low")


class AppScreeningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        mongo.save_screening_report = Mock(return_value="screen-1")
        self.twilio = FakeLookup(line_type="nonFixedVoip", carrier="Bandwidth", name="")
        self.saved_screener = main.screener
        main.screener = cs.CallScreener(memory, self.twilio)

    async def asyncTearDown(self):
        main.screener = self.saved_screener

    def context_of(self, response):
        token = ElementTree.fromstring(response.text).find("./Connect/Stream/Parameter").attrib["value"]
        return main.contexts.take(token)

    def test_incoming_calls_are_screened_outbound_are_not(self):
        client = caller_client()
        inbound = self.context_of(client.post("/incoming-call", data={
            "From": "+16175550123", "To": OURS, "Direction": "inbound", "StirVerstat": "TN-Validation-Failed"}))
        self.assertEqual(cs.level_for(inbound["screening"]["score"]), "medium")
        outbound = self.context_of(client.post("/incoming-call", params={"script": "2"}, data={
            "From": OURS, "To": "+13125550123", "Direction": "outbound-api"}))
        self.assertNotIn("screening", outbound)
        voicemail = self.context_of(client.post("/voice-mail", data={"From": "Anonymous", "Direction": "inbound"}))
        self.assertIsNone(voicemail["screening"]["number"])

    async def test_lookup_tool_and_report_tool(self):
        call = {"channel": CHANNEL, "call_sid": "CA" + "1" * 32,
                "screening": cs.screen_webhook({"From": "+16175550123"}, [OURS])}
        execute = main.make_tool_executor(call)
        found = await execute("lookup_caller_number", "c1", {"phone_number": ""})
        self.assertEqual((found["line_type"], found["risk"]), ("nonFixedVoip", "low"))
        self.assertIn("note", found)
        args = dict(VerdictTests.SCAM)
        result = await execute("report_suspicious_call", "c2", args)
        self.assertEqual((result["status"], result["risk"], result["relay"]), ("reported", "high", "queued"))
        posted = started_jobs("discord.send")[-1]["args"]["content"]
        for part in ("Suspicious call screened", "+16175550123", "CI-4471", "TX-2026-88123", "nonFixedVoip"):
            self.assertIn(part, posted)
        saved = mongo.save_screening_report.call_args.args
        self.assertEqual((saved[0], saved[1]["claims"]["case_number"]), (call["call_sid"], "TX-2026-88123"))
        again = await execute("report_suspicious_call", "c3", args)
        self.assertEqual(again["status"], "already_reported")
        # The caller ID and the callback number they gave were each looked up once.
        self.assertEqual(sorted(n for n, _ in self.twilio.calls), ["+12025550199", "+16175550123"])

    async def test_partial_claims_are_accepted(self):
        execute = main.make_tool_executor({"channel": CHANNEL, "screening": cs.screen_webhook({"From": ""})})
        args = {key: "" for key in main.SCREENING_CLAIMS}
        args["reason"] = "Robocall about a car warranty"
        result = await execute("report_suspicious_call", "c1", args)
        self.assertEqual(result["status"], "reported")
        self.assertIn("withheld", started_jobs("discord.send")[-1]["args"]["content"])

    def test_risky_call_is_reported_at_hang_up_once(self):
        risky = {"call_sid": "CA9", "screening": cs.screen_webhook(
            {"From": "+16175550123", "StirVerstat": "TN-Validation-Failed"}, [OURS])}
        main.report_screened_call_end(risky)
        content = started_jobs("discord.send")[-1]["args"]["content"]
        self.assertIn("ended before details were collected", content)
        reset()
        main.report_screened_call_end(dict(risky, screening_reported=True))
        clean = {"call_sid": "CA8", "screening": cs.screen_webhook(
            {"From": "+16175550123", "StirVerstat": "TN-Validation-Passed-A"}, [OURS])}
        main.screener = cs.CallScreener(memory, FakeLookup())
        main.report_screened_call_end(clean)
        main.report_screened_call_end({"call_sid": "CA7"})
        self.assertEqual(started_jobs("discord.send"), [])

    def test_screening_reaches_the_model_and_the_tools_are_offered(self):
        names = {tool["name"] for tool in main.TOOLS}
        self.assertTrue({"lookup_caller_number", "report_suspicious_call"} <= names)
        config = main.session_config(main.SETTINGS, {"screening": cs.summary_for_model(
            cs.screen_webhook({"From": OURS}, [OURS]))})
        backend = config["delegation"]["responses"]["instructions"]
        self.assertIn("Screening suspicious callers", backend)
        self.assertIn('"risk": "high"', backend)
        self.assertIn("Privacy policy", config["instructions"])


class IntakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        mongo.save_call_intake = Mock(return_value="intake-1")
        mongo.save_screening_report = Mock(return_value="screen-1")

    def call(self):
        return {"channel": CHANNEL, "call_sid": "CA" + "3" * 32, "caller_number": "+14155550123",
                "screening": cs.screen_webhook({"From": "+14155550123", "FromCity": "SAN FRANCISCO",
                                                "FromState": "CA"}, [OURS])}

    async def test_intake_is_saved_and_sent_once(self):
        call = self.call()
        execute = main.make_tool_executor(call)
        args = {key: "" for key in main.INTAKE_FIELDS}
        args.update(caller_name="Priya Shah", organization="Stripe", role="Technical recruiter",
                    reason="Scheduling a new grad SWE onsite, Payments team, Seattle",
                    referral="His application on the careers site", urgency="Needs times by Friday")
        result = await execute("record_call_intake", "c1", args)
        self.assertEqual((result["status"], result["relay"]), ("recorded", "queued"))
        posted = started_jobs("discord.send")[-1]["args"]["content"]
        for part in ("Incoming call from Priya Shah (Technical recruiter, Stripe)", "+14155550123",
                     "San Francisco, CA", "screening low", "Reason: Scheduling", "Got the number from: His application",
                     "Urgency: Needs times by Friday"):
            self.assertIn(part, posted)
        self.assertNotIn("Email:", posted)
        self.assertEqual(mongo.save_call_intake.call_args.args[1]["number"], "+14155550123")
        again = await execute("record_call_intake", "c2", args)
        self.assertEqual(again["status"], "already_recorded")

    async def test_empty_intake_is_refused_and_a_suspicious_report_still_goes_through(self):
        call = self.call()
        execute = main.make_tool_executor(call)
        empty = await execute("record_call_intake", "c1", {key: "" for key in main.INTAKE_FIELDS})
        self.assertEqual(empty["status"], "invalid")
        filled = dict({key: "" for key in main.INTAKE_FIELDS}, reason="Says he's from the IRS")
        await execute("record_call_intake", "c2", filled)
        report = await execute("report_suspicious_call", "c3",
                               dict({key: "" for key in main.SCREENING_CLAIMS}, organization="IRS"))
        self.assertEqual(report["status"], "reported")
        # After a suspicious report, no ordinary intake is sent as well.
        execute2 = main.make_tool_executor(dict(self.call(), screening_reported=True))
        self.assertEqual((await execute2("record_call_intake", "c4", filled))["status"], "already_recorded")

    def test_incoming_calls_start_with_who_and_why(self):
        inbound = main.session_config(main.SETTINGS, {"script": "1"})
        outbound = main.session_config(main.SETTINGS, {"script": "2"})
        self.assertIn("ask who is calling and what the call is about", main.greeting({"script": "1"}))
        self.assertIn("Incoming call policy", inbound["instructions"])
        self.assertIn("Incoming call intake", inbound["delegation"]["responses"]["instructions"])
        self.assertIn("Cross-check", inbound["delegation"]["responses"]["instructions"])
        self.assertNotIn("Incoming call policy", outbound["instructions"])
        self.assertNotIn("Incoming call intake", outbound["delegation"]["responses"]["instructions"])
        voicemail = main.session_config(main.SETTINGS, {}, voicemail=True)
        self.assertNotIn("Incoming call policy", voicemail["instructions"])
        self.assertIn("record_call_intake", {t["name"] for t in main.TOOLS})


class ScreenedStreamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reset()
        mongo.save_screening_report = Mock(return_value="screen-1")
        self.twilio = FakeLookup(line_type="nonFixedVoip", carrier="Bandwidth", name="")
        self.saved_screener = main.screener
        main.screener = cs.CallScreener(memory, self.twilio)

    async def asyncTearDown(self):
        main.screener = self.saved_screener

    async def test_a_risky_caller_who_hangs_up_is_reported_with_one_lookup(self):
        screening = cs.screen_webhook({"From": "+16175550123", "StirVerstat": "TN-Validation-Failed"}, [OURS])
        token = main.contexts.put({"script": "1", "name": "", "message": "", "caller_number": "+16175550123",
                                   "screening": screening})
        with patch.object(main.websockets, "connect", return_value=AsyncMock()), \
                patch.object(main, "LiveBridge") as bridge, \
                patch.object(main, "watch_call", new=AsyncMock()), \
                patch.object(main, "finish_call", new=AsyncMock()):
            bridge.return_value.run = AsyncMock()
            await main.handle_stream(test_app.StreamTests.phone(None, token))
        instructions = bridge.call_args.args[3]["delegation"]["responses"]["instructions"]
        self.assertIn("STIR/SHAKEN validation failed", instructions)
        self.assertEqual(len(self.twilio.calls), 1)
        content = started_jobs("discord.send")[-1]["args"]["content"]
        self.assertIn("ended before details were collected: HIGH risk", content)
        self.assertIn("nonFixedVoip", content)


if __name__ == "__main__":
    unittest.main()
