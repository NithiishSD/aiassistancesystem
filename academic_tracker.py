"""Academic / placement-prep tracking (Roadmap Item 14).

The "personal tutor" use case: log DSA and aptitude practice, then use that
history to identify which topics are actually weak and what to practice next.

Why this is a separate store rather than ChromaDB memory: `memory.py` is a
semantic store, good at "what did the user tell me about X". Practice history
is structured, numeric, and queried by aggregation — accuracy per topic, days
since last practice, counts. Semantic similarity is the wrong retrieval model
for that, so this keeps its own JSON store and leaves memory.py alone.

Weakness scoring, and why it is shaped this way:
  - Accuracy is smoothed (Laplace): a topic attempted once and failed scores
    as roughly 33% rather than 0%, so a single bad day doesn't permanently
    pin a topic to the top of the weak list.
  - Staleness counts: a topic solved well two months ago is a genuine risk
    for an exam next week, even with perfect historical accuracy.
  - Low volume counts: a topic with two attempts is mostly unknown, and
    unknown deserves attention — but less than known-bad.
These three combine into one score so the recommendation is explainable:
every suggestion carries the reason it was surfaced.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from zedek_logger import get_logger

log = get_logger("academic_tracker")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TRACKER_PATH = os.path.join(_PROJECT_ROOT, "data", "academic_progress.json")

# Outcome vocabulary. Kept closed so aggregates stay meaningful.
SOLVED = "solved"
FAILED = "failed"
PARTIAL = "partial"
VALID_RESULTS = {SOLVED, FAILED, PARTIAL}

# A partial solve counts as half credit — it is real progress, but not a solve.
_RESULT_CREDIT = {SOLVED: 1.0, PARTIAL: 0.5, FAILED: 0.0}

# Weakness score weights. Accuracy dominates, staleness matters, volume nudges.
WEIGHT_INACCURACY = 0.55
WEIGHT_STALENESS = 0.30
WEIGHT_LOW_VOLUME = 0.15

# A topic untouched for this long is considered maximally stale.
STALENESS_CEILING_DAYS = 30
# Attempts beyond this no longer reduce the "unknown topic" penalty.
VOLUME_CONFIDENCE_ATTEMPTS = 10


@dataclass
class PracticeAttempt:
    """One logged practice attempt."""

    topic: str
    result: str
    problem: str = ""
    difficulty: str = ""
    source: str = "manual"
    minutes: float = 0.0
    notes: str = ""
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TopicStats:
    """Aggregated performance for one topic."""

    topic: str
    attempts: int = 0
    solved: int = 0
    partial: int = 0
    failed: int = 0
    total_minutes: float = 0.0
    last_practiced: str = ""

    @property
    def raw_accuracy(self) -> float:
        if self.attempts == 0:
            return 0.0
        credit = self.solved + 0.5 * self.partial
        return credit / self.attempts

    @property
    def smoothed_accuracy(self) -> float:
        """Laplace-smoothed accuracy, so tiny samples aren't treated as certain."""
        credit = self.solved + 0.5 * self.partial
        return (credit + 1.0) / (self.attempts + 2.0)

    def days_since_practice(self, now: datetime | None = None) -> float:
        if not self.last_practiced:
            return float(STALENESS_CEILING_DAYS)
        try:
            last = datetime.fromisoformat(self.last_practiced)
        except ValueError:
            return float(STALENESS_CEILING_DAYS)
        delta = (now or datetime.now()) - last
        return max(0.0, delta.total_seconds() / 86400.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "attempts": self.attempts,
            "solved": self.solved,
            "partial": self.partial,
            "failed": self.failed,
            "total_minutes": round(self.total_minutes, 1),
            "accuracy": round(self.raw_accuracy, 3),
            "last_practiced": self.last_practiced,
        }


@dataclass
class WeakTopic:
    """A topic flagged for practice, with the reason it was flagged."""

    topic: str
    score: float
    reason: str
    stats: TopicStats = field(default=None)  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "score": round(self.score, 3),
            "reason": self.reason,
            "stats": self.stats.to_dict() if self.stats else {},
        }


class AcademicTracker:
    """Practice log plus weak-topic analysis."""

    def __init__(self, path: str = DEFAULT_TRACKER_PATH) -> None:
        self.path = path
        self._attempts: list[PracticeAttempt] = []
        self._loaded = False

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self._attempts = [PracticeAttempt(**row) for row in payload.get("attempts", [])]
            log.info("academic_progress_loaded", extra={"attempt_count": len(self._attempts)})
        except (OSError, ValueError, TypeError) as err:
            # A corrupt progress file must not crash the assistant — start
            # empty and say so, rather than losing the session.
            log.info("academic_progress_load_failed", extra={"error_type": type(err).__name__})
            self._attempts = []

    def _save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            payload = {"attempts": [a.to_dict() for a in self._attempts]}
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            return True
        except OSError as err:
            log.info("academic_progress_save_failed", extra={"error_type": type(err).__name__})
            return False

    # ── Logging ──────────────────────────────────────────────────────────

    def log_attempt(
        self,
        topic: str,
        result: str,
        problem: str = "",
        difficulty: str = "",
        source: str = "manual",
        minutes: float = 0.0,
        notes: str = "",
    ) -> PracticeAttempt | None:
        """Record one practice attempt. Returns None if the input is invalid."""
        self._load()

        topic = (topic or "").strip().lower()
        result = (result or "").strip().lower()

        if not topic:
            log.info("academic_log_rejected_no_topic", extra={})
            return None
        if result not in VALID_RESULTS:
            log.info("academic_log_rejected_bad_result", extra={"result": result})
            return None

        attempt = PracticeAttempt(
            topic=topic,
            result=result,
            problem=(problem or "").strip(),
            difficulty=(difficulty or "").strip().lower(),
            source=(source or "manual").strip().lower(),
            minutes=max(0.0, float(minutes or 0.0)),
            notes=(notes or "").strip(),
            timestamp=datetime.now().isoformat(),
        )
        self._attempts.append(attempt)
        self._save()
        log.info("academic_attempt_logged", extra={"topic": topic, "result": result})
        return attempt

    # ── Aggregation ──────────────────────────────────────────────────────

    def all_attempts(self) -> list[PracticeAttempt]:
        self._load()
        return list(self._attempts)

    def topics(self) -> list[str]:
        self._load()
        return sorted({a.topic for a in self._attempts})

    def topic_stats(self, topic: str) -> TopicStats:
        """Aggregate stats for one topic (empty stats if never practiced)."""
        self._load()
        topic = (topic or "").strip().lower()
        stats = TopicStats(topic=topic)

        for attempt in self._attempts:
            if attempt.topic != topic:
                continue
            stats.attempts += 1
            stats.total_minutes += attempt.minutes
            if attempt.result == SOLVED:
                stats.solved += 1
            elif attempt.result == PARTIAL:
                stats.partial += 1
            else:
                stats.failed += 1
            if attempt.timestamp > stats.last_practiced:
                stats.last_practiced = attempt.timestamp

        return stats

    def all_topic_stats(self) -> list[TopicStats]:
        return [self.topic_stats(topic) for topic in self.topics()]

    # ── Weakness analysis ────────────────────────────────────────────────

    def weakness_score(self, stats: TopicStats, now: datetime | None = None) -> tuple[float, str]:
        """Score how much a topic needs practice, plus the reason why.

        Returns (score in 0..1, human-readable reason).
        """
        inaccuracy = 1.0 - stats.smoothed_accuracy
        staleness = min(stats.days_since_practice(now), STALENESS_CEILING_DAYS) / STALENESS_CEILING_DAYS
        low_volume = max(0.0, 1.0 - (stats.attempts / VOLUME_CONFIDENCE_ATTEMPTS))

        score = (
            WEIGHT_INACCURACY * inaccuracy
            + WEIGHT_STALENESS * staleness
            + WEIGHT_LOW_VOLUME * low_volume
        )

        # Attribute the score to whichever factor contributed most, so the
        # recommendation can explain itself instead of just ranking.
        contributions = {
            "accuracy": WEIGHT_INACCURACY * inaccuracy,
            "staleness": WEIGHT_STALENESS * staleness,
            "volume": WEIGHT_LOW_VOLUME * low_volume,
        }
        dominant = max(contributions, key=contributions.get)

        days = int(stats.days_since_practice(now))
        if dominant == "accuracy":
            reason = f"accuracy is {round(stats.raw_accuracy * 100)}% over {stats.attempts} attempt(s)"
        elif dominant == "staleness":
            reason = f"not practiced in {days} day(s)"
        else:
            reason = f"only {stats.attempts} attempt(s) logged — too little data to trust"

        return score, reason

    def weak_topics(self, limit: int = 5, now: datetime | None = None) -> list[WeakTopic]:
        """Rank topics by how much they need practice."""
        self._load()
        scored: list[WeakTopic] = []
        for stats in self.all_topic_stats():
            score, reason = self.weakness_score(stats, now)
            scored.append(WeakTopic(topic=stats.topic, score=score, reason=reason, stats=stats))

        scored.sort(key=lambda w: w.score, reverse=True)
        return scored[:limit]

    def recommend(self, limit: int = 3, now: datetime | None = None) -> dict[str, Any]:
        """Suggest what to practice next, with reasons. Honest on cold start."""
        self._load()
        if not self._attempts:
            return {
                "has_data": False,
                "message": (
                    "No practice logged yet, so I can't tell you what's weak — that would "
                    "just be a guess. Log a few attempts and the weak topics become real."
                ),
                "recommendations": [],
            }

        weak = self.weak_topics(limit=limit, now=now)
        return {
            "has_data": True,
            "message": f"Based on {len(self._attempts)} logged attempt(s) across {len(self.topics())} topic(s).",
            "recommendations": [w.to_dict() for w in weak],
        }

    # ── Summary ──────────────────────────────────────────────────────────

    def summary(self, now: datetime | None = None) -> dict[str, Any]:
        """Overall practice summary."""
        self._load()
        if not self._attempts:
            return {"has_data": False, "total_attempts": 0, "topics": [], "recent_streak_days": 0}

        solved = sum(1 for a in self._attempts if a.result == SOLVED)
        partial = sum(1 for a in self._attempts if a.result == PARTIAL)

        return {
            "has_data": True,
            "total_attempts": len(self._attempts),
            "solved": solved,
            "partial": partial,
            "failed": len(self._attempts) - solved - partial,
            "overall_accuracy": round((solved + 0.5 * partial) / len(self._attempts), 3),
            "total_minutes": round(sum(a.minutes for a in self._attempts), 1),
            "topics": [s.to_dict() for s in self.all_topic_stats()],
            "recent_streak_days": self.practice_streak(now),
        }

    def practice_streak(self, now: datetime | None = None) -> int:
        """Consecutive days ending today (or yesterday) with logged practice."""
        self._load()
        if not self._attempts:
            return 0

        practice_days: set[Any] = set()
        for attempt in self._attempts:
            try:
                practice_days.add(datetime.fromisoformat(attempt.timestamp).date())
            except ValueError:
                continue
        if not practice_days:
            return 0

        today = (now or datetime.now()).date()
        # A streak still counts if today hasn't been practiced yet but
        # yesterday was — the day isn't over.
        cursor = today if today in practice_days else today - timedelta(days=1)
        if cursor not in practice_days:
            return 0

        streak = 0
        while cursor in practice_days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak


def format_recommendations(result: dict[str, Any]) -> str:
    """Render recommendations for the terminal."""
    if not result.get("has_data"):
        return result.get("message", "No practice data yet.")

    lines = [result["message"], "", "Focus next on:"]
    for index, rec in enumerate(result.get("recommendations", []), start=1):
        stats = rec.get("stats", {})
        lines.append(
            f"  {index}. {rec['topic']} — {rec['reason']} "
            f"({stats.get('solved', 0)}/{stats.get('attempts', 0)} solved)"
        )
    return "\n".join(lines)


def format_summary(result: dict[str, Any]) -> str:
    """Render the overall practice summary for the terminal."""
    if not result.get("has_data"):
        return "No practice logged yet."

    lines = [
        f"Logged {result['total_attempts']} attempt(s) — "
        f"{result['solved']} solved, {result['partial']} partial, {result['failed']} failed "
        f"({round(result['overall_accuracy'] * 100)}% accuracy).",
        f"Time practiced: {result['total_minutes']} minutes. "
        f"Current streak: {result['recent_streak_days']} day(s).",
        "",
        "By topic:",
    ]
    for topic in result.get("topics", []):
        lines.append(
            f"  {topic['topic']}: {topic['solved']}/{topic['attempts']} solved "
            f"({round(topic['accuracy'] * 100)}%)"
        )
    return "\n".join(lines)
