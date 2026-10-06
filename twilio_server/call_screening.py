"""Spam and spoofing screening for inbound calls.

Three layers, cheapest first:

1. The webhook (`screen_webhook`): what Twilio already tells us about the call,
   at no cost and with no network calls. Withheld caller ID, a number that
   isn't a number, our own number calling us, a caller ID sharing our number's
   first six digits (neighbour spoofing), the STIR/SHAKEN attestation
   (`StirVerstat`), and Twilio's caller-name (CNAM) if the number has it on.
2. A reverse lookup (`CallScreener.lookup`): Twilio Lookup v2 for line type,
   carrier and registered caller name, and IPQualityScore's spam reputation if
   IPQS_API_KEY is set. It's prefetched when the call connects and cached for a
   day per number, so the assistant's tool and the end-of-call report reuse it.
3. What the caller says (`claim_flags`): who they say they are and what they
   want. Government agencies, banks and tech support asking for gift cards,
   codes, remote access, or threatening arrest are the classic scripts.

`assess` folds all three into a score, a level and plain reasons, and
`report_text` turns them into the Discord note. Nothing here tells the caller
they were flagged: results go to the backend model and to Samarth only.
"""

import json
import logging
import re
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

LOOKUP_TTL = 86400
LOOKUP_KEY = "screen:lookup:"
LEVELS = (("high", 60), ("medium", 30), ("low", 0))

# Twilio's stand-in numbers for a caller who withheld their ID, plus the words
# carriers put in their place.
WITHHELD_NUMBERS = {"+266696687": "anonymous", "+7378742833": "restricted",
                    "+8656696": "unavailable", "+2562533": "blocked"}
WITHHELD_WORDS = {"anonymous", "restricted", "unavailable", "unknown", "private", "blocked", ""}
SPAM_NAME = re.compile(r"\b(spam|scam|fraud|telemarket\w*|robo ?call\w*|suspected|potential)\b", re.I)

# StirVerstat values Twilio passes on signed calls, with what each one means.
ATTESTATION = {
    "TN-Validation-Passed-A": (-10, "Caller ID verified by the originating carrier (STIR/SHAKEN A)"),
    "TN-Validation-Passed-B": (5, "Carrier knows the customer but not this number (STIR/SHAKEN B)"),
    "TN-Validation-Passed-C": (15, "Carrier only knows where the call entered its network (STIR/SHAKEN C)"),
    "No-TN-Validation": (10, "The call was not signed, so the caller ID is unverified"),
}
LINE_TYPE_SCORES = {"nonFixedVoip": (20, "a non-fixed VoIP number, cheap to get and common for spam"),
                    "voip": (10, "a VoIP number"), "tollFree": (10, "a toll-free number"),
                    "unknown": (5, "a number of unknown type")}
# IPQualityScore's line types, in Twilio Lookup's vocabulary.
IPQS_LINE_TYPES = {"voip": "voip", "wireless": "mobile", "landline": "landline", "toll free": "tollFree",
                   "pager": "pager", "satellite": "unknown", "premium rate": "premium"}

# Who callers commonly impersonate, and how to reach the real one. These are the
# agencies' publicly listed lines; the report says to confirm them on the .gov site.
AGENCIES = [
    (re.compile(r"\b(irs|internal revenue|tax(es)?)\b", re.I), "IRS", "800-829-1040 (irs.gov)"),
    (re.compile(r"\b(ssa|social security)\b", re.I), "Social Security", "800-772-1213 (ssa.gov)"),
    (re.compile(r"\b(uscis|immigration|ice|homeland security|dhs|visa)\b", re.I), "USCIS / immigration",
     "800-375-5283 (uscis.gov)"),
    (re.compile(r"\b(fbi|federal bureau)\b", re.I), "FBI", "1-800-CALL-FBI (fbi.gov)"),
    (re.compile(r"\b(medicare|medicaid)\b", re.I), "Medicare", "1-800-633-4227 (medicare.gov)"),
]
GOVERNMENT = re.compile(r"\b(government|federal|state|county|city|police|sheriff|officer|court|"
                        r"judge|marshal|irs|ssa|uscis|immigration|fbi|dea|treasury|department|agency|"
                        r"embassy|consulate|customs|border|medicare|jury)\b", re.I)
RED_FLAGS = [
    (re.compile(r"gift ?cards?|itunes|google play card|steam card", re.I), "asked for gift cards"),
    (re.compile(r"bitcoin|crypto|usdt|btc|eth\b|wallet address|bitcoin atm", re.I), "asked for cryptocurrency"),
    (re.compile(r"wire transfer|western union|moneygram|zelle|venmo|cash ?app|paypal", re.I),
     "asked for a hard-to-reverse payment"),
    (re.compile(r"\barrest|warrant|jail|deport|police will|legal action|lawsuit|suspend", re.I),
     "threatened arrest, deportation or suspension"),
    (re.compile(r"social security number|\bssn\b|bank account|routing number|card number|passport number", re.I),
     "asked for identity or account numbers"),
    (re.compile(r"verification code|one[- ]time|\botp\b|security code|passcode|password|\bpin\b", re.I),
     "asked for a code or password"),
    (re.compile(r"anydesk|teamviewer|remote access|install|download", re.I), "asked for remote access to a device"),
    (re.compile(r"immediately|right now|\btoday\b|\btonight\b|within (an|one|the|\d+) (hour|minute)s?|urgent|"
                r"don't hang up|stay on the line", re.I), "pressed for urgency"),
    (re.compile(r"don't tell|keep (this|it) (secret|confidential)|tell no one", re.I), "asked for secrecy"),
]


def _e164(raw):
    number = re.sub(r"[\s().\-]", "", raw or "")
    if number and not number.startswith("+") and len(number) == 10:
        number = "+1" + number
    elif number and not number.startswith("+") and len(number) == 11 and number.startswith("1"):
        number = "+" + number
    return number if re.fullmatch(r"\+[1-9]\d{7,14}", number) else None


def _prefix(number):
    """A NANP number's area code and exchange: +1 617 555 xxxx -> 617555."""
    return number[2:8] if number and number.startswith("+1") and len(number) == 12 else None


def level_for(score):
    """A verified caller can score below zero; that's still low."""
    return next(name for name, floor in LEVELS if max(score, 0) >= floor)


def screen_webhook(fields, own_numbers=()):
    """Signals from Twilio's inbound-call webhook alone. No network calls."""
    raw_from = (fields.get("From") or "").strip()
    number = _e164(raw_from)
    caller_id_name = (fields.get("CallerName") or "").strip()
    verstat = (fields.get("StirVerstat") or "").strip()
    reasons, spoofing = [], []
    score = 0

    withheld = WITHHELD_NUMBERS.get(raw_from) or (raw_from.lower() if raw_from.lower() in WITHHELD_WORDS else None)
    if withheld is not None:
        score += 40
        reasons.append(f"Caller ID withheld ({withheld or 'blank'})")
        number = None
    elif number is None:
        score += 30
        reasons.append(f"Caller ID {raw_from!r} isn't a valid phone number")
        spoofing.append("caller ID isn't a dialable number")

    own = {n for n in (_e164(o) for o in own_numbers) if n}
    if number and number in own:
        score += 60
        reasons.append("The call shows one of our own numbers as its caller ID")
        spoofing.append("our own number was used as the caller ID")
    elif number and _prefix(number) and _prefix(number) in {_prefix(n) for n in own}:
        score += 20
        reasons.append("Caller ID shares our number's area code and exchange (neighbour spoofing pattern)")
        spoofing.append("neighbour spoofing pattern")

    if verstat.startswith("TN-Validation-Failed"):
        score += 50
        reasons.append("STIR/SHAKEN validation failed: the caller ID was likely spoofed")
        spoofing.append(f"STIR/SHAKEN {verstat}")
    elif verstat in ATTESTATION:
        points, why = ATTESTATION[verstat]
        score += points
        reasons.append(why)
        if verstat in ("TN-Validation-Passed-C", "No-TN-Validation"):
            spoofing.append(why)

    if caller_id_name and SPAM_NAME.search(caller_id_name):
        score += 40
        reasons.append(f"Caller ID name says {caller_id_name!r}")

    city = (fields.get("FromCity") or "").strip()
    where = ", ".join(p for p in (city.title() if city.isupper() else city,
                                  fields.get("FromState"), fields.get("FromCountry")) if p)
    return {"number": number, "raw_from": raw_from, "caller_id_name": caller_id_name,
            "attestation": verstat or "none", "location": where,
            "score": score, "reasons": reasons, "spoofing": spoofing}


def lookup_signals(lookup):
    """Score points and reasons from a reverse lookup result."""
    score, reasons, spoofing = 0, [], []
    if not lookup or lookup.get("status") != "ok":
        return score, reasons, spoofing
    if lookup.get("valid") is False:
        score += 30
        reasons.append("Lookup says the number isn't valid")
        spoofing.append("number calling us isn't a valid assigned number")
    line_type = lookup.get("line_type")
    if line_type in LINE_TYPE_SCORES:
        points, why = LINE_TYPE_SCORES[line_type]
        score += points
        reasons.append(f"It's {why}")
    cnam = lookup.get("registered_name") or ""
    if cnam and SPAM_NAME.search(cnam):
        score += 40
        reasons.append(f"Registered caller name is {cnam!r}")
    rep = lookup.get("reputation") or {}
    fraud = rep.get("fraud_score")
    if isinstance(fraud, (int, float)):
        if fraud >= 90:
            score += 40
            reasons.append(f"Spam reputation score {fraud}/100 (very high)")
        elif fraud >= 75:
            score += 25
            reasons.append(f"Spam reputation score {fraud}/100 (high)")
    if rep.get("spammer"):
        score += 40
        reasons.append("Number is reported as a spammer")
    if rep.get("recent_abuse"):
        score += 25
        reasons.append("Number was recently reported for abuse")
    if rep.get("active") is False:
        score += 15
        reasons.append("Number doesn't look active, which is common for spoofed caller IDs")
        spoofing.append("caller ID belongs to an inactive number")
    return score, reasons, spoofing


def claim_flags(claims):
    """Red flags in what the caller said, and which agency they claimed to be."""
    text = " ".join(str(v) for v in (claims or {}).values() if v)
    flags = [why for pattern, why in RED_FLAGS if pattern.search(text)]
    who = " ".join(str((claims or {}).get(k) or "") for k in ("organization", "department", "category"))
    agency = next(((name, line) for pattern, name, line in AGENCIES if pattern.search(who)), None)
    government = bool(GOVERNMENT.search(who))
    return flags, agency, government


def assess(screening, lookup=None, claims=None):
    """One verdict from the webhook, the lookup and the conversation."""
    screening = screening or {}
    score = screening.get("score", 0)
    reasons = list(screening.get("reasons", []))
    spoofing = list(screening.get("spoofing", []))
    l_score, l_reasons, l_spoof = lookup_signals(lookup)
    score += l_score
    reasons += l_reasons
    spoofing += l_spoof
    flags, agency, government = claim_flags(claims)
    if flags:
        score += 15 * len(flags)
        reasons += [f"Caller {flag}" for flag in flags]
    if government and lookup and lookup.get("line_type") in ("voip", "nonFixedVoip"):
        score += 15
        reasons.append("Claims to be government but is calling from a VoIP number")
    if government and flags:
        score += 20
        reasons.append("Government agencies don't demand payment or codes over the phone")
    callback = _e164((claims or {}).get("callback_number") or "")
    if callback and screening.get("number") and callback != screening["number"]:
        reasons.append("The callback number they gave differs from their caller ID")
    spoof_score = len(spoofing)
    return {"score": max(score, 0), "level": level_for(max(score, 0)), "reasons": reasons,
            "spoofing": {"risk": "high" if any("STIR/SHAKEN TN-Validation-Failed" in s or "own number" in s
                                               for s in spoofing) else
                         "medium" if spoof_score else "low", "signals": spoofing},
            "red_flags": flags, "agency": agency, "claims_government": government}


def summary_for_model(screening):
    """The compact part of the webhook screening the backend model sees."""
    if not screening:
        return None
    return {"risk": level_for(screening.get("score", 0)), "score": screening.get("score", 0),
            "reasons": screening.get("reasons", []), "spoofing_signals": screening.get("spoofing", []),
            "caller_id_name": screening.get("caller_id_name", "")}


def _fmt_lookup(lookup):
    if not lookup:
        return "not run"
    if lookup.get("status") != "ok":
        return lookup.get("message", "unavailable")
    parts = [p for p in (
        lookup.get("line_type") and f"line {lookup['line_type']}",
        lookup.get("carrier") and f"carrier {lookup['carrier']}",
        lookup.get("registered_name") and f"name {lookup['registered_name']!r}",
        lookup.get("country") and f"country {lookup['country']}",
    ) if p]
    rep = lookup.get("reputation") or {}
    if rep:
        bits = [f"fraud score {rep['fraud_score']}" if rep.get("fraud_score") is not None else None,
                "spammer" if rep.get("spammer") else None, "recent abuse" if rep.get("recent_abuse") else None,
                "inactive" if rep.get("active") is False else None]
        parts.append("reputation: " + (", ".join(b for b in bits if b) or "clean"))
    return "; ".join(parts) or "no details"


def report_text(screening, verdict, lookup=None, claims=None, callback_lookup=None, call_sid="", ended_early=False):
    """The Discord note: everything known about the call, most useful first."""
    screening, claims = screening or {}, claims or {}
    number = screening.get("number") or screening.get("raw_from") or "withheld"
    head = (f"Screened call ended before details were collected: {verdict['level'].upper()} risk"
            if ended_early else f"Suspicious call screened: {verdict['level'].upper()} risk")
    caller_id = f" (caller ID name {screening['caller_id_name']!r})" if screening.get("caller_id_name") else ""
    lines = [f"{head} (score {verdict['score']})",
             f"From: {number}{caller_id}" + (f", {screening['location']}" if screening.get("location") else "")
             + (f" · call {call_sid}" if call_sid else ""),
             f"Spoofing risk: {verdict['spoofing']['risk']}"
             + (f" ({'; '.join(verdict['spoofing']['signals'])})" if verdict["spoofing"]["signals"] else ""),
             f"STIR/SHAKEN: {screening.get('attestation', 'none')}",
             f"Reverse lookup: {_fmt_lookup(lookup)}"]
    who = " / ".join(v for v in (claims.get("organization"), claims.get("department")) if v)
    fields = [("Says they are", " · ".join(v for v in (claims.get("caller_name"), who) if v)),
              ("Category", claims.get("category")), ("ID / badge", claims.get("official_id")),
              ("Case / reference no.", claims.get("case_number")),
              ("Callback number given", claims.get("callback_number")
               and f"{claims['callback_number']} (lookup: {_fmt_lookup(callback_lookup)})"),
              ("What they want", claims.get("reason")), ("Demands", claims.get("demands")),
              ("Other details", claims.get("other_details"))]
    lines += [f"{label}: {value}" for label, value in fields if value]
    if verdict["red_flags"]:
        lines.append("Red flags: " + "; ".join(verdict["red_flags"]))
    if verdict["reasons"]:
        lines.append("Why: " + "; ".join(verdict["reasons"]))
    if verdict.get("agency"):
        name, line = verdict["agency"]
        lines.append(f"Verify independently: {name} {line}. Don't use a number the caller gave.")
    elif verdict.get("claims_government"):
        lines.append("Verify independently through the agency's number on its official .gov site.")
    if screening.get("number"):
        lines.append("Search the number: https://www.google.com/search?q=" + quote(f'"{screening["number"]}"'))
    return "\n".join(lines)


class CallScreener:
    """Reverse lookups with a Redis cache. Never raises: a failed lookup is a result."""

    def __init__(self, redis, twilio_client=None, ipqs_key="", lookup_enabled=True, http_get=requests.get):
        self.redis = redis
        self.twilio = twilio_client
        self.ipqs_key = ipqs_key
        self.lookup_enabled = lookup_enabled
        self.http_get = http_get

    @classmethod
    def from_env(cls, redis, twilio_client, env):
        return cls(redis, twilio_client, ipqs_key=env.get("IPQS_API_KEY", ""),
                   lookup_enabled=env.get("SCREENING_LOOKUP", "1") != "0")

    def lookup(self, raw_number):
        number = _e164(raw_number)
        if number is None:
            return {"status": "invalid", "message": "Not a phone number that can be looked up."}
        if not self.lookup_enabled:
            return {"status": "disabled", "message": "Reverse lookup is turned off (SCREENING_LOOKUP=0)."}
        try:
            cached = self.redis.get(LOOKUP_KEY + number)
            if cached:
                return json.loads(cached)
        except Exception as exc:
            logger.warning("Lookup cache read failed (%s)", type(exc).__name__)
        result = {"status": "ok", "number": number}
        sources = []
        if self.twilio is not None:
            try:
                info = self.twilio.lookups.v2.phone_numbers(number).fetch(
                    fields="line_type_intelligence,caller_name")
                line = info.line_type_intelligence or {}
                name = info.caller_name or {}
                result.update(valid=info.valid, country=info.country_code, national_format=info.national_format,
                              line_type=line.get("type"), carrier=line.get("carrier_name"),
                              registered_name=name.get("caller_name"), registered_type=name.get("caller_type"))
                sources.append("twilio")
            except Exception as exc:
                logger.warning("Twilio Lookup failed (%s: %s)", type(exc).__name__, getattr(exc, "msg", exc))
        if self.ipqs_key:
            try:
                resp = self.http_get(f"https://www.ipqualityscore.com/api/json/phone/{self.ipqs_key}/{number[1:]}",
                                     params={"strictness": 1}, timeout=6)
                data = resp.json()
                if data.get("success"):
                    result["reputation"] = {k: data.get(k) for k in (
                        "fraud_score", "spammer", "recent_abuse", "risky", "active", "VOIP", "prepaid",
                        "do_not_call", "leaked")}
                    if not result.get("carrier"):
                        result["carrier"] = data.get("carrier")
                    if not result.get("line_type"):
                        result["line_type"] = IPQS_LINE_TYPES.get((data.get("line_type") or "").lower())
                    if not result.get("registered_name") and data.get("name") not in (None, "", "N/A"):
                        result["registered_name"] = data["name"]
                    sources.append("ipqs")
            except Exception as exc:
                logger.warning("IPQS lookup failed (%s)", type(exc).__name__)
        if not sources:
            return {"status": "unavailable", "number": number,
                    "message": "The reverse lookup services couldn't be reached."}
        result["sources"] = sources
        try:
            self.redis.setex(LOOKUP_KEY + number, LOOKUP_TTL, json.dumps(result))
        except Exception as exc:
            logger.warning("Lookup cache write failed (%s)", type(exc).__name__)
        return result
