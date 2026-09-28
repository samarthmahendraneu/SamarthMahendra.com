import importlib.util
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


@unittest.skipUnless(importlib.util.find_spec("celery") and importlib.util.find_spec("pymongo"),
                     "needs pythonserver's requirements")
class WorkerStartupTests(unittest.TestCase):
    def test_tasks_can_import_the_folders_modules_after_celery_loads_the_app(self):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
                   REDIS_URL="redis://default:not-a-real-password@localhost:6399/0",
                   MONGO_URI="mongodb://localhost:27017")
        proc = subprocess.run([sys.executable, "-c", LOAD_LIKE_CELERY], cwd=REPO / "pythonserver",
                              env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        self.assertIn("ok", proc.stdout)
        # The broker URL carries the Redis password; it must never reach the log.
        self.assertNotIn("not-a-real-password", proc.stdout + proc.stderr)
        self.assertIn("localhost:6399", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
