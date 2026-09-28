"""Time zones for the assistants: what time it is, and what moment a time means.

People give wall-clock times in their own zone: "2pm Pacific on the 5th".
resolve() turns that into an exact moment using the zone's real offset on
that date, so a November meeting isn't an hour out because the offset was
worked out in summer. Every time shown back names its zone.

Kept byte-identical in twilio_server/ and pythonserver/; the tests fail if the
copies drift.
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Samarth's own zone: what "his time" means, and the default for the clock tool.
SAMARTH_ZONE = os.getenv("SAMARTH_TIMEZONE") or "America/New_York"
# What people (and models) say for the US zones. Arizona, which keeps
# standard time all year, is America/Phoenix, not "Mountain".
ALIASES = {
    "ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
    "EASTERN": "America/New_York",
    "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
    "CENTRAL": "America/Chicago",
    "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
    "MOUNTAIN": "America/Denver",
    "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "PACIFIC": "America/Los_Angeles",
    "UTC": "UTC", "GMT": "UTC",
}


def zone(name):
    """A zone for an IANA name ("America/Los_Angeles") or a common US name
    ("Pacific", "PT"). Raises ValueError, with a reason fit to pass on."""
    key = " ".join(str(name or "").split())
    key = ALIASES.get(key.upper().removesuffix(" TIME"), key)
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'"{name}" isn\'t a timezone name I know; use one like '
                         "America/Los_Angeles or Europe/London") from None


def offset_text(moment):
    """E.g. "UTC-07:00"."""
    raw = moment.strftime("%z")
    return f"UTC{raw[:3]}:{raw[3:]}"


def clock(moment):
    """E.g. "2:00 PM"."""
    return f"{moment.hour % 12 or 12}:{moment:%M} {'AM' if moment.hour < 12 else 'PM'}"


def short(moment):
    """E.g. "Thursday 2:00 PM PDT"."""
    return f"{moment:%A} {clock(moment)} {moment:%Z}"


def readable(moment):
    """E.g. "Thursday, October 1, 2026 at 2:00 PM PDT"."""
    return f"{moment:%A, %B} {moment.day}, {moment.year} at {clock(moment)} {moment:%Z}"


def also_in(moment, zone_name):
    """A time, followed by the same moment in another zone when that reads
    differently: "Thursday 2:00 PM PDT (5:00 PM EDT)"."""
    other = moment.astimezone(zone(zone_name))
    text = short(moment)
    if other.strftime("%Z%z") != moment.strftime("%Z%z"):
        day = "" if other.date() == moment.date() else f"{other:%A} "
        text += f" ({day}{clock(other)} {other:%Z})"
    return text


def resolve(when, zone_name):
    """The exact moment a wall-clock time in a zone means.

    `when` is the local date and time as the person said it, e.g.
    "2026-10-01T14:00". An offset in it is accepted only if it is the zone's
    real one on that date: an offset carried over from another season would
    silently move the time by an hour, so that is refused instead. Raises
    ValueError, with a reason fit to pass on.
    """
    tz = zone(zone_name)
    try:
        written = datetime.fromisoformat(str(when or "").strip().replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("The time should be a date and time like 2026-10-01T14:00") from None
    wall = written.replace(tzinfo=None)
    moment = wall.replace(tzinfo=tz)
    if moment.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != wall:
        raise ValueError(f"{clock(wall)} on {wall:%B} {wall.day} doesn't exist in {tz.key}: "
                         "the clocks go forward that night")
    if written.tzinfo is not None and written.utcoffset() != moment.utcoffset():
        raise ValueError(f"{tz.key} is {offset_text(moment)} on that date, not "
                         f"{offset_text(written)}; give the local time without an offset")
    return moment


def now_in(zone_name, now=None):
    """What the clock tool reports: the time in a zone, and in Samarth's."""
    tz = zone(zone_name or SAMARTH_ZONE)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    here = now.astimezone(zone(SAMARTH_ZONE))
    return {"timezone": tz.key, "now": readable(now), "local_time": now.isoformat(timespec="minutes"),
            "utc_offset": offset_text(now), "samarth_timezone": SAMARTH_ZONE,
            "samarth_now": readable(here)}


def zone_for_number(number):
    """The zone a phone number belongs to, or None if it spans several
    (toll-free numbers, some area codes) or can't be told."""
    try:
        import phonenumbers
        from phonenumbers import timezone as number_zones
        zones = number_zones.time_zones_for_number(phonenumbers.parse(number, None))
    except Exception:
        return None
    zones = [name for name in zones if name != "Etc/Unknown"]
    if len(zones) != 1:
        return None
    try:
        return zone(zones[0]).key
    except ValueError:
        return None
