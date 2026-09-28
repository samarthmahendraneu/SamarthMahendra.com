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
# Who to ring with Samarth's answers, per conversation (an events.py channel).
CALLBACK_FOR_KEY = "discord:callback-for:"
# Calls from before the event streams marked each question live instead of
# the call; read so a listener deployed mid-call still sees those callers.
LIVE_KEY = "discord:live:"
# Long enough for Samarth to answer that evening and the caller still to be
# rung back; short enough not to accumulate.
TTL = 12 * 3600
ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


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

    def want_callback(self, origin, name, number, timezone=None):
        """Ring this conversation's person with Samarth's answers if they've
        gone by the time he replies.

        Held for the conversation, not one question: people ask again in other
        words, or ask for the call before the question is put, and the call is
        for whatever he answers either way.
        """
        self.redis.setex(CALLBACK_FOR_KEY + origin, TTL,
                         json.dumps({"name": name, "number": number, "timezone": timezone or None}))

    def request_callback(self, question_id, name, number, timezone=None):
        """A call back with this question's answer, and its conversation's others."""
        record = self.get(question_id)
        if record is None:
            return None
        if record.get("origin"):
            self.want_callback(record["origin"], name, number, timezone)
        if record["status"] != "answered":
            # On the question too, for a listener from before want_callback.
            record.update(callback_name=name, callback_number=number,
                          callback_timezone=timezone or None, callback_state="requested")
            self.save(record)
        return record

    def callback_request(self, record):
        """Who to ring with this question's answer ({name, number, timezone}),
        or None if nobody asked or a call back was already arranged."""
        if record.get("callback_state") in ("placed", "failed"):
            return None
        if record.get("origin"):
            raw = self.redis.get(CALLBACK_FOR_KEY + record["origin"])
            if raw:
                return json.loads(raw)
        if record.get("callback_state") == "requested":
            return {"name": record.get("callback_name") or "", "number": record["callback_number"],
                    "timezone": record.get("callback_timezone")}
        return None

    def claim_callback(self, question_id):
        """Claim the right to ring back with this question's answer: the record,
        with who to call filled in, or None if no call back is wanted or it
        was already claimed.

        Atomic (SET NX): both the listener, when a reply lands, and the call,
        when it ends mid-reply, can decide a call back is due. A read-then-
        write claim would let both win and ring the caller twice.
        """
        record = self.get(question_id)
        wanted = record and self.callback_request(record)
        if not wanted:
            return None
        if not self.redis.set(CLAIM_KEY + question_id, "1", nx=True, ex=TTL):
            return None
        record.update(callback_name=wanted["name"], callback_number=wanted["number"],
                      callback_timezone=wanted.get("timezone"), callback_state="placed")
        self.save(record)
        return record

    def book_answer_call(self, question_id, callbacks):
        """Ring the person back with Samarth's answer (a callbacks.CallbackStore).

        Returns (question, call back), or None if no call back is wanted, one
        is already arranged, or there's no answer yet. Raises ValueError, with
        a reason fit to pass on, if the call can't be booked.
        """
        record = self.get(question_id)
        if record is None or record["status"] != "answered":
            return None
        record = self.claim_callback(question_id)
        if record is None:
            return None
        name = record["callback_name"] or ""
        question, reply = clip(record["question"], 300), clip(record["reply"], 900)
        try:
            booked = callbacks.schedule(
                record["callback_number"], name,
                purpose=f"You asked: {question} Samarth's answer is: {reply}",
                voicemail=(f"{'Hi ' + name if name else 'Hi'}, this is Luma, Samarth Mahendra's AI "
                           f"assistant, calling back with his answer to your question. You asked: "
                           f"{question}. He says: {reply}. To talk it through, call this number "
                           "back. Goodbye."),
                source="question", question_id=record["id"], origin=record.get("origin"),
                tz=record.get("callback_timezone"))
        except Exception:
            # So the chat doesn't tell them a call is coming.
            record["callback_state"] = "failed"
            self.save(record)
            raise
        return record, booked

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
