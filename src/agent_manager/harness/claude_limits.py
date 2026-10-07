"""Recognising a Claude usage-limit failure in a `claude` log.

`parse_limit` reads the one line the CLI
prints, `You've hit your <kind> limit · resets <when> (<tz>)`, and nothing
else: prose that merely mentions a limit never matches, because the line must
start with the phrase.
"""

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_manager.harness.limits import LimitHit, LimitKind, read_tail

_LINE = re.compile(
    r"^\s*You(?:'|’)ve hit your (?P<kind>.*?) limit\b(?P<rest>.*)$", re.IGNORECASE
)
_RESETS = re.compile(r"\bresets\s+(?P<when>[^()]*?)\s*(?:\((?P<tz>[^()]+)\))?\s*$", re.IGNORECASE)
_TIME = re.compile(r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>[ap]m)\b", re.IGNORECASE)
_DATE = re.compile(r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})\b")
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}


def parse_limit(text: str, now: datetime) -> LimitHit | None:
    """The limit hit `text` reports, or `None`; `now` anchors a reset that names no date.

    A bare time resets at its next occurrence after `now` in the named zone; a
    `<Mon> <d>` date resets this year, or next year when that has passed. The
    last matching line wins.
    """
    found: LimitHit | None = None
    for line in text.splitlines():
        match = _LINE.match(line)
        if match is None:
            continue
        named_kind = match["kind"].lower()
        kind: LimitKind = (
            "weekly" if "week" in named_kind else "session" if "session" in named_kind else "other"
        )
        found = LimitHit(kind, _resets_at(match["rest"], now), line.strip())
    return found


def _resets_at(rest: str, now: datetime) -> datetime | None:
    resets = _RESETS.search(rest)
    if resets is None or resets["tz"] is None:
        return None
    try:
        zone = ZoneInfo(resets["tz"].strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    when = resets["when"]
    time = _TIME.search(when)
    if time is None:
        return None
    hour, minute = int(time["hour"]), int(time["minute"] or 0)
    if not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if time["meridiem"].lower() == "pm" else 0)
    local_now = now.astimezone(zone)
    named = _DATE.search(when[: time.start()])
    try:
        if named is None:
            candidate = _wall(local_now.date(), hour, minute, zone)
            if candidate <= local_now:
                candidate = _wall(local_now.date() + timedelta(days=1), hour, minute, zone)
        else:
            month = _MONTHS.get(named["month"][:3].lower())
            if month is None:
                return None
            day = int(named["day"])
            candidate = datetime(local_now.year, month, day, hour, minute, tzinfo=zone)
            if candidate <= local_now:
                candidate = datetime(local_now.year + 1, month, day, hour, minute, tzinfo=zone)
    except ValueError:
        return None
    return candidate.astimezone(timezone.utc)


def _wall(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)


def read_limit(path: Path, clock: Callable[[], datetime]) -> LimitHit | None:
    """`parse_limit` over the tail of the log at `path`; `None` when it cannot be read."""
    text = read_tail(path)
    return None if text is None else parse_limit(text, clock())
