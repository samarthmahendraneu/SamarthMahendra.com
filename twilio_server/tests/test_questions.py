import asyncio
import importlib.util
import pathlib
import unittest
from unittest.mock import AsyncMock, Mock

import question_watch

from question_store import QuestionStore

REPO = pathlib.Path(__file__).resolve().parents[2]


def load_listener():
    """discord_listener.py runs on the pythonserver worker, so it lives there."""
    spec = importlib.util.spec_from_file_location(
        "discord_listener", REPO / "pythonserver" / "discord_listener.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def exists(self, key):
        return int(key in self.values)

    def delete(self, key):
        self.values.pop(key, None)


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

    def test_claim_is_atomic_across_processes(self):
        qid = self.store.ask("Is he free?")
        self.store.pop_for_posting()
        self.store.request_callback(qid, "Alice", "+16175550123")
        self.store.answer("Yes")
        self.assertTrue(self.store.claim_callback(qid))
        # A second process that read the record before the first claim saved it
        # still sees "requested"; the SET NX claim key must stop it anyway.
        record = self.store.get(qid)
        record["callback_state"] = "requested"
        self.store.save(record)
        self.assertFalse(self.store.claim_callback(qid))

    def test_live_key_tracks_whether_the_caller_is_on_the_line(self):
        qid = self.store.ask("Is he free?")
        self.assertFalse(self.store.is_live(qid))
        self.store.mark_live(qid)
        self.assertTrue(self.store.is_live(qid))
        self.store.clear_live(qid)
        self.assertFalse(self.store.is_live(qid))

    def test_delivered_flag_and_handoff_queue(self):
        first, second = self.store.ask("A?"), self.store.ask("B?")
        self.assertFalse(self.store.get(first)["delivered_live"])
        self.store.mark_delivered(first)
        self.assertTrue(self.store.get(first)["delivered_live"])
        self.store.queue_callback(first)
        self.store.queue_callback(second)
        self.assertEqual([self.store.pop_callback(), self.store.pop_callback(),
                          self.store.pop_callback()], [first, second, None])

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
        place_callback = load_listener().place_callback
        client = Mock()
        client.calls.create.return_value = Mock(sid="CA9")
        record = {"question": "Is he free Thursday?", "reply": "Thursday after 2pm",
                  "callback_name": "Alice", "callback_number": "+16175550123"}
        place_callback(record, client, "+18339703274")
        url = client.calls.create.call_args.kwargs["url"]
        self.assertIn("script=2", url)
        self.assertIn("Thursday+after+2pm", url)
        self.assertEqual(client.calls.create.call_args.kwargs["to"], "+16175550123")



class FakeBridge:
    def __init__(self):
        self.closing = False
        self.said = []
        self.accepting = True

    async def say(self, content):
        if not self.accepting:
            return False
        self.said.append(content)
        return True


def answered_question(store, reply="Thursday works", callback=False):
    qid = store.ask("Is he free Thursday?", "Alice")
    store.pop_for_posting()
    if callback:
        store.request_callback(qid, "Alice", "+16175550123")
    if reply is not None:
        store.answer(reply)
    return qid


class WatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = QuestionStore(FakeRedis())
        self.bridge = FakeBridge()

    async def run_watch(self, asked, ticks=3):
        task = asyncio.create_task(
            question_watch.watch_questions(self.bridge, asked, self.store, interval=0.01))
        await asyncio.sleep(0.01 * ticks + 0.02)
        self.bridge.closing = True
        await asyncio.wait_for(task, 1)

    async def test_reply_is_spoken_once_and_marked_delivered(self):
        qid = answered_question(self.store)
        await self.run_watch([qid], ticks=5)
        self.assertEqual(len(self.bridge.said), 1)
        self.assertIn("Thursday works", self.bridge.said[0])
        self.assertIn("Is he free Thursday?", self.bridge.said[0])
        self.assertTrue(self.store.get(qid)["delivered_live"])

    def test_commentary_mentions_the_dropped_callback_only_when_one_was_asked_for(self):
        plain = self.store.get(answered_question(self.store))
        asked = self.store.get(answered_question(self.store, callback=True))
        self.assertNotIn("call back", question_watch.reply_commentary(plain))
        self.assertIn("no longer needed", question_watch.reply_commentary(asked))

    def test_commentary_stays_well_under_the_500_token_limit(self):
        # A rejected append used to end the call, and a Discord reply can run to
        # thousands of characters.
        record = self.store.get(answered_question(self.store, reply="word " * 2000))
        said = question_watch.reply_commentary(record)
        self.assertLess(len(said), 1400)       # ~350 tokens at ~4 characters each
        self.assertIn("…", said)

    async def test_unanswered_question_stays_live_and_silent(self):
        qid = answered_question(self.store, reply=None)
        await self.run_watch([qid])
        self.assertEqual(self.bridge.said, [])
        self.assertTrue(self.store.is_live(qid))

    async def test_reply_not_marked_delivered_if_the_call_would_not_take_it(self):
        qid = answered_question(self.store)
        self.bridge.accepting = False          # session closing
        await self.run_watch([qid])
        self.assertFalse(self.store.get(qid)["delivered_live"])

    async def test_call_end_hands_an_unspoken_reply_to_the_listener(self):
        missed = answered_question(self.store, callback=True)
        heard = answered_question(self.store, callback=True)
        self.store.mark_delivered(heard)
        pending = answered_question(self.store, reply=None, callback=True)
        for qid in (missed, heard, pending):
            self.store.mark_live(qid)
        await question_watch.finish_questions([missed, heard, pending], self.store)
        # Only the reply that landed but was never spoken needs a ring-back now;
        # the pending one is rung back by the listener when its reply arrives.
        self.assertEqual(self.store.pop_callback(), missed)
        self.assertIsNone(self.store.pop_callback())
        self.assertFalse(any(self.store.is_live(q) for q in (missed, heard, pending)))


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import discord
        self.store = QuestionStore(FakeRedis())
        self.twilio = Mock()
        self.twilio.calls.create.return_value = Mock(sid="CA9")
        self.listener = load_listener().Listener(
            self.store, 123, self.twilio, "+18339703274", intents=discord.Intents.none())
        self.listener.channel = Mock(send=AsyncMock())

    def posted(self):
        return " ".join(c.args[0] for c in self.listener.channel.send.await_args_list)

    async def test_reply_while_caller_is_on_the_line_is_not_called_back(self):
        qid = answered_question(self.store, reply=None, callback=True)
        self.store.mark_live(qid)
        await self.listener.on_reply("Thursday works")
        self.twilio.calls.create.assert_not_called()
        self.assertIn("still on the call", self.posted())

    async def test_reply_after_hang_up_rings_back_when_asked(self):
        answered_question(self.store, reply=None, callback=True)
        await self.listener.on_reply("Thursday works")
        self.twilio.calls.create.assert_called_once()
        self.assertIn("back now", self.posted())

    async def test_reply_after_hang_up_without_callback_is_just_saved(self):
        answered_question(self.store, reply=None)
        await self.listener.on_reply("Thursday works")
        self.twilio.calls.create.assert_not_called()
        self.assertIn("already hung up", self.posted())

    async def test_handed_over_callback_is_placed_once(self):
        qid = answered_question(self.store, callback=True)
        self.store.queue_callback(qid)
        self.store.queue_callback(qid)         # both sides decided it was due
        self.listener.is_closed = Mock(side_effect=[False, False, True])
        await self.listener.post_pending()
        self.twilio.calls.create.assert_called_once()


class SharedStoreTests(unittest.TestCase):
    def test_both_services_use_the_same_question_store(self):
        # The voice agent (twilio_server) and the listener (pythonserver) are
        # deployed from separate folders, so each carries a copy. Letting them
        # drift is how a worker ends up not knowing a task the web service sends.
        voice = (REPO / "twilio_server" / "question_store.py").read_text()
        listener = (REPO / "pythonserver" / "question_store.py").read_text()
        self.assertEqual(voice, listener,
                         "question_store.py differs between twilio_server/ and pythonserver/")


if __name__ == "__main__":
    unittest.main()
