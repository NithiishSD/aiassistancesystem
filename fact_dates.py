"""
Anchoring relative dates in stored facts (ROADMAP A2 follow-up).

"User's exam: next week" is true on the day it is said and misleading a month
later. When a fact is stored, the first relative time expression in it is kept
as the user said it and followed by what it meant on that day:

    User's exam: next week (week of 5 October 2026)
    User's move date: last month (August 2026)
    User's interview: tomorrow (1 October 2026)

An expression with no single reading ("next Friday", "next semester") is
anchored to the day it was said instead of being guessed at:

    User's presentation: next Friday (said on Wednesday 30 September 2026)

Rules only; no model call.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9, "ten": 10, "couple of": 2, "few": 3}
_COUNT = r"(\d{1,3}|" + "|".join(_NUMBERS) + r")"
_DAY_NAMES = "|".join(_WEEKDAYS)
_ANNOTATED_RE = re.compile(r"\((?:said on|week of|around)?\s*\w*\s*\d{0,2}\s*\w+ \d{4}\)|\(\d{4}\)")


def _date(day: datetime) -> str:
    return f"{day.day} {day.strftime('%B %Y')}"


def _month(now: datetime, offset: int) -> str:
    index = now.year * 12 + now.month - 1 + offset
    return datetime(index // 12, index % 12 + 1, 1).strftime("%B %Y")


def _week(now: datetime, offset: int) -> str:
    monday = now - timedelta(days=now.weekday()) + timedelta(weeks=offset)
    return f"week of {_date(monday)}"


def _count(word: str) -> int:
    return int(word) if word.isdigit() else _NUMBERS[word.lower()]


def _shift(now: datetime, amount: int, unit: str) -> str:
    unit = unit.lower()
    if unit == "day":
        return _date(now + timedelta(days=amount))
    if unit == "week":
        return "around " + _date(now + timedelta(weeks=amount))
    if unit == "month":
        return _month(now, amount)
    return str(now.year + amount)


def _coming(now: datetime, day_name: str) -> str:
    ahead = (_WEEKDAYS.index(day_name.lower()) - now.weekday()) % 7 or 7
    return _date(now + timedelta(days=ahead))


_OFFSET = {"this": 0, "next": 1, "last": -1}

# (pattern, what it meant on the day it was said). First match in the text wins.
_RULES = [
    (re.compile(r"\b(today|tonight)\b", re.I), lambda now, m: _date(now)),
    (re.compile(r"\bday after tomorrow\b", re.I), lambda now, m: _date(now + timedelta(days=2))),
    (re.compile(r"\btomorrow\b", re.I), lambda now, m: _date(now + timedelta(days=1))),
    (re.compile(r"\byesterday\b", re.I), lambda now, m: _date(now - timedelta(days=1))),
    (re.compile(r"\b(this|next|last) week\b", re.I), lambda now, m: _week(now, _OFFSET[m.group(1).lower()])),
    (re.compile(r"\b(this|next|last) month\b", re.I), lambda now, m: _month(now, _OFFSET[m.group(1).lower()])),
    (re.compile(r"\b(this|next|last) year\b", re.I), lambda now, m: str(now.year + _OFFSET[m.group(1).lower()])),
    (re.compile(rf"\bin {_COUNT} (day|week|month|year)s?\b", re.I),
     lambda now, m: _shift(now, _count(m.group(1)), m.group(2))),
    (re.compile(rf"\b{_COUNT} (day|week|month|year)s? ago\b", re.I),
     lambda now, m: _shift(now, -_count(m.group(1)), m.group(2))),
    (re.compile(rf"\b(?:this|this coming|coming|on) ({_DAY_NAMES})\b", re.I), lambda now, m: _coming(now, m.group(1))),
    # No single reading: keep the words and record the day they were said.
    (re.compile(rf"\b(?:next|last) (?:{_DAY_NAMES}|semester|term|weekend)\b|\bthis (?:semester|term|weekend)\b", re.I),
     lambda now, m: f"said on {now.strftime('%A')} {_date(now)}"),
]


def anchor_relative_dates(text: str, now: datetime) -> str:
    """`text` with its first relative time expression followed by what it meant
    on `now`. Text without one, or already anchored, is returned unchanged."""
    if not text or _ANNOTATED_RE.search(text):
        return text
    best = None
    for pattern, meaning in _RULES:
        match = pattern.search(text)
        if match and (best is None or match.start() < best[0].start()):
            best = (match, meaning)
    if best is None:
        return text
    match, meaning = best
    return f"{text[:match.end()]} ({meaning(now, match)}){text[match.end():]}"
