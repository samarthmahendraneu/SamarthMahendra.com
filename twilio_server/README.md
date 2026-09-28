# Twilio phone assistant — GPT-Live 1

The existing Twilio Media Streams routes now use `gpt-live-1` for conversation
and a Responses backend for profile answers, meeting scheduling, and messages.
The raw WebSocket implementation uses the existing `websockets==13.1` pin;
no OpenAI SDK or dependency upgrade is required.

## Environment changes

Merge these values into the Twilio web service's existing environment:

```dotenv
MODEL=gpt-live-1
VOICE=marin
LIVE_BACKEND_MODEL=gpt-5.6-luna
```

| Variable | Migration action |
| --- | --- |
| `MODEL` | Replace any Realtime/preview model with `gpt-live-1`. It is also the new default. An incompatible existing value fails at startup with an explicit error. |
| `VOICE` | Set `marin` to use the new default. An existing `VOICE=sage` will otherwise remain in effect; environment variables override defaults. |
| `VOICE_STYLE` | Optional speaking-style text, sent to the voice model in `session.instructions`. Omit or leave blank to use the built-in American conversational prompt. |
| `LIVE_BACKEND_MODEL` | New optional setting, default `gpt-5.6-luna`. Used by Responses for reasoning and tools, not the speaking voice. |
| `PUBLIC_BASE_URL` | New optional setting for outbound Twilio callbacks. Defaults to the existing `https://twillio-ai-assistant.onrender.com` host. Set only if your web service uses a different public hostname. |

Keep `OPENAI_API_KEY`, Twilio credentials/number, Redis, MongoDB, and `PORT`
settings. Email and Discord settings belong to the worker, not this service. The same project key must have access to both configured
OpenAI models. Voice sessions and delegated backend usage are billed separately.
The worker's launch command changes; see [Background work](#background-work-updates-and-call-backs).
See [.env.example](.env.example); it contains settings only, no credentials.

## Conversational voice style

The reviewed style is active by default for both regular calls and voicemail:
General American English, warm and composed delivery, natural phrasing and
pauses, brief replies, and selective listening acknowledgments. `VOICE=marin`
still selects the voice. No environment change is required for the new prompt.

To supply a different style without editing Python, set this optional variable:

```dotenv
VOICE_STYLE="Use General American English with a warm, relaxed conversational pace, natural pauses, and concise everyday phrasing."
```

This replaces the built-in **style paragraph**, while retaining Luma's identity,
interruption policy, delegation rules, and call-ending behavior. It is trusted
operator configuration; caller input is not used as a style override. The
application inserts it into GPT-Live's `session.instructions` at session startup,
as described in the [official prompting guide](https://developers.openai.com/api/docs/guides/live-prompting).
It does not send an unsupported `style` parameter or add the style to the backend
model's instructions. Restart the web service after changing the environment;
new calls receive the updated style. See [the design and research notes](VOICE_PROMPT_REVIEW.md).

## Deployment

1. Deploy the entire `twilio_server` directory, including `live_bridge.py`,
   `live_config.py`, and `profile_context.txt`, with the settings above.
2. Keep the existing build command (`pip install -r requirements.txt`) and web
   command (`uvicorn main:app --host 0.0.0.0 --port "$PORT"`, from this directory).
   Use Python 3.11 or newer. Keep Redis, MongoDB, and the worker available.
3. Restart/redeploy the web service after editing environment values. Allow
   existing calls to finish first: a deployment does not migrate active sockets.
4. Keep Twilio's incoming voice webhook at `/incoming-call` and voicemail webhook
   at `/voice-mail`. The `/start-calls` API and both media WebSocket paths remain.
   No SIP setup, new phone number, or Twilio dashboard URL change is required.
5. Check `/` reports `"model": "gpt-live-1"`. This proves configuration only;
   the health endpoint does not contact OpenAI or validate model access.
6. Place an authorized test call to check greeting, latency, interruptions, and
   goodbye playback. Test voicemail and scheduling with your own test details;
   those actions save records and may send email/Discord notifications.

Production environment values, OpenAI model access, and real call quality are not
verified by the offline tests. Rollback requires restoring both the prior code
and its Realtime model environment value; changing `MODEL` alone is insufficient.

## What changed

- Connect to `wss://api.openai.com/v1/live/sessions`, send `session.start`, and wait
  for `session.started` before greeting or audio. Twilio μ-law at 8 kHz passes
  through unchanged. Startup audio is bounded and paced, not replayed in a burst.
  Pacing follows audio duration without accumulating send/scheduling overhead.
  If the 250-frame input buffer fills, discard the oldest frames and keep the
  call running; mark/stop events remain readable. This can lose caller speech
  during a sustained stall. Log the first overflow and total dropped frames at
  shutdown without logging audio; long stalls reset pacing to avoid catchup bursts.
- Use `session.input_audio.append` and `session.output_audio.delta`. GPT-Live
  manages listening/speaking continuously; the old server VAD, truncation, and
  voice `response.create` logic are removed. No arbitrary clear is sent when a
  caller backchannels.
- Keep conversational style short, with factual profile context and workflows
  on the Responses backend. `profile_context.txt` preserves the existing supplied
  profile; review that record separately when updating career information.
- Handle nested `response.event` function items, return every result with
  `response.item.create`, and then continue the backend with `response.create`.
  Tools run off the audio path and duplicate call IDs are not executed again
  within the same connection. This is not durable exactly-once execution across
  process restarts. Uncertain side effects are not retried automatically.
- Fix voicemail's database argument signature and the caller-response field;
  return saved IDs and errors so the assistant can report the actual result.
- Store each call's context behind a single-use Redis token with a five-minute
  TTL, passed through Twilio `<Parameter>`. No shared name/message/script keys
  and no unsupported query string on the Stream URL.
- Wait for a brief quiet interval and a Twilio playback mark before ending an
  assistant-ended call. Live has no output-audio-done event; the conservative
  output noise gate has a bounded timeout and needs real-phone validation.
- Close with `session.close` and keep listening for `session.closed`, logging
  final usage. Missing terminal events are reported as unconfirmed usage.

## Background work, updates and call backs

Slow work no longer holds up a conversation. A tool that would make someone
wait starts it in the background and returns at once; when it finishes, the
conversation is told, and the assistant passes it on without being asked.

- **Event streams** (`events.py`): every call (`call:<CallSid>`) and website chat
  (`chat:<session>`) has a Redis stream. Samarth's Discord replies, finished
  jobs and outbound-call answers are appended to the stream of the
  conversation that asked. A live call speaks them through
  `session.commentary.append` (`call_events.py`); a chat gets a new message,
  which the browser receives from `/chat/events`.
- **Jobs** (`jobs.py`, run by `pythonserver/job_handlers.py`): meeting invite
  emails, Discord posts and chat-requested phone calls. The caller hears when
  their invite has been sent, and hears if it or a relay failed. Discord posts
  use the REST API and cannot @mention anyone.
- **Tools run in parallel.** The backend may call several tools in one turn,
  and a batch's tools run at the same time; each batch's results still go back
  together.
- **Discord replies are matched by reply-to**, not arrival order. Reply to the
  question's message (or to the bot's note about it). When everything open is
  from one caller or chat, a plain message answers its latest question; with
  two people waiting, the bot asks you to reply to the right one. A second
  reply to an answered question is passed on as a follow-up.
- **Call backs** (`callbacks.py`): the Discord listener's scheduler
  (`pythonserver/callback_scheduler.py`) finds them as they fall due and has
  this service dial them through `/start-calls`, sending only the call back's
  id, so the worker needs no Twilio keys. `request_callback` rings the caller when Samarth
  answers after they hang up. It holds for the whole call or chat, so a
  question asked again in other words is covered, and in the chat it can be
  asked for before the question or, once he has answered, rings straight away
  if the visitor has left. `schedule_callback` books a call at a time the
  caller chooses. Twilio's machine detection leaves a voicemail if nobody
  answers in person; missed calls are retried after 10 and 30 minutes, three
  tries in all. Automatic calls wait for calling hours on the caller's own
  clock. What each call is about stays in Redis; the call URL carries only an id.
- **The website chat books call backs too**, with the same scheduler: a call when
  Samarth answers a question (only if the visitor has left the chat by then;
  otherwise they see the answer there), or at a time they choose. Limits are the
  phone line's plus three per chat; Samarth's own number (`UNLIMITED_NUMBERS` in
  `callbacks.py`) has none. Set `TWILIO_VERIFY_SERVICE_SID` on the chat
  service to also require a code texted to the number before it can be rung.
- **Times are timezone-aware** (`timezones.py`). Both assistants have a
  `get_current_time` tool, so they never guess the date. Tools take the
  person's local time as they said it plus their timezone, and the server
  works out the offset for that date, so a November meeting booked in October
  isn't an hour out; an offset that contradicts the zone is refused. A
  caller's likely zone comes from their phone number (Twilio's `From`, or `To`
  on outbound calls) and a chat visitor's from their browser; the assistant
  confirms it. Emails and Discord notes give the time in the person's zone,
  and in Samarth's when it differs.

New settings, all optional:

| Variable | Where | Default |
| --- | --- | --- |
| `SAMARTH_TIMEZONE` | voice service, chat service and worker | `America/New_York` (Samarth's own clock, shown beside other people's times) |
| `CALLBACK_TIMEZONE` | voice service and worker | `America/New_York` (for callers whose timezone can't be told from what they said or their number) |
| `CALLBACK_HOURS` | voice service and worker | `10-20` (local hours for automatic call backs and retries) |
| `CALLBACK_COUNTRY_CODES` | voice service and worker | `1` (US and Canada; Caribbean +1 numbers are always refused) |
| `TWILIO_SERVICE_URL` | worker | `https://twillio-ai-assistant.onrender.com` (the voice service, which places chat-requested calls and call backs) |
| `TWILIO_VERIFY_SERVICE_SID` | chat service | unset (a Twilio Verify service; when set with `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`, chat call backs need a texted code) |
| `SAMARTH_EMAIL` | chat service | `samarth.mahendragowda@gmail.com` (copy of chat-booked meetings) |

New voice-service routes, called by Twilio only: `/callback-call` and
`/callback-status` for call backs, `/call-status` for outbound calls a chat
asked for. No Twilio dashboard change is needed. Machine detection is billed
per call back.

There is one Celery worker, deployed from `pythonserver/`. This service runs
none: `worker_client.py` sends its tasks by name through the shared Redis, and
a test checks the worker defines every task this service sends.

Every service shares the Redis plan's connection limit, 30 on Redis Cloud's
free plan. Each process caps its share (`redis_pool.py`, and the Celery
settings in `celery_worker.py`, `worker_client.py` and `start_workers.sh`):
the voice and chat services hold at most 5 connections each, the worker about
8 and the Discord listener 3. That leaves room for a new copy of a service
starting up during a deploy. Only one Celery worker should use this Redis:
another would hold connections of its own and take tasks meant for this one.

Deploy the worker first: it still runs work queued by older services, while
new services queue work only it knows. Change its start command to
`bash start_workers.sh`, which runs Celery on a thread pool
(`--pool=threads --concurrency=8`) beside the Discord listener, then deploy
the voice and chat services. Calls already in progress keep working: the new
listener still recognises questions asked by the old code.

## Offline tests

With both folders' requirements installed (the tests also cover the worker in
`pythonserver/`), run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=twilio_server python -m unittest discover -s twilio_server/tests -v
```

The tests use fake audio sockets and replace MongoDB, Redis, Celery, and Twilio
call creation. They do not contact OpenAI, dial phone numbers, or send messages.
They cover startup gating, audio passthrough, sustained audio pacing, buffer
overflow recovery, function result ordering, parallel tool batches,
duplicate tool calls, failures, graceful shutdown, playback acknowledgment,
per-call context isolation, voicemail saving, and the existing HTTP routes;
and, for the worker and chat in `pythonserver/`, Discord reply matching, the
event streams, jobs, call back scheduling and retries, and the chat's tool
loop and follow-ups. The shared modules are kept identical in both folders.

## Protocol references

- [OpenAI: Live WebSockets](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)
- [OpenAI: delegation and tools](https://developers.openai.com/api/docs/guides/live-delegation)
- [OpenAI: session lifecycle and voice options](https://developers.openai.com/api/docs/guides/live-conversations)
- [Twilio: Stream custom parameters](https://www.twilio.com/docs/voice/twiml/stream#custom-parameters)
