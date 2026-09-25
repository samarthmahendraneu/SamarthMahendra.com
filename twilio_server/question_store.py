"""Shared state for questions the assistant asks Samarth on Discord.

The voice bridge writes questions here and reads answers back; the persistent
Discord listener posts them and records replies. Nothing blocks on Discord: a
call must never go silent waiting for a human to type.
"""

import json
import re
import time
import uuid

QUESTION_KEY = "discord:q:"
POST_QUEUE = "discord:post_queue"
OPEN_QUEUE = "discord:open"
# Long enough for a caller to be rung back, short enough not to accumulate.
TTL = 3600
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
        """Mark a callback as placed. Returns False if already claimed.

        Guards against ringing a caller twice when a reply is seen more than
        once or the listener restarts mid-flight.
        """
        record = self.get(question_id)
        if record is None or record.get("callback_state") != "requested":
            return False
        record["callback_state"] = "placed"
        self.save(record)
        return True

    def waiting_for(self, record):
        return time.time() - record["asked_at"]
