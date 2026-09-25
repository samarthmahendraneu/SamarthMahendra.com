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

Keep `OPENAI_API_KEY`, Twilio credentials/number, Redis, MongoDB, SMTP, Discord,
and `PORT` settings. The same project key must have access to both configured
OpenAI models. Voice sessions and delegated backend usage are billed separately.
The Celery worker's environment and launch command do not need migration changes.
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
   Use Python 3.11 or newer. Keep Redis, MongoDB, and the Celery worker available.
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

## Offline tests

With the existing server requirements installed, run from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=twilio_server python -m unittest discover -s twilio_server/tests -v
```

The tests use fake audio sockets and replace MongoDB, Redis, Celery, and Twilio
call creation. They do not contact OpenAI, dial phone numbers, or send messages.
They cover startup gating, audio passthrough, function result ordering,
duplicate tool calls, failures, graceful shutdown, playback acknowledgment,
per-call context isolation, voicemail saving, and the existing HTTP routes.

## Protocol references

- [OpenAI: Live WebSockets](https://developers.openai.com/api/docs/guides/voice-websockets?api=live)
- [OpenAI: delegation and tools](https://developers.openai.com/api/docs/guides/live-delegation)
- [OpenAI: session lifecycle and voice options](https://developers.openai.com/api/docs/guides/live-conversations)
- [Twilio: Stream custom parameters](https://www.twilio.com/docs/voice/twiml/stream#custom-parameters)
