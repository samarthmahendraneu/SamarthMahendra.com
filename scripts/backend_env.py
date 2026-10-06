#!/usr/bin/env python3
"""Print the environment settings the backend's security update needs.

Generates fresh secrets on this machine and prints, for each Render service
in the order to deploy them, the KEY=value lines to paste into that service's
Environment tab ("Add from .env"). A value two services share is generated
once, so both get the same one. Nothing is saved or sent anywhere, and every
run makes new secrets: paste everything from a single run.

    python3 scripts/backend_env.py
    python3 scripts/backend_env.py --discord-id 123456789012345678
    python3 scripts/backend_env.py --discord-id 111...,222... --no-admin-token

Your Discord user ID: in Discord's settings, Advanced > Developer Mode on,
then right-click your name > Copy User ID.
"""

import argparse
import re
import secrets
import sys

# Paste your Discord user ID between the quotes (several: separate with
# commas). Left empty, the script asks for it when run.
DISCORD_USER_ID = ""

WORKER ="1. Worker (pythonserver, start command: bash start_workers.sh)"
VOICE = "2. Voice service (twilio_server, twillio-ai-assistant.onrender.com)"
CHAT = "3. Chat service (pythonserver/app.py, samarthmahendra-github-io.onrender.com)"


def discord_ids(raw):
    """Discord user IDs are 17-20 digit numbers; several may be given."""
    ids = re.findall(r"\d+", raw or "")
    wrong = [i for i in ids if not 17 <= len(i) <= 20]
    if wrong:
        sys.exit(f"Not a Discord user ID: {', '.join(wrong)} (they're 17-20 digits)")
    return ",".join(ids)


def main():
    parser = argparse.ArgumentParser(description="Print the backend's new Render settings.")
    parser.add_argument("--discord-id", help="your Discord user ID; separate several with commas")
    parser.add_argument("--no-admin-token", action="store_true",
                        help="skip ADMIN_TOKEN, leaving the admin endpoints locked to everyone")
    args = parser.parse_args()

    raw_discord = args.discord_id or DISCORD_USER_ID or None
    if raw_discord is None and sys.stdin.isatty():
        raw_discord = input("Your Discord user ID (Enter to skip): ")
    discord = discord_ids(raw_discord)

    voice_api_token = secrets.token_urlsafe(32)
    # Typed (or pasted) into the website chat to place calls: 24 characters.
    chat_calls_password = secrets.token_urlsafe(18)

    services = {
        WORKER: {"VOICE_API_TOKEN": voice_api_token, "CHAT_CALLS_PASSWORD": chat_calls_password},
        VOICE: {"VOICE_API_TOKEN": voice_api_token},
        CHAT: {"CHAT_CALLS_PASSWORD": chat_calls_password},
    }
    if discord:
        services[WORKER]["DISCORD_ANSWER_USER_IDS"] = discord
    if not args.no_admin_token:
        services[CHAT]["ADMIN_TOKEN"] = secrets.token_urlsafe(32)

    for name, settings in services.items():
        print(f"\n# {name}")
        for key, value in settings.items():
            print(f"{key}={value}")

    print("\n# On the voice service, keep TWILIO_AUTH_TOKEN as it is: it now also checks")
    print("# that call webhooks come from Twilio. Don't set TWILIO_SKIP_SIGNATURE_CHECK")
    print("# unless calls break and you need them back while you fix it (set it to 1).")
    if not discord:
        print("# No Discord ID: anyone who can post in the channel can answer callers.")
    print("# Keep CHAT_CALLS_PASSWORD somewhere safe: you need it to place calls from the chat.")


if __name__ == "__main__":
    main()
