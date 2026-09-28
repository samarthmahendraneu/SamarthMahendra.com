"""Shared state for questions the assistant asks Samarth on Discord.

Kept byte-identical in twilio_server/ (the voice agent asks) and pythonserver/
(the chat asks and the listener answers); the tests fail if the copies drift.

Whoever asks writes the question here and gets its id straight back; the
persistent Discord listener posts it and records Samarth's reply. Nothing
blocks on Discord: a call must never go silent, nor a chat hang, waiting for
a human to type.

Each question remembers its origin -- the conversation that asked, as an
events.py channel -- and the Discord message it was posted as. A reply is
matched to its question by Discord's reply-to link, not by arrival order: with
two conversations waiting, an oldest-first guess could read one caller
another caller's answer out loud.
"""

import json
import re
import time
import uuid

QUESTION_KEY = "discord:q:"
POST_QUEUE = "discord:post_queue"
OPEN_QUEUE = "discord:open"
POST_KEY = "discord:post:"
CALLBACK_QUEUE = "discord:callback_queue"
CLAIM_KEY = "discord:callback-claim:"
# Calls from before the event streams marked each question live instead of
# the call; read so a listener deployed mid-call still sees those callers.
LIVE_KEY = "discord:live:"
# Long enough for Samarth to answer that evening and the caller still to be
# rung back; short enough not to accumulate.
TTL = 12 * 3600
ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


class QuestionStore:
    def __init__(self, redis):
        self.redis = redis

    def key(self, question_id):
        if not ID_PATTERN.fullmatch(question_id or ""):
            raise ValueError("Invalid question id")
        return QUESTION_KEY + question_id

    def ask(self, question, caller_name="", origin=None):
        """Record a question and queue it for the listener to post."""
        question_id = uuid.uuid4().hex
        record = {
            "id": question_id, "question": question, "caller_name": caller_name,
            "origin": origin, "asked_at": time.time(), "status": "pending",
            "reply": None, "replied_at": None, "followups": [],
            "callback_name": None, "callback_number": None, "callback_timezone": None,
            "callback_state": None,
            "delivered_live": False, "discord_message_id": None,
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
        question_id = _text(self.redis.lpop(POST_QUEUE))
        if question_id is None:
            return None
        record = self.get(question_id)
        if record is None:
            return None            # expired before the listener got to it
        record["status"] = "asked"
        self.save(record)
        self.redis.rpush(OPEN_QUEUE, question_id)
        return record

    # ---- matching Discord replies to questions ----

    def remember_post(self, question_id, message_id):
        """Tie a Discord message (the question, or a note about it) to a question."""
        self.redis.setex(POST_KEY + str(message_id), TTL, question_id)
        record = self.get(question_id)
        if record is not None and not record.get("discord_message_id"):
            record["discord_message_id"] = str(message_id)
            self.save(record)

    def question_for_post(self, message_id):
        question_id = _text(self.redis.get(POST_KEY + str(message_id)))
        return question_id if question_id and ID_PATTERN.fullmatch(question_id) else None

    def open_questions(self):
        """Questions posted and still waiting for an answer, oldest first."""
        waiting = []
        for question_id in self.redis.lrange(OPEN_QUEUE, 0, -1):
            question_id = _text(question_id)
            record = self.get(question_id) if ID_PATTERN.fullmatch(question_id or "") else None
            if record is None or record["status"] != "asked":
                self.redis.lrem(OPEN_QUEUE, 0, question_id)     # expired or answered
                continue
            waiting.append(record)
        return waiting

    def answer(self, question_id, reply):
        """Attach Samarth's reply. None if the question is gone or already answered."""
        self.redis.lrem(OPEN_QUEUE, 0, question_id)
        record = self.get(question_id)
        if record is None or record["status"] == "answered":
            return None
        record["status"] = "answered"
        record["reply"] = reply
        record["replied_at"] = time.time()
        self.save(record)
        return record

    def add_followup(self, question_id, reply):
        """A second message on an answered question: a correction or an addition."""
        record = self.get(question_id)
        if record is None or record["status"] != "answered":
            return None
        record.setdefault("followups", []).append({"text": reply, "at": time.time()})
        self.save(record)
        return record

    # ---- calling the caller back ----

    def request_callback(self, question_id, name, number, timezone=None):
        record = self.get(question_id)
        if record is None:
            return None
        record["callback_name"] = name
        record["callback_number"] = number
        record["callback_timezone"] = timezone or None
        record["callback_state"] = "requested"
        self.save(record)
        return record

    def claim_callback(self, question_id):
        """Claim the right to arrange a call back. Returns False if already claimed.

        Atomic (SET NX): both the listener, when a reply lands, and the call,
        when it ends mid-reply, can decide a call back is due. A read-then-
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

    def mark_delivered(self, question_id):
        record = self.get(question_id)
        if record is not None:
            record["delivered_live"] = True
            self.save(record)

    # A call that ended before its reply was spoken hands it over here.

    def queue_callback(self, question_id):
        self.redis.rpush(CALLBACK_QUEUE, question_id)

    def pop_callback(self):
        return _text(self.redis.lpop(CALLBACK_QUEUE))

    def is_live_legacy(self, question_id):
        """Whether a call from before the event streams still holds the line."""
        return bool(self.redis.exists(LIVE_KEY + question_id))

    def waiting_for(self, record):
        return time.time() - record["asked_at"]
