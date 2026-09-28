"""Queues work for the one Celery worker, deployed from pythonserver/.

The voice service runs no worker of its own. Tasks are sent by name through
the shared Redis broker, so this needs none of the worker's code, and none of
its email or Discord credentials.
"""

import os

from celery import Celery

# Only the broker matters here; the task names belong to the worker.
celery_app = Celery("celery_worker", broker=os.getenv("REDIS_URL"))


def enqueue_job(job_id):
    """Hand a background job (jobs.py) to the worker's run_job."""
    celery_app.send_task("celery_worker.run_job", args=[job_id])


def enqueue_chat_followup(session_id):
    """Have the worker tell a website chat about news on its event stream."""
    celery_app.send_task("celery_worker.chat_followup", args=[session_id])
