"""Scheduled call backs: ring a caller at a set time, and retry if they miss it.

Two things schedule one: a caller who asked to be rung back once Samarth
answers (see question_store), and a caller who asked for a call at a time
of their choosing (the schedule_callback tool). The Discord listener's
scheduler claims calls as they fall due (pythonserver/callback_scheduler.py)
and has the voice service dial them; Twilio's status webhook on the voice
service reports how each went, and a missed call is tried again later, up to
MAX_ATTEMPTS times.

What a call is about stays here, under an unguessable id, rather than riding
in the call's URL where Twilio logs it. An answer of Samarth's that lands
after the caller has gone, but before a call they booked for a time of their
choosing rings, goes along on that call (pending_for, add_answer).

Automatic calls -- a reply that lands late in the evening, a retry -- go out
at any hour, unless calling hours are set (CALLBACK_HOURS): then they wait
for them in the caller's own timezone, the one they gave, else the one their
number belongs to, else CALLBACK_TIMEZONE. A time the caller chose is kept as
given.

Kept byte-identical in twilio_server/ and pythonserver/; the tests fail if the
copies drift.
"""

import json
import re
import time
import uuid
from datetime import datetime, timedelta

import timezones

CALLBACK_KEY = "callback:"
DUE_KEY = "callbacks:due"
COUNT_KEY = "callbacks:count:"
# One per call back and try: set by whoever dials it.
DIAL_KEY = "callbacks:dialing:"
# Each conversation's (call's or chat's) call backs, for pending_for.
ORIGIN_KEY = "callbacks:origin:"
# Answers a call back is to pass on. Not on the record: the scheduler and the
# voice service rewrite that as the call moves on, and an edit landing between
# one of their reads and its write would be lost, or would undo theirs.
ANSWERS_KEY = "callbacks:answers:"
TTL = 7 * 86400
MAX_ATTEMPTS = 3
# Wait before the second and third tries.
RETRY_DELAYS = (10 * 60, 30 * 60)
# Enough for a real caller; not enough to turn the line into a way to pester
# someone else's phone.
MAX_PER_NUMBER_PER_DAY = 3
# Samarth's own phone, for testing: no limit on how often it's called back.
UNLIMITED_NUMBERS = frozenset(("+18577071671",))
# How far ahead a caller can book a call.
MAX_AHEAD = 14 * 86400
# A call this overdue (the scheduler was down) is not placed at night.
LATE = 15 * 60
ID_PATTERN = re.compile(r"[0-9a-f]{32}")
E164 = re.compile(r"\+[1-9]\d{7,14}")
# +1 numbers that are not the US or Canada: Caribbean and Atlantic islands
# billed as international calls, a favourite of toll fraud.
NANP_ELSEWHERE = frozenset((
    "242", "246", "264", "268", "284", "345", "441", "473", "649", "658",
    "664", "721", "758", "767", "784", "809", "829", "849", "868", "869", "876",
))
FINAL_STATUSES = ("completed", "busy", "no-answer", "failed", "canceled")
MISSED_STATUSES = ("busy", "no-answer", "failed", "canceled")


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def _sentence(text):
    """Text ending in a full stop unless it already ends a sentence."""
    text = str(text).strip()
    return text if text.endswith((".", "?", "!")) else text + "."


class CallbackStore:
    def __init__(self, redis, timezone="America/New_York", hours=None,
                 country_codes=("1",)):
        self.redis = redis
        # For callers whose timezone can't be told any other way.
        self.default_zone = timezones.zone(timezone).key
        # (start, end) local hours for automatic calls; None for any hour.
        self.hours = hours
        self.country_codes = tuple(country_codes)

    @classmethod
    def from_env(cls, redis, env):
        hours = None
        if env.get("CALLBACK_HOURS"):
            start, _, end = env["CALLBACK_HOURS"].partition("-")
            hours = (int(start), int(end))
        codes = [code.strip().lstrip("+") for code in
                 (env.get("CALLBACK_COUNTRY_CODES") or "1").split(",")]
        return cls(redis, timezone=env.get("CALLBACK_TIMEZONE") or "America/New_York",
                   hours=hours, country_codes=[c for c in codes if c])

    def key(self, callback_id):
        if not ID_PATTERN.fullmatch(callback_id or ""):
            raise ValueError("Invalid callback id")
        return CALLBACK_KEY + callback_id

    # ---- who may be called, and when ----

    def check_number(self, number):
        """Raise ValueError, with a reason fit to pass on, for a number we won't ring."""
        if not isinstance(number, str) or not E164.fullmatch(number):
            raise ValueError("Callback needs an E.164 number, e.g. +16175550123")
        if not any(number.startswith("+" + code) for code in self.country_codes):
            raise ValueError("Call backs can only go to numbers in: "
                             + ", ".join("+" + c for c in self.country_codes))
        if number.startswith("+1") and number[2:5] in NANP_ELSEWHERE:
            raise ValueError("Call backs can only go to US and Canadian +1 numbers")

    def callee_zone(self, number, given=None):
        """(zone, where it came from): the one the caller gave, else their
        number's, else the default. A zone given but not recognised raises."""
        if given:
            return timezones.zone(given).key, "given"
        found = timezones.zone_for_number(number)
        return (found, "number") if found else (self.default_zone, "default")

    def calling_time(self, timestamp, zone_name=None):
        """The first moment at or after `timestamp` inside calling hours,
        on the callee's clock."""
        if not self.hours:
            return timestamp
        start, end = self.hours
        moment = datetime.fromtimestamp(timestamp, timezones.zone(zone_name or self.default_zone))
        if moment.hour < start:
            moment = moment.replace(hour=start, minute=0, second=0, microsecond=0)
        elif moment.hour >= end:
            moment = (moment + timedelta(days=1)).replace(hour=start, minute=0, second=0,
                                                          microsecond=0)
        return moment.timestamp()

    def local(self, timestamp, zone_name=None):
        return datetime.fromtimestamp(timestamp, timezones.zone(zone_name or self.default_zone))

    def when_text(self, timestamp, zone_name=None):
        """For the caller: "Tuesday 10:00 AM PDT"."""
        return timezones.short(self.local(timestamp, zone_name))

    def when_for_samarth(self, record):
        """For Samarth: the caller's time, and his own when it differs."""
        return timezones.also_in(self.local(record["due_at"], record.get("timezone")),
                                 timezones.SAMARTH_ZONE)

    # ---- scheduling ----

    def schedule(self, to, name, purpose, voicemail, when=None, source="request",
                 question_id=None, origin=None, now=None, tz=None):
        """Book a call back and return its record.

        `when` is the time the caller asked for, as a timestamp, or None to
        call as soon as calling hours allow; `tz` is the caller's timezone if
        they gave it. Raises ValueError, with a reason fit to pass on, if the
        number, time or timezone can't be used.
        """
        now = time.time() if now is None else now
        self.check_number(to)
        zone_name, zone_source = self.callee_zone(to, tz)
        if when is not None:
            if when < now - 60:
                raise ValueError("That time has already passed")
            if when > now + MAX_AHEAD:
                raise ValueError("Call backs can be booked up to 14 days ahead")
            due = max(when, now)
        else:
            due = self.calling_time(now, zone_name)
        if to not in UNLIMITED_NUMBERS:
            day = self.local(due, zone_name).strftime("%Y%m%d")
            count_key = COUNT_KEY + to + ":" + day
            if self.redis.incr(count_key) > MAX_PER_NUMBER_PER_DAY:
                raise ValueError("That number already has the most call backs allowed for the day")
            self.redis.expire(count_key, 2 * 86400)
        record = {
            "id": uuid.uuid4().hex, "to": to, "name": name, "purpose": purpose,
            "voicemail": voicemail, "source": source, "question_id": question_id,
            "origin": origin, "requested_for": when, "due_at": due,
            "timezone": zone_name, "timezone_source": zone_source,
            "state": "scheduled", "outcome": None, "attempts": 0,
            "max_attempts": MAX_ATTEMPTS, "calls": {}, "answered_by": None,
            "created_at": now, "updated_at": now,
        }
        self.save(record)
        self.redis.zadd(DUE_KEY, {record["id"]: due})
        if origin:
            self.redis.rpush(ORIGIN_KEY + origin, record["id"])
            self.redis.expire(ORIGIN_KEY + origin, TTL)
        return record

    def get(self, callback_id):
        raw = self.redis.get(self.key(callback_id))
        return json.loads(raw) if raw else None

    def save(self, record):
        record["updated_at"] = time.time()
        self.redis.setex(self.key(record["id"]), TTL, json.dumps(record))

    def cancel(self, callback_id):
        record = self.get(callback_id)
        if record is None:
            return None
        self.redis.zrem(DUE_KEY, callback_id)
        record["state"] = "cancelled"
        self.save(record)
        return record

    # ---- answers that land before a booked call rings ----

    def timed_for(self, origin):
        """The conversation's call backs at a time they chose, soonest first.
        Those booked to deliver an answer are left out: they carry theirs."""
        if not origin:
            return []
        records = (self.get(_text(i)) for i in self.redis.lrange(ORIGIN_KEY + origin, 0, -1))
        return sorted((r for r in records if r and r["source"] != "question"),
                      key=lambda r: r["due_at"])

    def pending_for(self, origin):
        """Of those, the ones that haven't started ringing yet."""
        return [r for r in self.timed_for(origin) if r["state"] == "scheduled"]

    def add_answer(self, callback_id, question, reply):
        """Have the call pass on Samarth's answer to a question."""
        key = ANSWERS_KEY + callback_id
        self.redis.rpush(key, json.dumps({"question": question, "reply": reply}))
        self.redis.expire(key, TTL)

    def answers(self, callback_id):
        return [json.loads(_text(raw)) for raw in self.redis.lrange(ANSWERS_KEY + callback_id, 0, -1)]

    def call_message(self, record):
        """What the call is for, as the voice agent is told it, with any
        answers that came in since it was booked."""
        answers = self.answers(record["id"])
        if not answers:
            return record["purpose"]
        said = " ".join(f"They asked: {a['question']} Samarth's answer is: {a['reply']}" for a in answers)
        return f"{record['purpose']} Since it was booked, Samarth has answered. {said}"

    def call_voicemail(self, record):
        """The call's voicemail, giving any answers that came in since."""
        answers = self.answers(record["id"])
        if not answers:
            return record["voicemail"]
        name = record.get("name") or ""
        said = " ".join(f"You asked: {_sentence(a['question'])} He says: {_sentence(a['reply'])}"
                        for a in answers)
        return (f"{'Hi ' + name if name else 'Hi'}, this is Luma, Samarth Mahendra's AI assistant, "
                f"calling you back as you asked, with his answer. {said} To talk it through, call "
                "this number back. Goodbye.")

    # ---- placing and following up ----

    def claim_due(self, now=None, limit=10):
        """Take the call backs now due. Each is handed to exactly one caller:
        removing it from the due set is the claim, and only one ZREM wins."""
        now = time.time() if now is None else now
        claimed = []
        for callback_id in self.redis.zrangebyscore(DUE_KEY, "-inf", now, start=0, num=limit):
            callback_id = callback_id.decode() if isinstance(callback_id, bytes) else callback_id
            if not self.redis.zrem(DUE_KEY, callback_id):
                continue
            record = self.get(callback_id)
            if record is None or record["state"] != "scheduled":
                continue
            if now - record["due_at"] > LATE:
                on_time = self.calling_time(now, record.get("timezone"))
                if on_time > now:
                    # Hours late and it's night where they are: wait for morning.
                    record["due_at"] = on_time
                    self.save(record)
                    self.redis.zadd(DUE_KEY, {callback_id: on_time})
                    continue
            record["state"] = "dialing"
            record["attempts"] += 1
            self.save(record)
            claimed.append(record)
        return claimed

    def take_for_dialing(self, callback_id):
        """For the voice service, asked to dial a call back: the record if the
        scheduler has claimed it (claim_due) and this try hasn't been dialled
        yet, else None. Atomic, so a repeated request can't ring twice."""
        record = self.get(callback_id)
        if record is None or record["state"] != "dialing":
            return None
        if not self.redis.set(f"{DIAL_KEY}{callback_id}:{record['attempts']}", "1", nx=True, ex=TTL):
            return None
        return record

    def dialed(self, callback_id, call_sid):
        record = self.get(callback_id)
        if record is not None:
            record["state"] = "ringing"
            record["calls"][call_sid] = "ringing"
            self.save(record)
        return record

    def note_answered_by(self, callback_id, answered_by):
        """Twilio's machine detection verdict, given when the call connects."""
        record = self.get(callback_id)
        if record is not None:
            record["answered_by"] = answered_by
            self.save(record)
        return record

    def dial_failed(self, callback_id, now=None):
        """Twilio refused to place the call at all: treat it as a missed try."""
        record = self.get(callback_id)
        if record is None:
            return None, None
        return record, self._retry_or_give_up(record, now)

    def finished(self, callback_id, call_sid, status, answered_by=None, now=None):
        """Record how a call ended. Returns (record, outcome), where outcome is
        "answered", "voicemail", "retrying", "gave_up", or None if nothing
        changed (an interim status, or Twilio repeating itself)."""
        record = self.get(callback_id)
        if record is None or status not in FINAL_STATUSES:
            return record, None
        if record["calls"].get(call_sid) in FINAL_STATUSES:
            return record, None
        record["calls"][call_sid] = status
        answered_by = answered_by or record.get("answered_by") or ""
        if status == "completed":
            machine = answered_by.startswith("machine") or answered_by == "fax"
            record["state"] = "done"
            record["outcome"] = "voicemail" if machine else "answered"
            self.save(record)
            return record, record["outcome"]
        return record, self._retry_or_give_up(record, now)

    def _retry_or_give_up(self, record, now=None):
        now = time.time() if now is None else now
        if record["attempts"] < record["max_attempts"]:
            delay = RETRY_DELAYS[min(record["attempts"], len(RETRY_DELAYS)) - 1]
            record["due_at"] = self.calling_time(now + delay, record.get("timezone"))
            record["state"] = "scheduled"
            record["outcome"] = "retrying"
            self.save(record)
            self.redis.zadd(DUE_KEY, {record["id"]: record["due_at"]})
        else:
            record["state"] = "failed"
            record["outcome"] = "gave_up"
            self.save(record)
        return record["outcome"]
