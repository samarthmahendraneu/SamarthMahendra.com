"""Background jobs: work a conversation starts now and hears about later.

A tool that would otherwise keep someone waiting -- emailing an invite,
placing calls, posting to Discord -- starts a job and returns its id at once.
The worker runs it (pythonserver/job_handlers.py), keeps the outcome here,
and appends job.done or job.failed to the conversation's event stream. That
is how the assistant volunteers "the invite just went out" without being
asked, and how a failure reaches someone instead of vanishing in a log.

Kept byte-identical in twilio_server/ and pythonserver/; the tests fail if the
copies drift.
"""

import json
import re
import time
import uuid

JOB_KEY = "job:"
CLAIM_KEY = "job-claim:"
TTL = 86400
# Longer than any job runs. A worker killed mid-job frees its claim after this,
# so a redelivered task can run it again.
CLAIM_TTL = 600
ID_PATTERN = re.compile(r"[0-9a-f]{32}")
# Who hears about the outcome: the conversation always, only when it failed,
# or never (for bookkeeping the person doesn't need to hear about).
ANNOUNCE = ("always", "failure", "never")
MAX_ERROR_CHARS = 300


class JobStore:
    def __init__(self, redis, enqueue=None):
        self.redis = redis
        # Hands a job id to the worker; injected so each service uses its own
        # Celery client and tests need none.
        self.enqueue = enqueue

    def key(self, job_id):
        if not ID_PATTERN.fullmatch(job_id or ""):
            raise ValueError("Invalid job id")
        return JOB_KEY + job_id

    def start(self, kind, args, origin=None, announce="failure", label=""):
        """Record a job and hand it to the worker. Returns the job id.

        If the hand-off fails the job is marked failed and the error re-raised,
        so the tool can say it didn't happen rather than promise it will.
        """
        if announce not in ANNOUNCE:
            raise ValueError("Unknown announce policy")
        job_id = uuid.uuid4().hex
        self.save({
            "id": job_id, "kind": kind, "args": args, "origin": origin,
            "announce": announce, "label": label, "status": "queued",
            "result": None, "error": None, "created_at": time.time(),
            "started_at": None, "finished_at": None,
        })
        try:
            self.enqueue(job_id)
        except Exception as exc:
            self.fail(job_id, f"could not be queued ({type(exc).__name__})")
            raise
        return job_id

    def get(self, job_id):
        raw = self.redis.get(self.key(job_id))
        return json.loads(raw) if raw else None

    def save(self, record):
        self.redis.setex(self.key(record["id"]), TTL, json.dumps(record))

    def claim(self, job_id):
        """Worker side: take a job, or None if it is finished or already taken.

        Celery can deliver a task twice; the SET NX claim stops a second copy
        sending the same email or placing the same calls again.
        """
        record = self.get(job_id)
        if record is None or record["status"] in ("done", "failed"):
            return None
        if not self.redis.set(CLAIM_KEY + job_id, "1", nx=True, ex=CLAIM_TTL):
            return None
        record["status"] = "running"
        record["started_at"] = time.time()
        self.save(record)
        return record

    def finish(self, job_id, result):
        return self._close(job_id, status="done", result=result)

    def fail(self, job_id, error):
        error = " ".join(str(error).split())[:MAX_ERROR_CHARS]
        return self._close(job_id, status="failed", error=error)

    def _close(self, job_id, **changes):
        record = self.get(job_id)
        if record is None:
            return None
        record.update(changes, finished_at=time.time())
        self.save(record)
        return record

    @staticmethod
    def should_announce(record):
        policy = record.get("announce")
        return bool(record.get("origin")) and (
            policy == "always" or (policy == "failure" and record["status"] == "failed"))

    @staticmethod
    def summary(record):
        """What a conversation is told about a job: no arguments, just the outcome."""
        return {"id": record["id"], "kind": record["kind"], "label": record.get("label") or "",
                "status": record["status"], "result": record.get("result"),
                "error": record.get("error")}

    def describe(self, record):
        """For check_task: the job's state in words a model can pass on."""
        waited = round(time.time() - record["created_at"])
        described = {"status": record["status"], "task": record.get("label") or record["kind"],
                     "waited_seconds": waited}
        if record["status"] == "done":
            described["result"] = record.get("result")
        elif record["status"] == "failed":
            described["error"] = record.get("error")
        else:
            described["message"] = "Still running; the conversation is told when it finishes."
        return described
