import unittest
from unittest.mock import Mock

from question_store import QuestionStore


class FakeRedis:
    """Enough of Redis for the store: string keys and list queues."""

    def __init__(self):
        self.values = {}
        self.lists = {}

    def setex(self, key, ttl, value):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lpop(self, key):
        items = self.lists.get(key) or []
        return items.pop(0) if items else None

    def lrem(self, key, count, value):
        items = self.lists.get(key) or []
        self.lists[key] = [i for i in items if i != value]


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = QuestionStore(FakeRedis())

    def test_question_is_queued_then_posted_once(self):
        qid = self.store.ask("Is he free Thursday?", "Alice", "CA1")
        self.assertEqual(self.store.get(qid)["status"], "pending")
        record = self.store.pop_for_posting()
        self.assertEqual(record["id"], qid)
        self.assertEqual(self.store.get(qid)["status"], "asked")
        self.assertIsNone(self.store.pop_for_posting())

    def test_reply_answers_the_oldest_open_question(self):
        first = self.store.ask("Is he free Thursday?")
        second = self.store.ask("Is he interested?")
        self.store.pop_for_posting()
        self.store.pop_for_posting()
        self.store.answer("Thursday works")
        self.store.answer("Yes, very")
        self.assertEqual(self.store.get(first)["reply"], "Thursday works")
        self.assertEqual(self.store.get(second)["reply"], "Yes, very")

    def test_chatter_with_nothing_open_is_not_recorded_as_an_answer(self):
        self.assertIsNone(self.store.answer("unrelated channel message"))

    def test_a_question_is_not_answered_twice(self):
        qid = self.store.ask("Is he free?")
        self.store.pop_for_posting()
        self.store.answer("Yes")
        self.assertIsNone(self.store.answer("No", qid))
        self.assertEqual(self.store.get(qid)["reply"], "Yes")

    def test_callback_is_claimed_once_so_the_caller_is_not_rung_twice(self):
        qid = self.store.ask("Is he free?")
        self.store.pop_for_posting()
        self.store.request_callback(qid, "Alice", "+16175550123")
        self.store.answer("Yes")
        self.assertTrue(self.store.claim_callback(qid))
        self.assertFalse(self.store.claim_callback(qid))

    def test_callback_is_not_placed_when_none_was_requested(self):
        qid = self.store.ask("Is he free?")
        self.store.pop_for_posting()
        self.store.answer("Yes")
        self.assertFalse(self.store.claim_callback(qid))

    def test_expired_question_does_not_crash_the_listener(self):
        qid = self.store.ask("Is he free?")
        self.store.redis.values.clear()     # TTL elapsed before posting
        self.assertIsNone(self.store.pop_for_posting())
        self.assertIsNone(self.store.get(qid))

    def test_invalid_question_id_is_rejected(self):
        for bad in ("", "../etc", "nope", "z" * 32):
            with self.assertRaises(ValueError):
                self.store.get(bad)


class CallbackMessageTests(unittest.TestCase):
    def test_callback_call_carries_the_question_and_answer(self):
        from discord_listener import place_callback
        client = Mock()
        client.calls.create.return_value = Mock(sid="CA9")
        record = {"question": "Is he free Thursday?", "reply": "Thursday after 2pm",
                  "callback_name": "Alice", "callback_number": "+16175550123"}
        place_callback(record, client, "+18339703274")
        url = client.calls.create.call_args.kwargs["url"]
        self.assertIn("script=2", url)
        self.assertIn("Thursday+after+2pm", url)
        self.assertEqual(client.calls.create.call_args.kwargs["to"], "+16175550123")


if __name__ == "__main__":
    unittest.main()
