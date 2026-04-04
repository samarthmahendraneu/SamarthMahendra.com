import os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from celery import Celery
from dotenv import load_dotenv
from openai import OpenAI, api_key

from celery_worker import celery_app, tool_call_fn



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





# get api key from environment variable
api_key = os.getenv("OPENAI_API_KEY", '')


# models : gpt-4.1, gpt-4.1-mini, gpt-4.1-nano
model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-5.4-nano")

client = OpenAI(api_key=api_key)


def generate_jitsi_meeting_url(user_name=None):
    from mongo_tool import insert_meeting
    base_url = "https://meet.jit.si/"

    # connvert into a html link
    if user_name:
        meeting_name = f"{user_name}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
    else:
        meeting_name = f"SamarthMeeting-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"

    return base_url + meeting_name

# Tool schema for ChatGPT function calling
schedule_meeting_tool_schema = {
    "type": "function",
    "name": "schedule_meeting_on_jitsi",
    "description": "Function to Schedule a meeting with Samarth and others on Jitsi, store meeting in MongoDB, and send an email invite with the Jitsi link. dont ask too much just schedule the meeting and don't need to ask for Samarth's availability, ask for alL input before scheduling the meeting.",
    "parameters": {
        "type": "object",
        "properties": {
            "members": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of member emails (apart from Samarth)"
            },
            "agenda": {"type": "string", "description": "Agenda for the meeting"},
            "timing": {"type": "string", "description": "Meeting time/date in ISO format"},
            "user_email": {"type": "string", "description": "Email of the user scheduling the meeting (for invite)"}
        },
        "required": ["members", "agenda", "timing", "user_email"]
    }
}








def schedule_meeting(args):
    # args: dict with keys members, agenda, timing, user_email
    members = args.get("members", [])
    agenda = args.get("agenda")
    timing = args.get("timing")
    user_email = args.get("user_email")
    # Always include Samarth
    if "samarth@samarthmahendra.com" not in members:
        members.append("samarth@samarthmahendra.com")
    print(members, agenda, timing, user_email)
    meeting_url = generate_jitsi_meeting_url("samarth")
    meeting_url_full = '<a href="{}">{}</a>'.format(meeting_url, meeting_url)
    meeting_id = mongo_tool.insert_meeting(members, agenda, timing, meeting_url)

    print(" Sending email : ", user_email, meeting_url)
    tool_call_fn.delay("send_meeting_email", None, {"email": user_email, "meeting_url": meeting_url})

    # ping samarth on discord about the meeting
    # celery_app.send_task("tool_call_fn", args=("talk_to_samarth_discord", None, {"action": "send", "message": {"content": f"Meeting scheduled with {', '.join(members)} on {timing} for {agenda}. Meeting link: {meeting_url}"}}))
    tool_call_fn.delay("talk_to_samarth_discord", None, {"action": "send", "message": {"content": f"Meeting scheduled with {', '.join(members)} on {timing} for {agenda}. Meeting link: {meeting_url}"}})
    return {"meeting_url": meeting_url_full, "meeting_id": meeting_id}

mongo_query_tool_schema = {
"type": "function",
  "name": "query_profile_info",
  "description": "Function to query profile information, requiring no input parameters for Job fit or any resume information.",
  "strict": True,
  "parameters": {
    "type": "object",
    "properties": {},
    "additionalProperties": False
  }
}
discord_tool_schema = {
    "type": "function",
    "name": "talk_to_samarth_discord",
    "description": "Send a message to samarth via Discord bot integration only once, and wait for a reply",
    "parameters": {
        "type": "object",
        "required": ["action", "message"],
        "properties": {
            "action": {
                "type": "string",
                "description": "The action to perform, either 'send' or 'receive'"
            },
            "message": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The content of the message"},
                },
                "required": ["content"],
                "additionalProperties": False
            }
        },
        "additionalProperties": False
    },
    "strict": True
}


#
# phone_numbers = {
#     "type": "function",
#     "name": "query_phone_numbers",
#     "description": "Function to query phone numbers to make calls, mom and dad is saved as mom and dad in the database",
#     "parameters": {
#         "type": "object",
#         "properties": {
#             "name": {
#                 "type": "string",
#                 "description": "Name of the person to call"
#             }
#         },
#         "required": ["name"],
#         "additionalProperties": False
#     }
# }


make_calls_tool_schema = {
    "type": "function",
    "name": "make_calls",
    "description": "Make calls to the given numbers on behalf of the user, take numbers and password before making call",
    "parameters": {
        "type": "object",
        "properties": {
            "numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of phone numbers to call"
            },
            "name": {
                "type": "string",
                "description": "Name of the person to call"
            },
            "message": {
                "type": "string",
                "description": " Purpose of the call with message"
            },
            "password": {
                "type": "string",
                "description": "Password to authenticate the user"
            }
        },
        "required": ["numbers", "name", "password"],
        "additionalProperties": False
    }
}



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





    data = await request.json()
    message = data.get("message")

    conversation = data.get("conversation", [])
    if not conversation:
        print("New conversation started")
        conversation = [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": """You are Samarth Mahendra’s AI personal assistant who usually talks to recruiters or anyone who is interested in samarth's profile or would want to hire him.\n\nYour capabilities include:\n- Communicating with Samarth via Discord to ask questions (only if necessary, try to answer withtout discord)or relay information.\n- Querying a MongoDB database to retrieve or verify candidate profiles and job fit.\n- Scheduling meetings only using Jitsi and sending out meeting invitations.\n- You can query the database for any information about Samarth.\n\nGuidelines:\n- Before pinging Samarth on Discord, always gather all relevant information from the user or available sources.\n- When evaluating if someone is a good match for a job, always gather the job information first, then check the candidate profile using the MongoDB tool.\n- When checking Samarth’s availability for meetings, never query the database; always confirm with Samarth directly on Discord.\n- Always act professionally and on behalf of Samarth.\n- Don't ping again to discord if any reply is pending, use one function tool at a time, don't answer queries outside his profile ex solve this coding question, about politcs, news, queries outside info about samarth 
                **Guidelines:**
- Do **not** provide direct coding solutions, programming advice, or answers to technical questions unrelated to the profile or scheduling.
- Focus solely on professional interactions, scheduling, and profile-related inquiries.
- If asked for code or answers outside your scope, politely inform the requester that such assistance is outside your responsibilities.
- Always maintain professionalism and adhere to the scope of your role."""}]
            },
            {
                "role": "user",
                "content": [{"type": "input_text", "text": message}]
            }
        ]
    else:
        if message:
            print("Continuing conversation")
            conversation.append({
                "role": "user",
                "content": [{"type": "input_text", "text": message}]
            })
    pending_calls = data.get("pending_calls", [])

    print(conversation)
    print("Payload", data)
    if pending_calls:
        # Check status of each pending call
        updated_pending_calls = []
        tool_outputs = []
        for call in pending_calls:
            # Each call may have: {"tool_calls": [...], "message_id": ...}
            message_id = call.get("message_id")
            if not message_id:
                continue
            tools_calls = call.get("tool_calls")
            print("tools_calls", tools_calls)
            status, message = mongo_tool.get_tool_message_status(message_id)
            if status== "completed":
                print("Tool call completed", status)
                # Tool call completed, add output to conversation
                output_str = json.dumps(message, ensure_ascii=False)
            else:
                # Still pending or error
                updated_pending_calls.append(call)
            # Add outputs to conversation
            if status == "completed":
                conversation += [tc for tc in (tools_calls or [])]
                conversation.append(
                    {
                        "type": "function_call_output",
                        "call_id": message_id,
                        "output": output_str
                    }
                )
        # Replace pending_calls with updated list
        pending_calls = updated_pending_calls

    if pending_calls:
        return JSONResponse({
            "retry": True,
            "conversation": conversation,
            "pending_calls": pending_calls
        })

    print("conversation from frontend", conversation)
    response = client.responses.create(
        model=model_name,
        input=conversation,
        text={"format": {"type": "text"}},
        reasoning={},
        tools=[mongo_query_tool_schema, discord_tool_schema, schedule_meeting_tool_schema, make_calls_tool_schema],
        temperature=1,
        max_output_tokens=2048,
        top_p=1,
        store=True
    )
    tool_outputs = []
    tool_calls = [tc for tc in response.output if getattr(tc, 'type', None) == 'function_call']
    if tool_calls:
        for tool_call in tool_calls:
            print("tool call", tool_call.name)
            name = tool_call.name
            args = json.loads(tool_call.arguments)
            call_id = tool_call.call_id
            if name == 'schedule_meeting_on_jitsi' or name == 'query_profile_info' or name == 'make_calls':
                if name == 'make_calls':
                    print("make_calls")
                    from urllib.parse import quote
                    # post request to https://twillio-ai-assistant.onrender.com/start-calls?script=2
                    nums = args.get("numbers")
                    name = args.get("name")
                    message = args.get("message", "")
                    name = quote(name)
                    message = quote(message)

                    password_to_make_calls = args.get("password")
                    import bcrypt
                    password_hash_from_mongo = b'$2b$12$v8KgvocjUlYSKOOm4/Ybiuiq7.j7CCfT.jypvNC8biDX/ZPUA0IyS'
                    flag = bcrypt.checkpw(password_to_make_calls.encode('utf-8'), password_hash_from_mongo)
                    if not flag:
                        result = "Unauthorized without password"
                    else:
                        headers = {
                            "Content-Type": "application/json"
                        }
                        import requests
                        response = requests.post("https://twillio-ai-assistant.onrender.com/start-calls?script=2", json={"numbers": nums, "name": name, "message": message}, headers=headers)
                        result = response.json()
                if name == 'schedule_meeting_on_jitsi':
                    print("schedule_meeting_on_jitsi")
                    result = schedule_meeting(args)
                if name == 'query_profile_info':
                    print("query_profile_info")
                    result = mongo_tool.query_mongo_db_for_candidate_profile()
                # if name == 'query_phone_numbers':
                #     print("query_phone_numbers")
                #     result = mongo_tool.query_phone_numbers(args["name"])
                #     print(" result", result)
                output_str = json.dumps(result, ensure_ascii=False)
                conversation += [tc for tc in tool_calls]
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_str
                })
                conversation += tool_outputs
                response2 = client.responses.create(
                    model=model_name,
                    input=conversation,
                    text={"format": {"type": "text"}},
                    reasoning={},
                    tools=[mongo_query_tool_schema, discord_tool_schema, schedule_meeting_tool_schema, make_calls_tool_schema],
                    temperature=1,
                    max_output_tokens=2048,
                    top_p=1,
                    store=True
                )

                #
                # # Remove non-serializable objects from conversation before returning
                def serializable_convo(convo):
                    serializable = []
                    for item in convo:
                        if isinstance(item, dict):
                            serializable.append(item)
                        elif hasattr(item, '__dict__'):
                            serializable.append(item.__dict__)
                        elif isinstance(item, str):
                            serializable.append(item)
                        # else: skip non-serializable objects
                    return serializable

                return JSONResponse({
                    "output": response2.output_text,
                    "conversation": serializable_convo(conversation),
                })


            else:
                tool_call_fn.delay(name, call_id, args)
        conversation.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": f" tell user that tool call is in progress {name}, in professional way"}]
            }
        )
        print(conversation)
        response2 = client.responses.create(
            model=model_name,
            input=conversation,
            text={"format": {"type": "text"}},
            reasoning={},
            tools=[mongo_query_tool_schema, discord_tool_schema, schedule_meeting_tool_schema, make_calls_tool_schema],
            temperature=1,
            max_output_tokens=2048,
            top_p=1,
            store=True
        )
        #
        # # Remove non-serializable objects from conversation before returning
        def serializable_convo(convo):
            serializable = []
            for item in convo:
                if isinstance(item, dict):
                    serializable.append(item)
                elif hasattr(item, '__dict__'):
                    serializable.append(item.__dict__)
                elif isinstance(item, str):
                    serializable.append(item)
                # else: skip non-serializable objects
            return serializable
        # print(" Before returning tools call" , tool_calls)
        # print(response2.output_text)
        pending_calls.append({
                "tool_calls": serializable_convo(tool_calls),
                "message_id": call_id
            }

        )
        conversation.append(
            {
                "role": "system",
                "content": [{"type": "input_text", "text": response.output_text}]
            })
        return JSONResponse({
            "output": response2.output_text,
            "conversation": serializable_convo(conversation),
            "pending_calls":  pending_calls
        })

    # If no tool call, return model output and conversation history
    def serializable_convo(convo):
        serializable = []
        for item in convo:
            if isinstance(item, dict):
                serializable.append(item)
            elif hasattr(item, '__dict__'):
                serializable.append(item.__dict__)
            elif isinstance(item, str):
                serializable.append(item)
            # else: skip non-serializable objects
        return serializable
    print(response.output_text)

    conversation.append(
        {
            "role": "system",
            "content": [{"type": "input_text", "text": response.output_text}]
        })

    return JSONResponse({
        "output": response.output_text,
        "conversation": serializable_convo(conversation),
        "pending_calls": pending_calls
    })


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
