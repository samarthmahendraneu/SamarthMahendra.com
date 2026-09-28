import unittest
from datetime import datetime, timezone

import timezones


class ResolveTests(unittest.TestCase):
    def test_a_wall_clock_time_gets_the_zones_offset_on_that_date(self):
        summer = timezones.resolve("2026-10-01T14:00", "America/Los_Angeles")
        winter = timezones.resolve("2026-11-05T14:00", "America/Los_Angeles")
        self.assertEqual(summer.isoformat(), "2026-10-01T14:00:00-07:00")
        self.assertEqual(winter.isoformat(), "2026-11-05T14:00:00-08:00")

    def test_an_offset_is_accepted_only_if_it_is_the_right_one(self):
        self.assertEqual(timezones.resolve("2026-11-05T14:00:00-08:00", "America/Los_Angeles").hour, 14)
        with self.assertRaisesRegex(ValueError, "UTC-08:00 on that date, not UTC-07:00"):
            timezones.resolve("2026-11-05T14:00:00-07:00", "America/Los_Angeles")
        with self.assertRaisesRegex(ValueError, "without an offset"):
            timezones.resolve("2026-11-05T19:00Z", "America/New_York")

    def test_a_time_the_clocks_skip_is_refused(self):
        with self.assertRaisesRegex(ValueError, "clocks go forward"):
            timezones.resolve("2027-03-14T02:30", "America/New_York")

    def test_a_time_that_happens_twice_takes_the_first(self):
        first = timezones.resolve("2026-11-01T01:30", "America/New_York")
        self.assertEqual(first.isoformat(), "2026-11-01T01:30:00-04:00")

    def test_unusable_input_says_why(self):
        with self.assertRaisesRegex(ValueError, "like 2026-10-01T14:00"):
            timezones.resolve("next Thursday", "America/New_York")
        with self.assertRaisesRegex(ValueError, "isn't a timezone name I know"):
            timezones.resolve("2026-10-01T14:00", "Mars/Olympus")


class ZoneTests(unittest.TestCase):
    def test_everyday_names_for_us_zones(self):
        cases = {"Pacific": "America/Los_Angeles", "pacific time": "America/Los_Angeles",
                 "PT": "America/Los_Angeles", "EST": "America/New_York", "Central": "America/Chicago",
                 "MT": "America/Denver", "UTC": "UTC", "Asia/Kolkata": "Asia/Kolkata",
                 "America/Phoenix": "America/Phoenix"}
        for name, key in cases.items():
            with self.subTest(name=name):
                self.assertEqual(timezones.zone(name).key, key)
        for bad in ("", None, "Pacific Standard", "../etc/passwd"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                timezones.zone(bad)

    def test_a_phone_number_tells_its_zone_when_it_has_just_one(self):
        cases = {"+16175550123": "America/New_York", "+14155550123": "America/Los_Angeles",
                 "+16025550123": "America/Phoenix", "+14165550123": "America/Toronto",
                 "+18005550123": None, "+19075550123": None, "not a number": None}
        for number, key in cases.items():
            with self.subTest(number=number):
                self.assertEqual(timezones.zone_for_number(number), key)


class DisplayTests(unittest.TestCase):
    def test_the_clock_tool_reports_the_zone_and_samarths_time(self):
        now = datetime(2026, 9, 28, 20, 5, tzinfo=timezone.utc)
        report = timezones.now_in("America/Los_Angeles", now=now)
        self.assertEqual(report["now"], "Monday, September 28, 2026 at 1:05 PM PDT")
        self.assertEqual(report["local_time"], "2026-09-28T13:05-07:00")
        self.assertEqual(report["utc_offset"], "UTC-07:00")
        self.assertEqual(report["samarth_now"], "Monday, September 28, 2026 at 4:05 PM EDT")
        self.assertEqual(timezones.now_in("", now=now)["timezone"], "America/New_York")

    def test_a_time_is_shown_in_a_second_zone_only_when_it_differs(self):
        pacific = timezones.resolve("2026-10-01T22:30", "America/Los_Angeles")
        self.assertEqual(timezones.also_in(pacific, "America/New_York"),
                         "Thursday 10:30 PM PDT (Friday 1:30 AM EDT)")
        eastern = timezones.resolve("2026-10-01T14:00", "America/New_York")
        self.assertEqual(timezones.also_in(eastern, "America/New_York"), "Thursday 2:00 PM EDT")


if __name__ == "__main__":
    unittest.main()
