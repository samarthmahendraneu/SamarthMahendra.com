import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from callbacks import DUE_KEY, MAX_PER_NUMBER_PER_DAY, CallbackStore
from memory_redis import MemoryRedis
from worker_modules import load

EASTERN = ZoneInfo("America/New_York")
NUMBER = "+16175550123"


def eastern(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=EASTERN).timestamp()


def pacific(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def central(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo("America/Chicago")).timestamp()


class CallbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = CallbackStore(MemoryRedis())

    def book(self, now=None, when=None, to=NUMBER):
        return self.store.schedule(to, "Alice", "why", "voicemail", when=when,
                                   now=eastern(1, 14) if now is None else now)

    def test_automatic_calls_wait_for_calling_hours(self):
        self.assertEqual(self.book(now=eastern(1, 14))["due_at"], eastern(1, 14))
        self.assertEqual(self.book(now=eastern(1, 6))["due_at"], eastern(1, 10))
        self.assertEqual(self.book(now=eastern(1, 22))["due_at"], eastern(2, 10))

    def test_calling_hours_are_on_the_callers_clock(self):
        # Noon in Boston is 9am in San Francisco: the Boston number is called
        # now, the San Francisco one waits until 10am there.
        boston = self.book(now=eastern(1, 12))
        san_francisco = self.book(now=eastern(1, 12), to="+14155550123")
        self.assertEqual(boston["due_at"], eastern(1, 12))
        self.assertEqual(san_francisco["due_at"], pacific(1, 10))
        self.assertEqual((san_francisco["timezone"], san_francisco["timezone_source"]),
                         ("America/Los_Angeles", "number"))

    def test_a_zone_the_caller_gave_beats_their_numbers(self):
        record = self.store.schedule(NUMBER, "Alice", "why", "vm", now=central(1, 8), tz="Central")
        self.assertEqual((record["due_at"], record["timezone_source"]), (central(1, 10), "given"))
        with self.assertRaises(ValueError):
            self.store.schedule(NUMBER, "Alice", "why", "vm", tz="Mars/Olympus")

    def test_an_unknown_zone_falls_back_to_the_default(self):
        record = self.book(now=eastern(1, 6), to="+18005550123")      # toll-free
        self.assertEqual((record["timezone"], record["timezone_source"], record["due_at"]),
                         ("America/New_York", "default", eastern(1, 10)))

    def test_a_retry_waits_for_morning_where_the_caller_is(self):
        record = self.book(now=pacific(1, 14), to="+14155550123")
        self.store.claim_due(now=pacific(1, 15))
        record, _ = self.store.finished(record["id"], "CA1", "no-answer", now=pacific(1, 19, 55))
        self.assertEqual(record["due_at"], pacific(2, 10))

    def test_samarth_sees_the_callers_time_and_his(self):
        record = self.book(now=pacific(1, 14), to="+14155550123")
        self.assertEqual(self.store.when_for_samarth(record), "Thursday 2:00 PM PDT (5:00 PM EDT)")
        self.assertEqual(self.store.when_text(record["due_at"], record["timezone"]), "Thursday 2:00 PM PDT")

    def test_a_time_the_caller_chose_is_kept_even_outside_calling_hours(self):
        record = self.book(now=eastern(1, 14), when=eastern(2, 8, 30))
        self.assertEqual(record["due_at"], eastern(2, 8, 30))
        self.assertEqual(self.store.redis.zscore(DUE_KEY, record["id"]), eastern(2, 8, 30))

    def test_times_must_be_ahead_and_not_too_far(self):
        with self.assertRaisesRegex(ValueError, "passed"):
            self.book(now=eastern(1, 14), when=eastern(1, 12))
        with self.assertRaisesRegex(ValueError, "14 days"):
            self.book(now=eastern(1, 14), when=eastern(1, 14) + 15 * 86400)

    def test_only_us_and_canadian_numbers_by_default(self):
        for number in ("+442079460958", "+18765550123", "+18095550123", "6175550123"):
            with self.subTest(number=number), self.assertRaises(ValueError):
                self.book(to=number)
        self.book(to="+14165550123")      # Toronto

    def test_other_countries_can_be_allowed(self):
        store = CallbackStore.from_env(MemoryRedis(), {"CALLBACK_COUNTRY_CODES": "1, +44",
                                                       "CALLBACK_HOURS": "9-18"})
        store.check_number("+442079460958")
        self.assertEqual(store.hours, (9, 18))

    def test_a_number_cant_be_booked_without_limit(self):
        for _ in range(MAX_PER_NUMBER_PER_DAY):
            self.book()
        with self.assertRaisesRegex(ValueError, "most call backs"):
            self.book()

    def test_due_calls_are_handed_out_exactly_once(self):
        now, later = self.book(), self.book(when=eastern(3, 12))
        claimed = self.store.claim_due(now=eastern(1, 15))
        self.assertEqual([r["id"] for r in claimed], [now["id"]])
        self.assertEqual((claimed[0]["state"], claimed[0]["attempts"]), ("dialing", 1))
        self.assertEqual(self.store.claim_due(now=eastern(1, 15)), [])
        self.assertEqual(self.store.get(later["id"])["state"], "scheduled")

    def test_a_call_that_is_hours_late_waits_for_morning_not_rings_at_night(self):
        # The scheduler was down: a 2pm call found at 11pm must not ring then.
        record = self.book(now=eastern(1, 14))
        self.assertEqual(self.store.claim_due(now=eastern(1, 23)), [])
        self.assertEqual(self.store.get(record["id"])["due_at"], eastern(2, 10))
        (claimed,) = self.store.claim_due(now=eastern(2, 10))
        self.assertEqual(claimed["id"], record["id"])

    def test_a_late_call_still_inside_calling_hours_goes_out(self):
        record = self.book(now=eastern(1, 14))
        (claimed,) = self.store.claim_due(now=eastern(1, 16))
        self.assertEqual(claimed["id"], record["id"])

    def test_a_cancelled_call_back_is_never_placed(self):
        record = self.book()
        self.store.cancel(record["id"])
        self.assertEqual(self.store.claim_due(now=eastern(1, 15)), [])

    def dial(self, record, sid="CA1", now=None):
        (claimed,) = self.store.claim_due(now=now or eastern(1, 15))
        self.store.dialed(claimed["id"], sid)
        return claimed

    def test_answered_or_voicemail_ends_it(self):
        answered, machine = self.book(), self.book()
        self.store.claim_due(now=eastern(1, 15))
        self.store.dialed(answered["id"], "CA1")
        self.store.dialed(machine["id"], "CA2")
        self.assertEqual(self.store.finished(answered["id"], "CA1", "completed", "human")[1], "answered")
        self.store.note_answered_by(machine["id"], "machine_end_beep")
        record, outcome = self.store.finished(machine["id"], "CA2", "completed")
        self.assertEqual((outcome, record["state"]), ("voicemail", "done"))

    def test_missed_calls_are_retried_then_given_up(self):
        record = self.book()
        delays = []
        for attempt, sid in enumerate(("CA1", "CA2", "CA3"), start=1):
            claimed = self.dial(record, sid, now=eastern(2, 19))
            self.assertEqual(claimed["attempts"], attempt)
            record, outcome = self.store.finished(record["id"], sid, "no-answer", now=eastern(1, 15))
            delays.append(record["due_at"] - eastern(1, 15))
        self.assertEqual(outcome, "gave_up")
        self.assertEqual(record["state"], "failed")
        self.assertEqual(delays[:2], [10 * 60, 30 * 60])

    def test_a_retry_that_would_land_at_night_waits_for_morning(self):
        record = self.book()
        self.dial(record)
        record, _ = self.store.finished(record["id"], "CA1", "busy", now=eastern(1, 19, 55))
        self.assertEqual(record["due_at"], eastern(2, 10))

    def test_repeated_or_interim_reports_change_nothing(self):
        record = self.book()
        self.dial(record)
        self.assertIsNone(self.store.finished(record["id"], "CA1", "ringing")[1])
        self.assertEqual(self.store.finished(record["id"], "CA1", "no-answer")[1], "retrying")
        self.assertIsNone(self.store.finished(record["id"], "CA1", "no-answer")[1])

    def test_a_call_twilio_refused_to_place_is_a_missed_try(self):
        record = self.book()
        self.store.claim_due(now=eastern(1, 15))
        record, outcome = self.store.dial_failed(record["id"], now=eastern(1, 15))
        self.assertEqual((outcome, record["state"]), ("retrying", "scheduled"))

    def test_times_are_spoken_the_way_people_say_them(self):
        self.assertEqual(self.store.when_text(eastern(2, 10)), "Friday 10:00 AM EDT")
        self.assertEqual(self.store.when_text(eastern(2, 15, 30)), "Friday 3:30 PM EDT")


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scheduler = load("callback_scheduler")
        self.store = CallbackStore(MemoryRedis())
        self.twilio = Mock()
        self.twilio.calls.create.return_value = SimpleNamespace(sid="CA7")
        self.notify = AsyncMock()

    def due_now(self):
        return self.store.schedule(NUMBER, "Alice", "why", "voicemail", when=time.time())

    async def test_due_call_backs_are_placed_with_machine_detection(self):
        record = self.due_now()
        await self.scheduler.tick(self.store, self.twilio, "+18339703274", "https://voice.test", self.notify)
        kwargs = self.twilio.calls.create.call_args.kwargs
        self.assertEqual((kwargs["to"], kwargs["from_"]), (NUMBER, "+18339703274"))
        self.assertEqual(kwargs["machine_detection"], "DetectMessageEnd")
        # What the call is about stays in Redis; the URLs carry only its id.
        for url in (kwargs["url"], kwargs["status_callback"]):
            self.assertEqual(parse_qs(urlsplit(url).query), {"cb": [record["id"]]})
        self.assertEqual(urlsplit(kwargs["status_callback"]).path, "/callback-status")
        self.assertEqual(self.store.get(record["id"])["state"], "ringing")

    async def test_a_call_twilio_rejects_is_retried_and_reported(self):
        self.twilio.calls.create.side_effect = RuntimeError("geo permission")
        record = self.due_now()
        await self.scheduler.tick(self.store, self.twilio, "+18339703274", "https://voice.test", self.notify)
        self.assertEqual(self.store.get(record["id"])["state"], "scheduled")
        self.assertIn("geo permission", self.notify.await_args.args[0])
        self.assertIn("Trying again", self.notify.await_args.args[0])

    async def test_nothing_due_places_nothing(self):
        self.store.schedule(NUMBER, "Alice", "why", "voicemail", when=time.time() + 7 * 86400)
        await self.scheduler.tick(self.store, self.twilio, "+1", "https://voice.test", self.notify)
        self.twilio.calls.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
