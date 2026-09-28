import asyncio
import re
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, PropertyMock, patch
from zoneinfo import ZoneInfo

import call_events
from callbacks import CallbackStore
from events import EventStream
from memory_redis import MemoryRedis
from worker_modules import REPO, load
from question_store import LIVE_KEY, QuestionStore

CALL = "call:CA" + "1" * 32
CHAT = "chat:" + "a" * 32
EASTERN = ZoneInfo("America/New_York")


def eastern(hour):
    return datetime(2026, 10, 1, hour, 0, tzinfo=EASTERN).timestamp()


def pacific(hour):
    return datetime(2026, 10, 1, hour, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()


def posted_question(store, question="Is he free Thursday?", origin=CALL, callback=False,
                    reply=None, message_id=None):
    qid = store.ask(question, "Alice", origin)
    store.pop_for_posting()
    if message_id is not None:
        store.remember_post(qid, message_id)
    if callback:
        store.request_callback(qid, "Alice", "+16175550123")
    if reply is not None:
        store.answer(qid, reply)
    return qid


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = QuestionStore(MemoryRedis())

    def test_question_is_queued_then_posted_once(self):
        qid = self.store.ask("Is he free Thursday?", "Alice", CALL)
        record = self.store.get(qid)
        self.assertEqual((record["status"], record["origin"]), ("pending", CALL))
        self.assertEqual(self.store.pop_for_posting()["id"], qid)
        self.assertEqual(self.store.get(qid)["status"], "asked")
        self.assertIsNone(self.store.pop_for_posting())

    def test_a_reply_is_matched_by_the_message_it_replies_to(self):
        first = posted_question(self.store, "Free Thursday?", message_id=111)
        second = posted_question(self.store, "Interested?", message_id=222)
        self.assertEqual(self.store.question_for_post(222), second)
        self.assertEqual(self.store.question_for_post("111"), first)
        self.assertIsNone(self.store.question_for_post(333))
        self.store.answer(second, "Yes, very")
        self.assertEqual(self.store.get(second)["reply"], "Yes, very")
        self.assertIsNone(self.store.get(first)["reply"])

    def test_open_questions_are_the_ones_still_waiting(self):
        answered = posted_question(self.store, "A?")
        waiting = posted_question(self.store, "B?")
        expired = posted_question(self.store, "C?")
        self.store.answer(answered, "yes")
        del self.store.redis.values["discord:q:" + expired]      # TTL elapsed
        self.assertEqual([r["id"] for r in self.store.open_questions()], [waiting])
        self.assertEqual(self.store.redis.lists["discord:open"], [waiting])

    def test_a_question_is_answered_once_and_later_messages_are_follow_ups(self):
        qid = posted_question(self.store, reply="Yes")
        self.assertIsNone(self.store.answer(qid, "No"))
        self.assertEqual(self.store.get(qid)["reply"], "Yes")
        record = self.store.add_followup(qid, "Actually, after 3pm")
        self.assertEqual([f["text"] for f in record["followups"]], ["Actually, after 3pm"])
        unanswered = posted_question(self.store)
        self.assertIsNone(self.store.add_followup(unanswered, "hm"))

    def test_callback_is_claimed_once_so_the_caller_is_not_rung_twice(self):
        qid = posted_question(self.store, callback=True, reply="Yes")
        self.assertTrue(self.store.claim_callback(qid))
        self.assertFalse(self.store.claim_callback(qid))

    def test_callback_is_not_placed_when_none_was_requested(self):
        qid = posted_question(self.store, reply="Yes")
        self.assertFalse(self.store.claim_callback(qid))

    def test_claim_is_atomic_across_processes(self):
        qid = posted_question(self.store, callback=True, reply="Yes")
        self.assertTrue(self.store.claim_callback(qid))
        # A second process that read the record before the first claim saved it
        # still sees "requested"; the SET NX claim key must stop it anyway.
        record = self.store.get(qid)
        record["callback_state"] = "requested"
        self.store.save(record)
        self.assertFalse(self.store.claim_callback(qid))

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


class CallEventTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        redis = MemoryRedis()
        self.store = QuestionStore(redis)
        self.events = EventStream(redis)
        self.bridge = FakeBridge()

    async def run_watch(self, ticks=3):
        task = asyncio.create_task(call_events.watch_call(
            self.bridge, CALL, self.events, self.store, interval=0.01))
        await asyncio.sleep(0.01 * ticks + 0.02)
        self.bridge.closing = True
        await asyncio.wait_for(task, 1)

    async def test_reply_is_spoken_once_and_marked_delivered(self):
        qid = posted_question(self.store, reply="Thursday works")
        self.events.publish(CALL, "question.answered", question_id=qid)
        await self.run_watch(ticks=5)
        self.assertEqual(len(self.bridge.said), 1)
        self.assertIn("Thursday works", self.bridge.said[0])
        self.assertIn("Is he free Thursday?", self.bridge.said[0])
        self.assertTrue(self.store.get(qid)["delivered_live"])

    async def test_finished_and_failed_jobs_are_told_to_the_caller(self):
        self.events.publish(CALL, "job.done", job={"label": "emailing the invite to a@example.com",
                                                   "status": "done"})
        self.events.publish(CALL, "job.failed", job={"label": "emailing the invite to a@example",
                                                     "status": "failed", "error": "the email address was rejected"})
        await self.run_watch()
        done, failed = self.bridge.said
        self.assertIn("now done: emailing the invite to a@example.com", done)
        self.assertIn("did not work", failed)
        self.assertIn("the email address was rejected", failed)

    async def test_follow_up_from_samarth_is_spoken_too(self):
        qid = posted_question(self.store, reply="Yes")
        self.store.add_followup(qid, "After 3pm though")
        self.events.publish(CALL, "question.followup", question_id=qid, text="After 3pm though")
        await self.run_watch()
        self.assertIn("After 3pm though", self.bridge.said[0])

    def test_commentary_mentions_the_dropped_callback_only_when_one_was_asked_for(self):
        plain = self.store.get(posted_question(self.store, reply="Yes"))
        asked = self.store.get(posted_question(self.store, reply="Yes", callback=True))
        self.assertNotIn("call back", call_events.reply_commentary(plain))
        self.assertIn("no longer needed", call_events.reply_commentary(asked))

    def test_commentary_stays_well_under_the_500_token_limit(self):
        # A rejected append used to end the call, and a Discord reply can run to
        # thousands of characters.
        record = self.store.get(posted_question(self.store, reply="word " * 2000))
        said = call_events.reply_commentary(record)
        self.assertLess(len(said), 1400)       # ~350 tokens at ~4 characters each
        self.assertIn("…", said)
        job = call_events.job_commentary({"label": "x" * 5000, "status": "failed", "error": "y" * 5000})
        self.assertLess(len(job), 1000)

    async def test_the_call_stays_live_while_nothing_has_happened(self):
        await self.run_watch()
        self.assertEqual(self.bridge.said, [])
        self.assertTrue(self.events.is_live(CALL))

    async def test_news_not_taken_by_a_closing_call_is_tried_again(self):
        qid = posted_question(self.store, reply="Yes")
        self.events.publish(CALL, "question.answered", question_id=qid)
        self.bridge.accepting = False
        task = asyncio.create_task(call_events.watch_call(
            self.bridge, CALL, self.events, self.store, interval=0.01))
        await asyncio.sleep(0.05)
        self.assertFalse(self.store.get(qid)["delivered_live"])
        self.bridge.accepting = True
        await asyncio.sleep(0.05)
        self.bridge.closing = True
        await asyncio.wait_for(task, 1)
        self.assertEqual(len(self.bridge.said), 1)
        self.assertTrue(self.store.get(qid)["delivered_live"])

    async def test_call_end_hands_an_unspoken_reply_to_the_listener(self):
        missed = posted_question(self.store, reply="Yes", callback=True)
        heard = posted_question(self.store, reply="Yes", callback=True)
        self.store.mark_delivered(heard)
        pending = posted_question(self.store, callback=True)
        self.events.mark_live(CALL)
        await call_events.finish_call(CALL, [missed, heard, pending], self.events, self.store)
        # Only the reply that landed but was never spoken needs a ring-back now;
        # the pending one is rung back by the listener when its reply arrives.
        self.assertEqual(self.store.pop_callback(), missed)
        self.assertIsNone(self.store.pop_callback())
        self.assertFalse(self.events.is_live(CALL))


class ListenerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import discord
        redis = MemoryRedis()
        self.store = QuestionStore(redis)
        self.events = EventStream(redis)
        self.callbacks = CallbackStore(redis)
        self.chat_followup = Mock()
        self.listener = load("discord_listener").Listener(
            self.store, 123, Mock(), "+18339703274", events=self.events,
            callbacks=self.callbacks, chat_followup=self.chat_followup,
            intents=discord.Intents.none())
        self.message_ids = iter(range(900, 999))
        self.listener.channel = Mock(send=AsyncMock(
            side_effect=lambda *a, **k: Mock(id=next(self.message_ids))))
        self.listener.scheduler = Mock()        # as setup_hook leaves it when Twilio is set up

    def posted(self):
        return " ".join(c.args[0] for c in self.listener.channel.send.await_args_list)

    def booked(self):
        return [r for r in (self.callbacks.get(k.split(":")[-1]) for k in list(self.store.redis.values)
                            if k.startswith("callback:"))]

    async def test_reply_to_a_question_answers_that_question_among_several(self):
        first = posted_question(self.store, "Free Thursday?", message_id=111)
        second = posted_question(self.store, "Interested?", message_id=222)
        await self.listener.on_reply("Yes, very", reference=222)
        self.assertEqual(self.store.get(second)["reply"], "Yes, very")
        self.assertEqual(self.store.get(first)["status"], "asked")
        ((_, kind, data),) = self.events.read(CALL)
        self.assertEqual((kind, data["question_id"]), ("question.answered", second))

    async def test_a_plain_message_answers_the_only_open_question(self):
        qid = posted_question(self.store)
        await self.listener.on_reply("Thursday works")
        self.assertEqual(self.store.get(qid)["reply"], "Thursday works")

    async def test_with_several_waiting_a_plain_message_is_not_guessed(self):
        first, second = posted_question(self.store, "A?"), posted_question(self.store, "B?")
        await self.listener.on_reply("Yes")
        self.assertEqual({self.store.get(q)["status"] for q in (first, second)}, {"asked"})
        self.assertIn("Reply to the question", self.posted())

    async def test_a_reply_to_some_other_message_is_not_guessed(self):
        qid = posted_question(self.store, message_id=111)
        await self.listener.on_reply("Yes", reference=555)
        self.assertEqual(self.store.get(qid)["status"], "asked")
        self.assertIn("Reply to the question", self.posted())

    async def test_chatter_with_nothing_open_is_ignored(self):
        await self.listener.on_reply("unrelated channel message")
        self.listener.channel.send.assert_not_awaited()

    async def test_reply_while_caller_is_on_the_line_is_not_called_back(self):
        posted_question(self.store, callback=True)
        self.events.mark_live(CALL)
        await self.listener.on_reply("Thursday works")
        self.assertEqual(self.booked(), [])
        self.assertIn("still on the call", self.posted())

    async def test_a_caller_from_before_the_event_streams_still_counts_as_live(self):
        qid = self.store.ask("Free?", "Alice", None)
        self.store.pop_for_posting()
        self.store.request_callback(qid, "Alice", "+16175550123")
        self.store.redis.setex(LIVE_KEY + qid, 10, "1")
        await self.listener.on_reply("Yes")
        self.assertEqual(self.booked(), [])
        self.assertIn("still on the call", self.posted())

    async def test_reply_after_hang_up_books_a_call_back_when_asked(self):
        qid = posted_question(self.store, callback=True)
        with patch("time.time", return_value=eastern(14)):
            await self.listener.on_reply("Thursday works")
        (record,) = self.booked()
        self.assertEqual((record["to"], record["question_id"], record["due_at"]),
                         ("+16175550123", qid, eastern(14)))
        self.assertIn("Thursday works", record["purpose"])
        self.assertIn("Thursday works", record["voicemail"])
        self.assertIn("back now", self.posted())

    async def test_a_worker_that_cant_dial_says_so_instead_of_promising_a_call(self):
        self.listener.scheduler = None
        posted_question(self.store, callback=True)
        with patch("time.time", return_value=eastern(14)):
            await self.listener.on_reply("Yes")
        self.assertEqual(len(self.booked()), 1)          # kept for when it can dial
        self.assertIn("can't place calls", self.posted())
        self.assertIn("TWILIO_ACCOUNT_SID", self.posted())
        self.assertNotIn("back now", self.posted())

    async def test_a_late_reply_is_called_back_in_the_morning(self):
        posted_question(self.store, callback=True)
        with patch("time.time", return_value=eastern(23)):
            await self.listener.on_reply("Yes")
        (record,) = self.booked()
        self.assertEqual(record["due_at"], eastern(10) + 86400)
        self.assertIn("outside calling hours", self.posted())

    async def test_a_late_reply_waits_for_morning_where_the_caller_is(self):
        qid = self.store.ask("Free?", "Bo", CALL)
        self.store.pop_for_posting()
        self.store.request_callback(qid, "Bo", "+14155550123")
        with patch("time.time", return_value=pacific(22)):
            await self.listener.on_reply("Yes")
        (record,) = self.booked()
        self.assertEqual(record["due_at"], pacific(10) + 86400)
        self.assertIn("Friday 10:00 AM PDT (1:00 PM EDT)", self.posted())

    async def test_the_zone_a_caller_gave_is_used_for_their_call_back(self):
        qid = self.store.ask("Free?", "Bo", CALL)
        self.store.pop_for_posting()
        self.store.request_callback(qid, "Bo", "+16175550123", "America/Chicago")
        with patch("time.time", return_value=eastern(9)):          # 8am in Chicago
            await self.listener.on_reply("Yes")
        (record,) = self.booked()
        self.assertEqual((record["timezone"], record["due_at"]), ("America/Chicago", eastern(11)))

    async def test_reply_after_hang_up_without_callback_is_just_saved(self):
        posted_question(self.store)
        await self.listener.on_reply("Thursday works")
        self.assertEqual(self.booked(), [])
        self.assertIn("already hung up", self.posted())

    async def test_a_chat_question_is_answered_in_the_chat(self):
        qid = posted_question(self.store, origin=CHAT)
        await self.listener.on_reply("Yes")
        self.chat_followup.assert_called_once_with("a" * 32)
        ((_, kind, data),) = self.events.read(CHAT)
        self.assertEqual((kind, data["question_id"]), ("question.answered", qid))
        self.assertIn("chat window", self.posted())

    async def test_a_chat_visitor_who_left_is_rung_when_samarth_answers(self):
        qid = posted_question(self.store, origin=CHAT, callback=True)
        with patch("time.time", return_value=eastern(14)):
            await self.listener.on_reply("Yes")
        (record,) = self.booked()
        self.assertEqual((record["question_id"], record["origin"]), (qid, CHAT))
        self.chat_followup.assert_called_once_with("a" * 32)       # there if they come back
        self.assertIn("back now", self.posted())

    async def test_a_chat_visitor_still_there_just_sees_it(self):
        posted_question(self.store, origin=CHAT, callback=True)
        self.events.mark_live(CHAT)
        await self.listener.on_reply("Yes")
        self.assertEqual(self.booked(), [])
        self.assertIn("still in the chat", self.posted())

    async def test_a_second_reply_is_passed_on_as_a_follow_up(self):
        qid = posted_question(self.store, reply="Yes", message_id=111)
        self.events.mark_live(CALL)
        await self.listener.on_reply("After 3pm though", reference=111)
        ((_, kind, data),) = self.events.read(CALL)
        self.assertEqual((kind, data["question_id"], data["text"]),
                         ("question.followup", qid, "After 3pm though"))
        self.assertEqual(self.store.get(qid)["reply"], "Yes")

    async def test_a_reply_to_the_bots_own_note_counts_as_a_reply_to_the_question(self):
        qid = posted_question(self.store, message_id=111, origin=CHAT)
        await self.listener.on_reply("Yes")                 # the note is message 900
        record = self.store.get(qid)
        self.assertEqual(self.store.question_for_post(900), qid)
        await self.listener.on_reply("And bring a CV", reference=900)
        self.assertEqual([e[1] for e in self.events.read(CHAT)],
                         ["question.answered", "question.followup"])
        self.assertEqual(record["reply"], "Yes")

    async def test_messages_are_routed_from_the_channel_and_its_threads_only(self):
        self.listener.on_reply = AsyncMock()
        me = Mock(id=1)
        user = patch.object(type(self.listener), "user", new_callable=PropertyMock, return_value=me)
        user.start()
        self.addCleanup(user.stop)
        cases = [
            (Mock(author=Mock(id=2), channel=Mock(id=123), reference=Mock(message_id=111)), 111),
            (Mock(author=Mock(id=2), channel=Mock(id=123), reference=None), None),
            # A thread started from question 111 has id 111.
            (Mock(author=Mock(id=2), channel=Mock(id=111, parent_id=123), reference=None), 111),
        ]
        for message, reference in cases:
            await self.listener.on_message(message)
            self.assertEqual(self.listener.on_reply.await_args.args[1], reference)
        self.listener.on_reply.reset_mock()
        for ignored in (Mock(author=me, channel=Mock(id=123)),
                        Mock(author=Mock(id=2), channel=Mock(id=555, parent_id=999))):
            await self.listener.on_message(ignored)
        self.listener.on_reply.assert_not_awaited()

    async def test_posting_a_question_remembers_its_message(self):
        qid = self.store.ask("Free?", "Alice", CHAT)
        self.listener.is_closed = Mock(side_effect=[False, True])
        await self.listener.post_pending()
        self.assertEqual(self.store.question_for_post(900), qid)
        self.assertIn("on the website chat", self.posted())

    async def test_handed_over_callback_is_booked_once(self):
        qid = posted_question(self.store, reply="Yes", callback=True)
        self.store.queue_callback(qid)
        self.store.queue_callback(qid)         # both sides decided it was due
        self.listener.is_closed = Mock(side_effect=[False, False, True])
        await self.listener.post_pending()
        self.assertEqual(len(self.booked()), 1)


class SharedModuleTests(unittest.TestCase):
    def test_both_services_carry_the_same_shared_modules(self):
        # The voice agent (twilio_server) and the worker, listener and chat
        # (pythonserver) deploy from separate folders, so each carries a copy.
        # Letting them drift is how one side stops understanding the other.
        for name in ("question_store.py", "events.py", "jobs.py", "callbacks.py", "timezones.py",
                     "redis_pool.py"):
            with self.subTest(name):
                self.assertEqual((REPO / "twilio_server" / name).read_text(),
                                 (REPO / "pythonserver" / name).read_text(),
                                 f"{name} differs between twilio_server/ and pythonserver/")


    def test_every_task_sent_by_name_is_one_the_worker_runs(self):
        # There is one Celery worker (pythonserver/celery_worker.py). The voice
        # service and the listener queue work by name only, so a name the
        # worker doesn't define would be dropped on arrival, and only this
        # test would notice.
        worker = (REPO / "pythonserver" / "celery_worker.py").read_text()
        defined = set(re.findall(r'@celery_app\.task\([^)]*name="([\w.]+)"', worker))
        defined |= {"celery_worker." + name for name in
                    re.findall(r'@celery_app\.task(?:\(bind=True\))?\ndef (\w+)', worker)}
        sent = set()
        for folder in ("twilio_server", "pythonserver"):
            for path in (REPO / folder).glob("*.py"):
                sent |= set(re.findall(r'send_task\("([\w.]+)"', path.read_text()))
        self.assertEqual(sent, {"celery_worker.run_job", "celery_worker.chat_followup"})
        self.assertLessEqual(sent, defined)
        self.assertIn("celery_worker.tool_call_fn", defined)     # for work queued before the redeploy


if __name__ == "__main__":
    unittest.main()
