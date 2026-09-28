import os
from fastapi import FastAPI, Request
import re

from fastapi.responses import JSONResponse, StreamingResponse
from celery import Celery
from dotenv import load_dotenv

from celery_worker import celery_app  # noqa: F401 -- also configures logging
import chat_agent

STREAM_ID = re.compile(r"\d{1,20}-\d{1,20}")



load_dotenv()
app = FastAPI()
celery = Celery(__name__, broker=os.getenv("REDIS_URL"))  # Reads REDIS_URL from env

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or restrict to your frontend domain(s)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)




import discord_tool
import asyncio
import mongo_tool
import json
import uuid
from threading import Lock
from datetime import datetime, timedelta
from fastapi import Body





def talk_to_manager_discord(message, wait_user_id=None, timeout=60):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        reply = loop.run_until_complete(discord_tool.ask_and_get_reply(message, wait_user_id=wait_user_id, timeout=timeout))
        if reply:
            return reply
        else:
            return "No reply received from Discord manager in time."
    except Exception as e:
        return f"Failed to send message to Discord or receive reply: {e}"
    finally:
        loop.close()

@app.post("/talk_to_samarth_discord")
async def talk_to_samarth_discord_api(request: Request):
    data = await request.json()
    message = data.get("message")
    result = talk_to_manager_discord(message)
    return {"result": result}

@app.post("/mongo_query")
async def mongo_query_api():
    result = mongo_tool.query_mongo_db_for_candidate_profile()
    return {"result": result}



@app.get("/health")
async def health():
    return {"status": "OK"}

# --- Practice Dashboard API ---

from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()   # ← fix here

PRACTICE_MONGO_URI = os.getenv("MONGO_URI", "")
PRACTICE_DB_NAME = os.getenv("MONGO_PRACTICE_DB_NAME", "practice_db")

try:
    practice_client = MongoClient(PRACTICE_MONGO_URI)
    practice_db = practice_client[PRACTICE_DB_NAME]
except Exception as e:
    print(f"Failed to connect to practice DB: {e}")
    practice_db = None

@app.get("/api/dashboard_stats")
async def get_dashboard_stats():
    """Return calendar check-ins, streak, and tag card statistics."""
    if practice_db is None:
        return JSONResponse(status_code=500, content={"error": "Database connection failed"})
        
    metadata = practice_db.metadata.find_one({"_id": "global_metadata"}) or {}
    
    # Optional pipeline to dynamically calculate tag counts if we don't want to use aggregated counts
    # But for now, we can just aggregate from the problems collection directly
    pipeline = [
        {"$project": {
            "tags": {"$setUnion": [{"$ifNull": ["$customTags", []]}, {"$ifNull": ["$topics", []]}]},
            "attempted": {"$ifNull": ["$attempted", 0]},
            "solved": {"$ifNull": ["$solved", 0]}
        }},
        {"$unwind": {"path": "$tags", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": {"$ifNull": ["$tags", "Untagged"]},
            "total": {"$sum": 1},
            "attempted": {"$sum": "$attempted"},
            "solved": {"$sum": "$solved"}
        }},
        {"$sort": {"total": -1}},
        {"$limit": 30} # top 30 tags
    ]
    
    tag_stats = list(practice_db.problems.aggregate(pipeline))
    formatted_tags = []
    for t in tag_stats:
        formatted_tags.append({
            "name": t["_id"],
            "total": t["total"],
            "attempted": t["attempted"],
            "solved": t["solved"]
        })

    # Return a structure matching the frontend needs
    return {
        "streak": metadata.get("streak", 0),
        "checkIns": metadata.get("checkIns", {}),
        "tagStats": formatted_tags
    }

from typing import Optional
from fastapi import Query
import math
import random

@app.get("/api/table")
async def get_practice_table(
    page: int = 1, 
    limit: int = 50, 
    sortCol: str = "frequency", 
    sortAsc: bool = False,
    search: str = "",
    level: str = "",
    topic: str = ""
):
    """Return paginated, sorted, and filtered table rows."""
    if practice_db is None:
        return JSONResponse(status_code=500, content={"error": "Database connection failed"})
        
    query = {}
    
    if level:
        query["difficulty"] = level
        
    if topic:
        query["$or"] = [
            {"customTags": topic},
            {"topics": topic}
        ]
        
    if search:
        search_lower = search.lower()
        # MongoDB text search or regex. Simple regex for now matching old frontend logic
        query["$or"] = [
            {"title": {"$regex": search_lower, "$options": "i"}},
            {"customTags": {"$regex": search_lower, "$options": "i"}},
            {"topics": {"$regex": search_lower, "$options": "i"}},
            {"_id": {"$regex": search_lower, "$options": "i"}},
            {"difficulty": {"$regex": search_lower, "$options": "i"}}
        ]

    # Map frontend sort column to DB field
    sort_field = sortCol
    # Confidence is computed, we can't easily natively sort by it, so if requested we have to fetch all and sort
    if sortCol == "confidence":
        cursor = practice_db.problems.find(query)
        items = list(cursor)
        def sort_conf(a, b):
            valA = -1 if a.get("attempted", 0) == 0 else a.get("solved", 0) / a.get("attempted", 1)
            valB = -1 if b.get("attempted", 0) == 0 else b.get("solved", 0) / b.get("attempted", 1)
            if valA < valB: return -1
            if valA > valB: return 1
            return 0
        import functools
        items.sort(key=functools.cmp_to_key(sort_conf), reverse=not sortAsc)
        total_items = len(items)
        paginated_items = items[(page-1)*limit : page*limit]
    else:
        direction = 1 if sortAsc else -1
        if sort_field == "id":
             sort_field = "_id" # might fail if IDs are strings and not properly padded, but fits legacy logic
             
        cursor = practice_db.problems.find(query).sort(sort_field, direction)
        total_items = practice_db.problems.count_documents(query)
        paginated_items = list(cursor.skip((page - 1) * limit).limit(limit))

    return {
        "items": paginated_items,
        "total": total_items,
        "page": page,
        "totalPages": math.ceil(total_items / limit) if limit else 1
    }

import time
@app.get("/api/daily_queue")
async def get_daily_queue():
    """Compute and return the daily queue of flashcards."""
    if practice_db is None:
         return []
         
    # Fetch all, score in python because logic includes math.random + date math
    # Optional: could push closer to db, but since collection is small (<2000), python is fine
    problems = list(practice_db.problems.find())
    
    solved_pool = [p for p in problems if p.get("solved", 0) > 0]
    new_pool = [p for p in problems if p.get("solved", 0) == 0]
    
    def score_problem(p):
        score = p.get("frequency", 0) * 2
        ratio = 0
        if p.get("attempted", 0) > 0:
            ratio = p.get("solved", 0) / p.get("attempted", 1)
        score += (1 - ratio) * 100
        
        now = time.time() * 1000
        next_review = p.get("nextReview") or 0
        if now >= next_review:
             score += 50
        return score + (random.random() * 10)
        
    solved_pool.sort(key=score_problem, reverse=True)
    new_pool.sort(key=score_problem, reverse=True)
    
    top_solved = solved_pool[:10]
    random.shuffle(top_solved)
    
    top_new = new_pool[:10]
    random.shuffle(top_new)
    
    selected_solved = top_solved[:3]
    selected_new = top_new[:5 - len(selected_solved)]
    
    unselected_solved = [p for p in top_solved if p not in selected_solved]
    while (len(selected_solved) + len(selected_new)) < 5 and unselected_solved:
        selected_solved.append(unselected_solved.pop(0))
        
    daily_queue = selected_solved + selected_new
    random.shuffle(daily_queue)
    
    return daily_queue

from pydantic import BaseModel
from typing import List, Dict, Any

class AttemptPayload(BaseModel):
    problem_id: str
    solved: bool
    code: str
    notes: str

@app.post("/api/flashcard/submit")
async def submit_flashcard_attempt(payload: AttemptPayload):
    """Handle attempt submission, update item stats and global streak."""
    if practice_db is None:
        return JSONResponse(status_code=500, content={"error": "Database connection failed"})
        
    p = practice_db.problems.find_one({"_id": payload.problem_id})
    if not p:
         return JSONResponse(status_code=404, content={"error": "Problem not found"})
         
    updates = {
        "$inc": {"attempted": 1},
        "$set": {"code": payload.code, "notes": payload.notes}
    }
    
    ONE_DAY = 24 * 60 * 60 * 1000
    now_ms = time.time() * 1000
    
    attempted = p.get("attempted", 0) + 1
    solved_count = p.get("solved", 0)
    
    if payload.solved:
        solved_count += 1
        updates["$inc"]["solved"] = 1
        updates["$set"]["lastSolved"] = datetime.utcnow().isoformat() + "Z"
        
        ratio = solved_count / attempted
        next_review = now_ms + (ONE_DAY * 7) if ratio > 0.8 else now_ms + (ONE_DAY * 3)
    else:
        next_review = now_ms + ONE_DAY
        
    updates["$set"]["nextReview"] = next_review
    
    practice_db.problems.update_one({"_id": payload.problem_id}, updates)
    
    # Update global check-ins
    today = datetime.utcnow().strftime("%Y-%m-%d")
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    metadata = practice_db.metadata.find_one({"_id": "global_metadata"}) or {}
    check_ins = metadata.get("checkIns", {})
    
    current_today = check_ins.get(today, 0)
    check_ins[today] = current_today + 1
    
    streak = metadata.get("streak", 0)
    if current_today == 0:
        if check_ins.get(yesterday):
            streak += 1
        else:
            streak = 1
            
    practice_db.metadata.update_one(
        {"_id": "global_metadata"}, 
        {"$set": {"checkIns": check_ins, "streak": streak}},
        upsert=True
    )
    
    return {"success": True}

class EditPayload(BaseModel):
    title: str
    url: str
    difficulty: str
    frequency: float
    notes: str
    customTags: List[str]
    topics: Optional[List[str]] = None
    techniques: Optional[List[str]] = None

@app.put("/api/problem/{id}")
async def update_problem(id: str, payload: EditPayload):
    if practice_db is None:
        return JSONResponse(status_code=500, content={"error": "Database connection failed"})
        
    updates = {
        "title": payload.title,
        "url": payload.url,
        "difficulty": payload.difficulty,
        "frequency": payload.frequency,
        "notes": payload.notes,
        "customTags": payload.customTags
    }
    
    if payload.topics is not None:
         updates["topics"] = payload.topics
    if payload.techniques is not None:
         updates["techniques"] = payload.techniques
         
    res = practice_db.problems.update_one(
         {"_id": id}, 
         {"$set": updates},
         upsert=True # Allow creating new problem from insights modal
    )
    
    return {"success": True}

@app.get("/api/problem/{id}")
async def get_problem(id: str):
    if practice_db is None:
         return JSONResponse(status_code=500, content={"error": "Database connection failed"})
    p = practice_db.problems.find_one({"_id": id})
    if p:
         return p
    return {"error": "Not Found"}

# ----------------------------

@app.get("/")
async def root():
    return {"status": "ok", "message": "FastAPI server is running"}



@app.post("/chat")
async def chat(request: Request):
    """A visitor's message. The conversation lives server-side (chat_agent.py);
    the browser sends its session id and the last event id it has seen."""
    data = await request.json()
    message = (data.get("message") or "").strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    session_id = chat_agent.session_id_for(data.get("session_id"), data.get("username"))
    cursor = data.get("cursor") or chat_agent.START
    if not isinstance(cursor, str) or not STREAM_ID.fullmatch(cursor):
        cursor = chat_agent.START
    timezone = data.get("timezone") if isinstance(data.get("timezone"), str) else None
    try:
        result = await asyncio.to_thread(chat_agent.agent().respond, session_id, message, cursor,
                                         timezone)
    except chat_agent.Busy:
        return JSONResponse({"error": "Still working on the last message", "session_id": session_id},
                            status_code=409)
    return JSONResponse(result)


@app.get("/chat/events")
async def chat_events(request: Request, session_id: str = "", after: str = "0-0"):
    """Server-sent events: messages about background work, as they're written.

    The browser opens this while something is pending. EventSource reconnects
    on its own and sends Last-Event-ID, so nothing is shown twice or missed.
    """
    if not chat_agent.SESSION_PATTERN.fullmatch(session_id):
        return JSONResponse({"error": "Unknown session"}, status_code=404)
    last = request.headers.get("last-event-id") or after
    if not STREAM_ID.fullmatch(last):
        last = chat_agent.START
    agent = chat_agent.agent()
    return StreamingResponse(agent.stream(session_id, last, request.is_disconnected),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/chat/presence")
async def chat_presence(session_id: str = "", here: int = 1):
    """The chat page is open and in view (here=1), or has gone (here=0).

    Query parameters and no body, so the browser can send it with
    sendBeacon as the page closes, and without a CORS preflight.
    """
    if not chat_agent.SESSION_PATTERN.fullmatch(session_id):
        return JSONResponse({"error": "Unknown session"}, status_code=404)
    await asyncio.to_thread(chat_agent.agent().set_presence, session_id, bool(here))
    return JSONResponse({"ok": True})


import requests
from fastapi import HTTPException

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql/"
GITHUB_API_URL = "https://api.github.com"
GITHUB_CONTRIBUTIONS_URL = "https://github-contributions-api.jogruber.de/v4"
GITHUB_STATS_CACHE_TTL = timedelta(days=1)
DEFAULT_GITHUB_USERNAMES = ["SamarthMahendraneu", "SamarthMahendra-Draup"]
PREFERRED_GITHUB_LANGUAGES = ["Python", "Java", "C++", "JavaScript", "TypeScript"]
_github_stats_cache = {}
_github_stats_cache_lock = Lock()


def _resolve_github_usernames(query_usernames=None):
    if query_usernames:
        usernames = [username.strip() for username in query_usernames.split(",") if username.strip()]
        if usernames:
            return usernames

    env_usernames = os.getenv("GITHUB_STATS_USERNAMES", "")
    if env_usernames:
        usernames = [username.strip() for username in env_usernames.split(",") if username.strip()]
        if usernames:
            return usernames

    return DEFAULT_GITHUB_USERNAMES


def _github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "samarthmahendra-portfolio",
    }
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def _build_github_stats(usernames):
    session = requests.Session()
    session.headers.update(_github_headers())

    total_repos = 0
    total_contributions_all_time = 0
    last_year_contributions = 0
    past_5_years_contributions = 0
    all_languages = {}
    warnings = []

    current_year = datetime.utcnow().year
    contribution_start_year = 2018
    past_5_years_start = current_year - 5

    for username in usernames:
        try:
            user_response = session.get(f"{GITHUB_API_URL}/users/{username}", timeout=15)
            user_response.raise_for_status()
            user_data = user_response.json()
            total_repos += user_data.get("public_repos", 0) or 0
        except Exception as exc:
            warnings.append(f"Could not fetch user profile for {username}: {exc}")
            continue

        try:
            repos_response = session.get(
                f"{GITHUB_API_URL}/users/{username}/repos",
                params={"per_page": 100, "sort": "updated"},
                timeout=20
            )
            repos_response.raise_for_status()
            repos = repos_response.json()

            for repo in repos:
                language = repo.get("language")
                if language:
                    all_languages[language] = all_languages.get(language, 0) + 1
        except Exception as exc:
            warnings.append(f"Could not fetch repositories for {username}: {exc}")

        for year in range(contribution_start_year, current_year + 1):
            try:
                contributions_response = session.get(
                    f"{GITHUB_CONTRIBUTIONS_URL}/{username}",
                    params={"y": year},
                    timeout=15
                )
                contributions_response.raise_for_status()
                contributions_data = contributions_response.json()
                year_contributions = contributions_data.get("total", {}).get(str(year), 0) or 0
                total_contributions_all_time += year_contributions

                if year == current_year:
                    last_year_contributions += year_contributions

                if year >= past_5_years_start:
                    past_5_years_contributions += year_contributions
            except Exception as exc:
                warnings.append(f"Could not fetch contributions for {username} in {year}: {exc}")

    ranked_languages = sorted(all_languages.items(), key=lambda item: (-item[1], item[0]))
    top_languages = [language for language, _ in ranked_languages[:3]]

    preferred_languages = [
        language for language in PREFERRED_GITHUB_LANGUAGES if language in all_languages
    ]
    if preferred_languages:
        top_languages = preferred_languages[:3]

    generated_at = datetime.utcnow()
    return {
        "usernames": usernames,
        "repos": total_repos,
        "total_contributions": total_contributions_all_time,
        "last_year_contributions": last_year_contributions,
        "past_5_years_contributions": past_5_years_contributions,
        "top_languages": top_languages,
        "generated_at": generated_at.isoformat() + "Z",
        "expires_at": (generated_at + GITHUB_STATS_CACHE_TTL).isoformat() + "Z",
        "warnings": warnings[:10],
    }


@app.post("/leetcode/proxy")
async def leetcode_proxy(request: Request):
    """
    Proxy any GraphQL request to the LeetCode GraphQL API.
    Bypasses CORS restrictions by doing the request server-side.
    """
    try:
        payload = await request.json()

        resp = requests.post(
            LEETCODE_GRAPHQL_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
            }
        )

        # Forward response back to browser
        return JSONResponse(resp.json())

    except Exception as e:
        print("❌ LeetCode Proxy Error:", e)
        raise HTTPException(status_code=500, detail="Error contacting LeetCode API")


@app.get("/github/stats")
async def github_stats_proxy(usernames: str = None):
    resolved_usernames = _resolve_github_usernames(usernames)
    cache_key = ",".join(resolved_usernames)
    now = datetime.utcnow()

    with _github_stats_cache_lock:
        cached_entry = _github_stats_cache.get(cache_key)
        if cached_entry and cached_entry["expires_at"] > now:
            payload = dict(cached_entry["payload"])
            payload["cached"] = True
            return JSONResponse(payload)

    try:
        payload = _build_github_stats(resolved_usernames)
    except Exception as exc:
        print("❌ GitHub Stats Proxy Error:", exc)
        raise HTTPException(status_code=500, detail="Error contacting GitHub APIs")

    expires_at = now + GITHUB_STATS_CACHE_TTL
    with _github_stats_cache_lock:
        _github_stats_cache[cache_key] = {
            "payload": payload,
            "expires_at": expires_at
        }

    response_payload = dict(payload)
    response_payload["cached"] = False
    return JSONResponse(response_payload)
