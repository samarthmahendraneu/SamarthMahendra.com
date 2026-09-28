"""The Redis client a process shares, with a ceiling on its connections.

Every service draws on one connection limit, 30 on Redis Cloud's free plan:
both web services, the worker, the Discord listener, and during a deploy the
old copy of whichever service is being replaced. redis-py opens a connection
for each thread that needs one at that moment and keeps it for good, so one
busy minute left a process holding connections it no longer needed, and the
worker couldn't start. With a ceiling, a thread beyond it waits for a free
connection instead. Nothing here waits on the server (no BLPOP, no XREAD
BLOCK), so that wait is short; keep it that way.

Kept byte-identical in twilio_server/ and pythonserver/; the tests fail if the
copies drift.
"""

import os

import redis

# How long a thread waits for a free connection before giving up.
WAIT = 10


def connect(max_connections, url=None):
    """A client that holds at most `max_connections` connections."""
    return redis.Redis(connection_pool=redis.BlockingConnectionPool.from_url(
        url or os.getenv("REDIS_URL") or "redis://localhost:6379/0",
        max_connections=max_connections, timeout=WAIT,
        socket_connect_timeout=5, socket_timeout=10))
