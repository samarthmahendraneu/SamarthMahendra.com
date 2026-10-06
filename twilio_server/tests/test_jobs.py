import os
import smtplib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from events import START, EventStream
from jobs import JobStore
from memory_redis import MemoryRedis
from worker_modules import load

CALL = "call:CA" + "1" * 32
CHAT = "chat:" + "a" * 32


class EventStreamTests(unittest.TestCase):
    def setUp(self):
        self.events = EventStream(MemoryRedis())

    def test_events_are_read_in_order_from_a_cursor(self):
        first = self.events.publish(CALL, "job.done", job={"id": "1"})
        second = self.events.publish(CALL, "question.answered", question_id="q")
        self.assertEqual([e[0] for e in self.events.read(CALL)], [first, second])
        ((event_id, kind, data),) = self.events.read(CALL, first)
        self.assertEqual((event_id, kind, data), (second, "question.answered", {"question_id": "q"}))
        self.assertEqual(self.events.latest(CALL), second)
        self.assertEqual(self.events.latest(CHAT), START)

    def test_channels_are_separate_and_expire(self):
        self.events.publish(CALL, "job.done")
        self.assertEqual(self.events.read(CHAT), [])
        self.assertEqual(self.events.redis.expirations["events:" + CALL], 86400)

    def test_live_marker(self):
        self.assertFalse(self.events.is_live(CALL))
        self.events.mark_live(CALL)
        self.assertTrue(self.events.is_live(CALL))
        self.events.clear_live(CALL)
        self.assertFalse(self.events.is_live(CALL))

    def test_only_known_channel_shapes_are_accepted(self):
        for bad in ("call:", "sms:123", "chat:../x", "call:a b", None, "call:" + "x" * 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.events.publish(bad, "job.done")


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.enqueue = Mock()
        self.jobs = JobStore(MemoryRedis(), enqueue=self.enqueue)

    def test_a_job_is_recorded_then_handed_to_the_worker(self):
        job_id = self.jobs.start("email.send", {"to": "a@example.com"}, origin=CALL,
                                 announce="always", label="emailing the invite")
        self.enqueue.assert_called_once_with(job_id)
        record = self.jobs.get(job_id)
        self.assertEqual((record["status"], record["origin"], record["label"]),
                         ("queued", CALL, "emailing the invite"))

    def test_a_job_the_worker_never_got_is_failed_not_left_pending(self):
        self.enqueue.side_effect = ConnectionError("broker down")
        with self.assertRaises(ConnectionError):
            self.jobs.start("email.send", {})
        (record,) = [JobStore.summary(r) for r in map(self.jobs.get, self.job_ids())]
        self.assertEqual(record["status"], "failed")
        self.assertIn("could not be queued", record["error"])

    def job_ids(self):
        return [k.split(":", 1)[1] for k in self.jobs.redis.values if k.startswith("job:")]

    def test_a_job_is_claimed_by_one_worker_only(self):
        job_id = self.jobs.start("email.send", {})
        self.assertEqual(self.jobs.claim(job_id)["status"], "running")
        self.assertIsNone(self.jobs.claim(job_id))
        self.jobs.finish(job_id, {"status": "sent"})
        self.jobs.redis.values.pop("job-claim:" + job_id)     # the claim expired
        self.assertIsNone(self.jobs.claim(job_id))            # but it's done

    def test_who_hears_about_a_job(self):
        cases = [("always", "done", True), ("always", "failed", True),
                 ("failure", "done", False), ("failure", "failed", True),
                 ("never", "failed", False)]
        for policy, status, expected in cases:
            with self.subTest(policy=policy, status=status):
                record = {"origin": CALL, "announce": policy, "status": status}
                self.assertEqual(JobStore.should_announce(record), expected)
        self.assertFalse(JobStore.should_announce({"origin": None, "announce": "always",
                                                   "status": "done"}))

    def test_description_for_check_task(self):
        job_id = self.jobs.start("email.send", {"to": "x"}, label="emailing the invite")
        self.assertEqual(self.jobs.describe(self.jobs.get(job_id))["status"], "queued")
        self.jobs.fail(job_id, "the email address was rejected")
        described = self.jobs.describe(self.jobs.get(job_id))
        self.assertEqual((described["status"], described["error"]),
                         ("failed", "the email address was rejected"))
        self.assertNotIn("to", described)        # arguments stay private

    def test_ids_are_checked(self):
        for bad in ("", "../job", "z" * 32):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.jobs.get(bad)


class RunJobTests(unittest.TestCase):
    def setUp(self):
        self.handlers = load("job_handlers")
        redis = MemoryRedis()
        self.jobs = JobStore(redis, enqueue=Mock())
        self.events = EventStream(redis)
        self.chat_news = Mock()
        self.notify = Mock()

    def run_job(self, kind="email.send", origin=CALL, announce="always", result=None, error=None):
        job_id = self.jobs.start(kind, {"to": "a@example.com"}, origin=origin, announce=announce,
                                 label="emailing the invite")

        def handler(args):
            if error:
                raise error
            return result or {"status": "sent"}

        record = self.handlers.run(job_id, self.jobs, self.events, self.chat_news, self.notify,
                                   handlers={kind: handler})
        return record, self.events.read(origin) if origin else []

    def test_a_finished_job_is_told_to_its_call(self):
        record, events = self.run_job()
        self.assertEqual(record["status"], "done")
        ((_, kind, data),) = events
        self.assertEqual((kind, data["job"]["label"], data["job"]["status"]),
                         ("job.done", "emailing the invite", "done"))

    def test_a_failure_carries_its_reason(self):
        error = self.handlers.JobError("the email address was rejected")
        record, events = self.run_job(announce="failure", error=error)
        self.assertEqual(record["error"], "the email address was rejected")
        self.assertEqual(events[0][1], "job.failed")

    def test_success_is_not_announced_when_only_failures_are(self):
        _, events = self.run_job(announce="failure")
        self.assertEqual(events, [])

    def test_an_unexpected_crash_is_reported_without_its_internals(self):
        with self.assertLogs("job_handlers", level="ERROR"):
            record, _ = self.run_job(error=KeyError("secret-internal-name"))
        self.assertEqual(record["status"], "failed")
        self.assertNotIn("secret-internal-name", record["error"])

    def test_a_chat_is_asked_to_follow_up(self):
        self.run_job(origin=CHAT)
        self.chat_news.assert_called_once_with("a" * 32)

    def test_a_failure_after_the_call_ended_goes_to_samarth(self):
        self.run_job(error=self.handlers.JobError("rejected"))
        self.assertIn("rejected", self.notify.call_args.args[0])
        self.notify.reset_mock()
        self.events.mark_live(CALL)                 # still on the line: they hear it
        self.run_job(error=self.handlers.JobError("rejected"))
        self.notify.assert_not_called()

    def test_a_failed_discord_post_is_not_reported_to_discord(self):
        self.run_job(kind="discord.send", error=self.handlers.JobError("HTTP 403"))
        self.notify.assert_not_called()

    def test_running_a_job_twice_does_it_once(self):
        job_id = self.jobs.start("email.send", {}, origin=CALL)
        handler = Mock(return_value={"status": "sent"})
        for _ in range(2):
            self.handlers.run(job_id, self.jobs, self.events, handlers={"email.send": handler})
        handler.assert_called_once()

    def test_an_unknown_kind_fails_instead_of_pretending(self):
        job_id = self.jobs.start("fax.send", {}, origin=CALL, announce="failure")
        record = self.handlers.run(job_id, self.jobs, self.events, handlers={})
        self.assertIn("doesn't know how", record["error"])


def response(status, body=None):
    return SimpleNamespace(status_code=status, json=lambda: body or {})


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.handlers = load("job_handlers")
        self.env = {"DISCORD_TOKEN": "t", "DISCORD_CHANNEL_ID": "42", "SMTP_HOST": "smtp.test",
                    "SMTP_USER": "me@test", "SMTP_PASS": "pw"}

    def test_discord_post_uses_rest_and_cannot_ping_everyone(self):
        post = Mock(return_value=response(200, {"id": "m1"}))
        result = self.handlers.send_discord({"content": "@everyone " + "x" * 3000}, env=self.env, post=post)
        self.assertEqual(result, {"status": "sent", "message_id": "m1"})
        url, kwargs = post.call_args.args[0], post.call_args.kwargs
        self.assertTrue(url.endswith("/channels/42/messages"))
        self.assertEqual(kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(len(kwargs["json"]["content"]), 2000)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bot t")

    def test_discord_rate_limit_is_waited_out_once(self):
        post = Mock(side_effect=[response(429, {"retry_after": 0}), response(200, {"id": "m2"})])
        self.assertEqual(self.handlers.send_discord({"content": "hi"}, env=self.env, post=post)["message_id"], "m2")

    def test_discord_refusal_or_missing_setup_is_a_job_error(self):
        with self.assertRaisesRegex(self.handlers.JobError, "HTTP 403"):
            self.handlers.send_discord({"content": "hi"}, env=self.env, post=Mock(return_value=response(403)))
        with self.assertRaisesRegex(self.handlers.JobError, "isn't set up"):
            self.handlers.send_discord({"content": "hi"}, env={}, post=Mock())

    def smtp(self, error=None):
        server = MagicMock()
        if error:
            server.send_message.side_effect = error
        factory = MagicMock()
        factory.return_value.__enter__.return_value = server
        return factory, server

    def test_email_is_sent_over_starttls(self):
        factory, server = self.smtp()
        args = {"to": "a@example.com", "subject": "Invite", "body": "Join at x"}
        self.assertEqual(self.handlers.send_email(args, env=self.env, smtp=factory),
                         {"status": "sent", "to": "a@example.com"})
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("me@test", "pw")
        sent = server.send_message.call_args.args[0]
        self.assertEqual((sent["To"], sent["Subject"]), ("a@example.com", "Invite"))

    def test_email_failures_say_what_went_wrong(self):
        factory, _ = self.smtp(smtplib.SMTPRecipientsRefused({"a@x": (550, b"no")}))
        with self.assertRaisesRegex(self.handlers.JobError, "address was rejected"):
            self.handlers.send_email({"to": "a@x", "subject": "s", "body": "b"}, env=self.env, smtp=factory)
        with self.assertRaisesRegex(self.handlers.JobError, "isn't set up"):
            self.handlers.send_email({"to": "a@x", "subject": "s", "body": "b"}, env={}, smtp=factory)

    def test_calls_are_placed_through_the_voice_service_with_their_origin(self):
        post = Mock(return_value=response(200, {"calls": [
            {"to": "+16175550123", "sid": "CA1"}, {"to": "123", "error": "Not a valid phone number."}]}))
        with patch.dict(os.environ, {"VOICE_API_TOKEN": "worker-key"}):
            result = self.handlers.place_calls({"numbers": ["+16175550123", "123"], "name": "Bob",
                                                "message": "Hiring?", "origin": CHAT},
                                               post=post, base_url="https://voice.test")
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer worker-key"})
        self.assertEqual(result["placed"], [{"to": "+16175550123", "sid": "CA1"}])
        self.assertEqual(result["failed"][0]["to"], "123")
        self.assertEqual(post.call_args.kwargs["json"]["origin"], CHAT)

    def test_no_calls_placed_is_a_failure_with_the_reasons(self):
        post = Mock(return_value=response(200, {"calls": [{"to": "123", "error": "Not a valid phone number."}]}))
        with self.assertRaisesRegex(self.handlers.JobError, "Not a valid phone number"):
            self.handlers.place_calls({"numbers": ["123"], "name": "Bob"}, post=post, base_url="x")


if __name__ == "__main__":
    unittest.main()
