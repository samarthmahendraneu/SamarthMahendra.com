import importlib.util
import json
import os
import subprocess
import sys
import unittest

from worker_modules import REPO

# Loads the worker the way `celery -A celery_worker worker` does: Celery puts
# the working directory on sys.path only while it imports the app module,
# then takes it off again. A task that imports one of the folder's modules
# after that fails in production however the tests load it.
LOAD_LIKE_CELERY = """
import os, sys
sys.path[:] = [p for p in sys.path if p not in ("", ".", os.getcwd())]
from celery.utils.imports import import_from_cwd
import_from_cwd("celery_worker")
import chat_agent, job_handlers, timezones
print("ok")
"""

CONNECTION_SETTINGS = """
import json
import celery_worker, chat_agent
conf = celery_worker.celery_app.conf
pool = celery_worker.redis_client.connection_pool
print(json.dumps({
    "result_backend": conf.result_backend, "ignore_result": conf.task_ignore_result,
    "sending_connections": conf.broker_pool_limit,
    "remote_control": conf.worker_enable_remote_control,
    "pool": type(pool).__name__, "max_connections": pool.max_connections,
    "chat_shares_the_client": chat_agent.agent().redis is celery_worker.redis_client,
}))
"""


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("celery", "pymongo", "bcrypt", "openai")),
                     "needs pythonserver's requirements")
class WorkerStartupTests(unittest.TestCase):
    def run_in_worker_folder(self, script):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   REDIS_URL="redis://default:not-a-real-password@localhost:6399/0",
                   MONGO_URI="mongodb://localhost:27017", OPENAI_API_KEY="test-key")
        proc = subprocess.run([sys.executable, "-c", script], cwd=REPO / "pythonserver",
                              env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        return proc

    def test_tasks_can_import_the_folders_modules_after_celery_loads_the_app(self):
        proc = self.run_in_worker_folder(LOAD_LIKE_CELERY)
        self.assertIn("ok", proc.stdout)
        # The broker URL carries the Redis password; it must never reach the log.
        self.assertNotIn("not-a-real-password", proc.stdout + proc.stderr)
        self.assertIn("localhost:6399", proc.stdout + proc.stderr)

    def test_the_worker_and_chat_share_one_capped_redis_client(self):
        # Every service shares the Redis plan's 30 connections (redis_pool.py).
        proc = self.run_in_worker_folder(CONNECTION_SETTINGS)
        settings = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(settings, {
            "result_backend": None, "ignore_result": True, "sending_connections": 1,
            "remote_control": False, "pool": "BlockingConnectionPool", "max_connections": 4,
            "chat_shares_the_client": True})

    def test_the_worker_runs_without_the_features_for_several_workers(self):
        script = (REPO / "pythonserver" / "start_workers.sh").read_text()
        self.assertRegex(script, r"celery -A celery_worker worker [^&]*"
                                 r"--without-gossip --without-mingle --without-heartbeat &")


if __name__ == "__main__":
    unittest.main()
