"""An in-memory stand-in for the parts of Redis the stores use.

Covers strings, lists, sorted sets and streams, with redis-py's call shapes.
TTLs are recorded, not enforced; a test that needs expiry clears keys itself.
"""

import itertools
import time


def _id_key(entry_id):
    ms, _, seq = str(entry_id).partition("-")
    return int(ms), int(seq or 0)


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}
        self.zsets = {}
        self.streams = {}
        self.expirations = {}
        self._ids = itertools.count(1)

    # ---- keys ----

    def exists(self, key):
        return int(key in self.values or key in self.lists or key in self.zsets
                   or key in self.streams)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.values, self.lists, self.zsets, self.streams):
                if store.pop(key, None) is not None:
                    removed += 1
        return removed

    def expire(self, key, ttl):
        self.expirations[key] = ttl
        return True

    # ---- strings ----

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.expirations[key] = ttl
        return True

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def eval(self, script, key_count, *keys_and_args):
        """Only the atomic GET-and-DELETE the call context store uses."""
        return self.values.pop(keys_and_args[0], None)

    # ---- lists ----

    def rpush(self, key, *values):
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    def lpop(self, key):
        items = self.lists.get(key) or []
        return items.pop(0) if items else None

    def lrem(self, key, count, value):
        items = self.lists.get(key) or []
        kept = [item for item in items if item != value]
        self.lists[key] = kept
        return len(items) - len(kept)

    def lrange(self, key, start, end):
        items = self.lists.get(key) or []
        end = len(items) if end == -1 else end + 1
        return list(items[start:end])

    # ---- sorted sets ----

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrem(self, key, *members):
        zset = self.zsets.get(key) or {}
        return sum(1 for member in members if zset.pop(member, None) is not None)

    def zscore(self, key, member):
        return (self.zsets.get(key) or {}).get(member)

    def zrangebyscore(self, key, low, high, start=None, num=None):
        low = float("-inf") if low == "-inf" else float(low)
        high = float("inf") if high == "+inf" else float(high)
        members = sorted((score, member) for member, score in (self.zsets.get(key) or {}).items()
                         if low <= score <= high)
        members = [member for _, member in members]
        if start is not None:
            members = members[start:start + num if num is not None else None]
        return members

    # ---- streams ----

    def xadd(self, key, fields, maxlen=None, approximate=True):
        # Real ids are "<ms>-<seq>"; a global sequence keeps them increasing.
        entry_id = f"{int(time.time() * 1000)}-{next(self._ids)}"
        last = self.streams.get(key, [])
        if last and _id_key(entry_id) <= _id_key(last[-1][0]):
            entry_id = f"{_id_key(last[-1][0])[0]}-{next(self._ids)}"
        entries = self.streams.setdefault(key, [])
        entries.append((entry_id, dict(fields)))
        if maxlen is not None and len(entries) > maxlen:
            del entries[:len(entries) - maxlen]
        return entry_id

    def xread(self, streams, count=None, block=None):
        result = []
        for key, after in streams.items():
            entries = [(entry_id, fields) for entry_id, fields in self.streams.get(key, [])
                       if _id_key(entry_id) > _id_key(after)]
            if count is not None:
                entries = entries[:count]
            if entries:
                result.append([key, entries])
        return result

    def xrevrange(self, key, max="+", min="-", count=None):
        entries = list(reversed(self.streams.get(key, [])))
        return entries[:count] if count is not None else entries
