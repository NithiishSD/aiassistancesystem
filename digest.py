"""
The daily digest (ROADMAP D1): what Zedek says without being asked.

Built only from data already on disk: the practice tracker and stored facts.
No model call, so every figure is the tracker's own and nothing is invented.
With nothing to report it returns "", and nothing is delivered.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Callable

from zedek_logger import get_logger

log = get_logger("digest")

STALE_AFTER_DAYS = 7
MAX_STALE_TOPICS = 3
MAX_WEAK_TOPICS = 3
MAX_COMMITMENTS = 5

# Facts worth resurfacing: things with a date attached in the user's head.
_COMMITMENT_RE = re.compile(
    r"\b(exams?|tests?|quiz(?:zes)?|deadlines?|due|interviews?|assignments?|submissions?|"
    r"viva|presentations?|contests?|hackathons?)\b", re.IGNORECASE)


def _stored_commitments() -> list[dict]:
    import memory

    facts: list[dict] = []
    for domain in ("academic", "personal"):
        try:
            facts.extend(memory.current_facts(domain))
        except Exception as error:  # a broken store must not break the digest
            log.info("digest_memory_unavailable", extra={"domain": domain, "error": str(error)[:200]})
    return facts


def _age(timestamp: float | None, now: datetime) -> str:
    if not timestamp:
        return "noted at an unknown date"
    days = int((now.timestamp() - timestamp) // 86400)
    if days <= 0:
        return "noted today"
    return f"noted {days} day{'s' if days != 1 else ''} ago"


def practice_lines(tracker, now: datetime) -> list[str]:
    stats = tracker.all_topic_stats()
    if not stats:
        return []
    lines: list[str] = []

    stale = sorted((s for s in stats if s.days_since_practice(now) >= STALE_AFTER_DAYS),
                   key=lambda s: s.days_since_practice(now), reverse=True)[:MAX_STALE_TOPICS]
    for topic in stale:
        lines.append(f"- {topic.topic}: not practised in {int(topic.days_since_practice(now))} days "
                     f"({round(topic.raw_accuracy * 100)}% over {topic.attempts} attempt(s)).")

    named = {topic.topic for topic in stale}
    for weak in tracker.weak_topics(limit=MAX_WEAK_TOPICS, now=now):
        if weak.topic not in named:
            lines.append(f"- {weak.topic}: {weak.reason}.")

    streak = tracker.practice_streak(now)
    if streak:
        lines.append(f"- Practice streak: {streak} day{'s' if streak != 1 else ''}.")
    return lines


def commitment_lines(facts: list[dict], now: datetime) -> list[str]:
    matching = [fact for fact in facts if _COMMITMENT_RE.search(fact.get("text", ""))]
    matching.sort(key=lambda fact: (fact.get("metadata") or {}).get("timestamp") or 0, reverse=True)
    return [f"- {fact['text']} ({_age((fact.get('metadata') or {}).get('timestamp'), now)})"
            for fact in matching[:MAX_COMMITMENTS]]


def build_digest(tracker=None, now: datetime | None = None,
                 facts_fn: Callable[[], list[dict]] | None = None) -> str:
    """Today's digest as plain text, or "" when there is nothing to report."""
    now = now or datetime.fromtimestamp(time.time())
    if tracker is None:
        from academic_tracker import AcademicTracker
        tracker = AcademicTracker()

    sections: list[str] = []
    practice = practice_lines(tracker, now)
    if practice:
        sections.append("Practice:\n" + "\n".join(practice))
    commitments = commitment_lines((facts_fn or _stored_commitments)(), now)
    if commitments:
        sections.append("You told me about (check the dates still hold):\n" + "\n".join(commitments))

    if not sections:
        return ""
    return f"Daily digest — {now.strftime('%A %d %B')}\n\n" + "\n\n".join(sections)


if __name__ == "__main__":
    print(build_digest() or "Nothing to report today.")
