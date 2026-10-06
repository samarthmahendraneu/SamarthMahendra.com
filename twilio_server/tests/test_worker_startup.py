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

WEB_APP_SECURITY = """
import json
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import app, chat_agent, mongo_tool
client = TestClient(app.app)
unused = [("post", "/talk_to_samarth_discord"), ("post", "/mongo_query"), ("get", "/api/dashboard_stats"),
          ("get", "/api/table"), ("get", "/api/daily_queue"), ("post", "/api/flashcard/submit"),
          ("get", "/api/problem/two-sum"), ("put", "/api/problem/two-sum")]
out = {"locked": sorted({client.request(method, path, json={}).status_code for method, path in unused})}
out["wrong_key"] = client.post("/mongo_query", headers={"Authorization": "Bearer guess"}).status_code
with patch.object(app, "ADMIN_TOKEN", "admin-key"), \\
        patch.object(mongo_tool, "query_mongo_db_for_candidate_profile", return_value={}):
    out["right_key"] = client.post("/mongo_query", headers={"Authorization": "Bearer admin-key"}).status_code
stub = Mock()
stub.allow_turn.return_value = False
with patch.object(chat_agent, "agent", return_value=stub):
    busy = client.post("/chat", json={"message": "hi", "session_id": "ab" * 16},
                       headers={"CF-Connecting-IP": "203.0.113.9"})
    out["rate_limited"] = [busy.status_code, "try again" in busy.json()["output"]]
    out["address"] = stub.allow_turn.call_args.args[0]
    out["too_long"] = client.post("/chat", json={"message": "x" * 5000, "session_id": "ab" * 16}).status_code
print(json.dumps(out))
"""


GITHUB_STATS = """
import json
from datetime import date, datetime
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import app

calendars = {
    "old-hand": [("2019-05-01", 7), ("2021-10-05", 2), ("2021-10-07", 3), ("2026-01-02", 4)],
    "newcomer": [("2025-10-06", 1), ("2025-10-07", 5), ("2026-10-06", 2), ("2026-12-31", 0)],
}

def fake_get(url, params=None, timeout=None):
    response = Mock()
    name = url.rstrip("/").split("/")[-1]
    if "contributions" in url:
        if name == "broken":
            response.raise_for_status.side_effect = RuntimeError("timed out")
        out["contribution_params"].add(json.dumps(params))
        response.json.return_value = {"contributions": [
            {"date": d, "count": n, "level": 1} for d, n in calendars.get(name, [])]}
    elif name == "repos":
        response.json.return_value = [{"language": "Python"}]
    else:
        response.json.return_value = {"public_repos": 2}
    return response

out = {"contribution_params": set()}
session = Mock(headers={})
session.get.side_effect = fake_get
with patch.object(app.requests, "Session", return_value=session):
    stats = app._build_github_stats(["old-hand", "newcomer"], today=date(2026, 10, 6))
    partial = app._build_github_stats(["newcomer", "broken"], today=date(2026, 10, 6))
out = {key: stats[key] for key in ("past_5_years_contributions", "last_year_contributions",
                                   "total_contributions", "repos", "complete")} | {
    "contribution_params": sorted(out["contribution_params"]),
    "partial": [partial["complete"], partial["past_5_years_contributions"]],
    "default_accounts": app.DEFAULT_GITHUB_USERNAMES}
client = TestClient(app.app)
for complete in (True, False):
    app._github_stats_cache.clear()
    with patch.object(app, "_build_github_stats", return_value={"complete": complete}), \
            patch.object(app, "DEFAULT_GITHUB_USERNAMES", ["a", "b"]):
        client.get("/github/stats?usernames=a%2Cb")
    left = app._github_stats_cache["a,b"]["expires_at"] - datetime.utcnow()
    out[f"cached_minutes_when_complete_{complete}"] = round(left.total_seconds() / 60)
print(json.dumps(out))
"""


PUBLIC_API = """
import json
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
import app

client = TestClient(app.app)
out = {}
calendar = {"query": "query userProfileCalendar($username: String!) { matchedUser(username: $username) { id } }",
            "variables": {"username": "samarthmahendra"}, "operationName": "userProfileCalendar"}
forwarded = Mock(return_value=Mock(json=Mock(return_value={"data": {}})))
with patch.object(app.requests, "post", forwarded):
    out["site_query"] = client.post("/leetcode/proxy", json=calendar).status_code
    out["other_user"] = client.post("/leetcode/proxy", json=dict(calendar, variables={"username": "someone"})).status_code
    out["other_operation"] = client.post("/leetcode/proxy", json=dict(calendar, operationName="globalData")).status_code
    out["smuggled_query"] = client.post("/leetcode/proxy", json=dict(calendar, query="query globalData { x }")).status_code
    out["not_json"] = client.post("/leetcode/proxy", content=b"nope").status_code
out["forwarded_once_with_timeout"] = [forwarded.call_count, forwarded.call_args.kwargs.get("timeout")]
with patch.object(app, "_build_github_stats", return_value={"complete": True}):
    app._github_stats_cache.clear()
    out["own_accounts"] = client.get("/github/stats?usernames=SamarthMahendra").status_code
    out["someone_else"] = client.get("/github/stats?usernames=torvalds").status_code
site = client.options("/chat", headers={"Origin": "https://samarthmahendra.com",
                                        "Access-Control-Request-Method": "POST"})
other = client.options("/chat", headers={"Origin": "https://evil.example",
                                         "Access-Control-Request-Method": "POST"})
out["cors"] = [site.headers.get("access-control-allow-origin"), other.headers.get("access-control-allow-origin")]
throttle = app.Throttle(2)
out["throttle"] = [throttle.allow("1.2.3.4") for _ in range(3)] + [throttle.allow("5.6.7.8")]
print(json.dumps(out))
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

    def test_the_web_app_locks_what_the_site_doesnt_use_and_paces_the_chat(self):
        proc = self.run_in_worker_folder(WEB_APP_SECURITY)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(result, {"locked": [403], "wrong_key": 403, "right_key": 200,
                                  "rate_limited": [429, True], "address": "203.0.113.9",
                                  "too_long": 413})

    def test_github_contributions_are_the_last_five_years_across_every_account(self):
        result = json.loads(self.run_in_worker_folder(GITHUB_STATS).stdout.strip().splitlines()[-1])
        self.assertEqual(result, {
            # Days after 6 Oct 2021, up to today: old-hand's 3 + 4, newcomer's 1 + 5 + 2.
            "past_5_years_contributions": 15,
            # Days after 6 Oct 2025: old-hand's 4, newcomer's 5 + 2.
            "last_year_contributions": 11,
            "total_contributions": 24,
            "repos": 4,
            "complete": True,
            # One request an account, for every year at once.
            "contribution_params": ['{"y": "all"}'],
            # An account that couldn't be counted marks the total incomplete...
            "partial": [False, 8],
            "default_accounts": ["SamarthMahendraneu", "SamarthMahendra-Draup", "SamarthMahendra"],
            # ...and an incomplete total is fetched again in minutes, not a day.
            "cached_minutes_when_complete_True": 1440,
            "cached_minutes_when_complete_False": 10,
        })

    def test_the_public_proxies_only_serve_the_site(self):
        result = json.loads(self.run_in_worker_folder(PUBLIC_API).stdout.strip().splitlines()[-1])
        self.assertEqual(result, {
            "site_query": 200, "other_user": 403, "other_operation": 403, "smuggled_query": 403,
            "not_json": 400, "forwarded_once_with_timeout": [1, 10],
            "own_accounts": 200, "someone_else": 403,
            "cors": ["https://samarthmahendra.com", None],
            "throttle": [True, True, False, True],
        })

    def test_the_worker_runs_without_the_features_for_several_workers(self):
        script = (REPO / "pythonserver" / "start_workers.sh").read_text()
        self.assertRegex(script, r"celery -A celery_worker worker [^&]*"
                                 r"--without-gossip --without-mingle --without-heartbeat &")


if __name__ == "__main__":
    unittest.main()
