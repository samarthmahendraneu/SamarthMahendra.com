"""Shared state for questions the assistant asks Samarth on Discord.

Kept byte-identical in twilio_server/ (the voice agent asks) and pythonserver/
(the listener answers); tests/test_questions.py fails if the copies drift.

The voice bridge writes questions here and reads answers back; the persistent
Discord listener posts them and records replies. Nothing blocks on Discord: a
call must never go silent waiting for a human to type.

A reply reaches the caller one of two ways. While the call is live, the
bridge keeps a short-lived live key alive for each question it asked and
speaks the reply as soon as it lands. Once that key is gone (the caller hung
up), the listener rings them back instead, if they asked for it.
"""

import json
import re
import time
import uuid

QUESTION_KEY = "discord:q:"
POST_QUEUE = "discord:post_queue"
OPEN_QUEUE = "discord:open"
LIVE_KEY = "discord:live:"
CALLBACK_QUEUE = "discord:callback_queue"
CLAIM_KEY = "discord:callback-claim:"
# Long enough for a caller to be rung back, short enough not to accumulate.
TTL = 3600
# The bridge refreshes a live key every second, so a call that ended without
# clearing its keys (a crashed web process) stops counting as live in 10s.
LIVE_TTL = 10
ID_PATTERN = re.compile(r"[0-9a-f]{32}")


class QuestionStore:
    def __init__(self, redis):
        self.redis = redis

    def key(self, question_id):
        if not ID_PATTERN.fullmatch(question_id or ""):
            raise ValueError("Invalid question id")
        return QUESTION_KEY + question_id

    def ask(self, question, caller_name="", call_sid=""):
        """Record a question and queue it for the listener to post."""
        question_id = uuid.uuid4().hex
        record = {
            "id": question_id, "question": question, "caller_name": caller_name,
            "call_sid": call_sid, "asked_at": time.time(), "status": "pending",
            "reply": None, "replied_at": None,
            "callback_name": None, "callback_number": None, "callback_state": None,
            "delivered_live": False,
        }
        self.redis.setex(QUESTION_KEY + question_id, TTL, json.dumps(record))
        self.redis.rpush(POST_QUEUE, question_id)
        return question_id

    def get(self, question_id):
        raw = self.redis.get(self.key(question_id))
        return json.loads(raw) if raw else None

    def save(self, record):
        self.redis.setex(QUESTION_KEY + record["id"], TTL, json.dumps(record))

    def pop_for_posting(self):
        """Listener side: take the next question to send to Discord."""
        question_id = self.redis.lpop(POST_QUEUE)
        if question_id is None:
            return None
        if isinstance(question_id, bytes):
            question_id = question_id.decode()
        record = self.get(question_id)
        if record is None:
            return None            # expired before the listener got to it
        record["status"] = "asked"
        self.save(record)
        self.redis.rpush(OPEN_QUEUE, question_id)
        return record

    def answer(self, reply, question_id=None):
        """Attach a reply, defaulting to the oldest question still waiting."""
        if question_id is None:
            question_id = self.redis.lpop(OPEN_QUEUE)
            if isinstance(question_id, bytes):
                question_id = question_id.decode()
        else:
            self.redis.lrem(OPEN_QUEUE, 0, question_id)
        if not question_id:
            return None
        record = self.get(question_id)
        if record is None or record["status"] == "answered":
            return None
        record["status"] = "answered"
        record["reply"] = reply
        record["replied_at"] = time.time()
        self.save(record)
        return record

    def request_callback(self, question_id, name, number):
        record = self.get(question_id)
        if record is None:
            return None
        record["callback_name"] = name
        record["callback_number"] = number
        record["callback_state"] = "requested"
        self.save(record)
        return record

    def claim_callback(self, question_id):
        """Claim the right to place a callback. Returns False if already claimed.

        Atomic (SET NX): both the listener, when a reply lands, and the bridge,
        when a call ends mid-reply, can decide a callback is due. A read-then-
        write claim would let both win and ring the caller twice.
        """
        record = self.get(question_id)
        if record is None or record.get("callback_state") != "requested":
            return False
        if not self.redis.set(CLAIM_KEY + question_id, "1", nx=True, ex=TTL):
            return False
        record["callback_state"] = "placed"
        self.save(record)
        return True

    # ---- the call's side: is the caller still on the line? ----

    def mark_live(self, question_id, ttl=LIVE_TTL):
        self.redis.setex(LIVE_KEY + question_id, ttl, "1")

    def clear_live(self, question_id):
        self.redis.delete(LIVE_KEY + question_id)

    def is_live(self, question_id):
        return bool(self.redis.exists(LIVE_KEY + question_id))

    def mark_delivered(self, question_id):
        record = self.get(question_id)
        if record is not None:
            record["delivered_live"] = True
            self.save(record)

    # ---- a call that ended before its reply was spoken ----

    def queue_callback(self, question_id):
        self.redis.rpush(CALLBACK_QUEUE, question_id)

    def pop_callback(self):
        question_id = self.redis.lpop(CALLBACK_QUEUE)
        if isinstance(question_id, bytes):
            question_id = question_id.decode()
        return question_id

    def waiting_for(self, record):
        return time.time() - record["asked_at"]
