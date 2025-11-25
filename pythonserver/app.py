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
from datetime import datetime
from fastapi import Body





# get api key from environment variable
api_key = os.getenv("OPENAI_API_KEY")


# models : gpt-4.1, gpt-4.1-mini, gpt-4.1-nano
model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4.1-nano")

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
                "content": [{"type": "input_text", "text": "You are Samarth Mahendra’s AI personal assistant who usually talks to recruiters or anyone who is interested in samarth's profile or would want to hire him.\n\nYour capabilities include:\n- Communicating with Samarth via Discord to ask questions (only if necessary, try to answer withtout discord)or relay information.\n- Querying a MongoDB database to retrieve or verify candidate profiles and job fit.\n- Scheduling meetings only using Jitsi and sending out meeting invitations.\n- You can query the database for any information about Samarth.\n\nGuidelines:\n- Before pinging Samarth on Discord, always gather all relevant information from the user or available sources.\n- When evaluating if someone is a good match for a job, always gather the job information first, then check the candidate profile using the MongoDB tool.\n- When checking Samarth’s availability for meetings, never query the database; always confirm with Samarth directly on Discord.\n- Always act professionally and on behalf of Samarth.\n- Don't ping again to discord if any reply is pending, use one function tool at a time"}]
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



