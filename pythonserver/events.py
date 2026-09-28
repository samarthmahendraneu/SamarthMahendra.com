"""Per-conversation event streams: how background work reports back.

Every conversation is a channel: "call:<Twilio call SID>" for a phone call,
"chat:<session id>" for a chat on the website. Whatever finishes work for a
conversation -- Samarth answering on Discord, a background job, an outbound
call getting its answer -- appends an event to that channel's Redis stream.
The conversation reads its own stream and tells the person: the voice bridge
speaks it, the chat pushes it to the browser. Nothing polls the thing it is
waiting on, and a browser that reconnects picks up where it left off.

Kept byte-identical in twilio_server/ and pythonserver/; the tests fail if the
copies drift.
"""

import json
import re
import time

STREAM_KEY = "events:"
LIVE_KEY = "events-live:"
# A day: long enough for a browser to come back and catch up.
STREAM_TTL = 86400
MAX_EVENTS = 500
# A live call refreshes its key every second, so one whose process died
# without clearing it stops counting as live ten seconds later.
LIVE_TTL = 10
START = "0-0"
CHANNEL_PATTERN = re.compile(r"(call|chat):[A-Za-z0-9_-]{1,64}")


def valid_channel(channel):
    return isinstance(channel, str) and bool(CHANNEL_PATTERN.fullmatch(channel))


def channel_kind(channel):
    return channel.split(":", 1)[0]


def channel_id(channel):
    return channel.split(":", 1)[1]


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


class EventStream:
    def __init__(self, redis):
        self.redis = redis

    def key(self, channel, prefix=STREAM_KEY):
        if not valid_channel(channel):
            raise ValueError("Invalid event channel")
        return prefix + channel

    def publish(self, channel, kind, **data):
        """Append an event; returns its id, which orders it within the channel."""
        key = self.key(channel)
        event_id = self.redis.xadd(
            key, {"kind": kind, "data": json.dumps(data), "at": repr(time.time())},
            maxlen=MAX_EVENTS, approximate=True)
        self.redis.expire(key, STREAM_TTL)
        return _text(event_id)

    def read(self, channel, after=START, count=100):
        """Events after the id `after`, oldest first, as (id, kind, data).

        Never blocks: callers poll, which keeps a slow Redis from holding a
        thread and works with the short socket timeouts the services use.
        """
        found = self.redis.xread({self.key(channel): after or START}, count=count)
        events = []
        for _stream, entries in found or []:
            for event_id, fields in entries:
                fields = {_text(k): _text(v) for k, v in fields.items()}
                try:
                    data = json.loads(fields.get("data") or "{}")
                except ValueError:
                    data = {}
                events.append((_text(event_id), fields.get("kind", ""), data))
        return events

    def latest(self, channel):
        """The newest event id, or START: where a new reader should begin."""
        entries = self.redis.xrevrange(self.key(channel), count=1)
        return _text(entries[0][0]) if entries else START

    # ---- is anyone there? ----

    def mark_live(self, channel, ttl=LIVE_TTL):
        self.redis.setex(self.key(channel, LIVE_KEY), ttl, "1")

    def clear_live(self, channel):
        self.redis.delete(self.key(channel, LIVE_KEY))

    def is_live(self, channel):
        return bool(self.redis.exists(self.key(channel, LIVE_KEY)))
