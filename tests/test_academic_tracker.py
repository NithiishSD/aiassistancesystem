"""
Unit tests for academic / placement-prep tracking (Roadmap Item 14).

Beyond basic logging, these pin down the scoring behavior that makes the
recommendations trustworthy:
- Smoothed accuracy stops a single failure from permanently pinning a topic.
- Staleness surfaces topics that were strong but have gone cold.
- Cold start says "I don't know" instead of inventing weak topics.
- A corrupt progress file degrades to empty rather than crashing.
"""

import json
import os
from datetime import datetime, timedelta

import pytest

import academic_tracker
from academic_tracker import AcademicTracker, FAILED, PARTIAL, SOLVED


@pytest.fixture
def tracker(tmp_path):
    return AcademicTracker(path=str(tmp_path / "progress.json"))


class TestLogging:
    def test_log_valid_attempt(self, tracker):
        attempt = tracker.log_attempt("graphs", SOLVED, problem="BFS shortest path", minutes=25)
        assert attempt is not None
        assert attempt.topic == "graphs"
        assert len(tracker.all_attempts()) == 1

    def test_topic_is_normalized(self, tracker):
        tracker.log_attempt("  Dynamic Programming  ", SOLVED)
        assert tracker.topics() == ["dynamic programming"]

    def test_invalid_result_rejected(self, tracker):
        assert tracker.log_attempt("graphs", "kind of solved") is None
        assert tracker.all_attempts() == []

    def test_empty_topic_rejected(self, tracker):
        assert tracker.log_attempt("", SOLVED) is None

    def test_attempts_persist_across_instances(self, tracker, tmp_path):
        tracker.log_attempt("graphs", SOLVED)
        reopened = AcademicTracker(path=tracker.path)
        assert len(reopened.all_attempts()) == 1

    def test_negative_minutes_clamped(self, tracker):
        attempt = tracker.log_attempt("graphs", SOLVED, minutes=-10)
        assert attempt.minutes == 0.0

    def test_corrupt_file_degrades_to_empty(self, tmp_path):
        path = tmp_path / "progress.json"
        path.write_text("this is not json")
        tracker = AcademicTracker(path=str(path))
        assert tracker.all_attempts() == []


class TestTopicStats:
    def test_counts_by_result(self, tracker):
        tracker.log_attempt("graphs", SOLVED)
        tracker.log_attempt("graphs", FAILED)
        tracker.log_attempt("graphs", PARTIAL)

        stats = tracker.topic_stats("graphs")
        assert stats.attempts == 3
        assert stats.solved == 1
        assert stats.failed == 1
        assert stats.partial == 1

    def test_partial_counts_as_half(self, tracker):
        tracker.log_attempt("graphs", PARTIAL)
        tracker.log_attempt("graphs", PARTIAL)
        assert tracker.topic_stats("graphs").raw_accuracy == pytest.approx(0.5)

    def test_unknown_topic_returns_empty_stats(self, tracker):
        stats = tracker.topic_stats("topic never practiced")
        assert stats.attempts == 0
        assert stats.raw_accuracy == 0.0

    def test_total_minutes_accumulate(self, tracker):
        tracker.log_attempt("graphs", SOLVED, minutes=20)
        tracker.log_attempt("graphs", SOLVED, minutes=15.5)
        assert tracker.topic_stats("graphs").total_minutes == pytest.approx(35.5)

    def test_smoothing_softens_single_failure(self, tracker):
        """One failure should read as uncertain, not as hopeless."""
        tracker.log_attempt("graphs", FAILED)
        stats = tracker.topic_stats("graphs")
        assert stats.raw_accuracy == 0.0
        assert stats.smoothed_accuracy == pytest.approx(1 / 3)


class TestWeaknessScoring:
    def test_low_accuracy_topic_ranks_above_strong_topic(self, tracker):
        for _ in range(5):
            tracker.log_attempt("graphs", SOLVED)
        for _ in range(5):
            tracker.log_attempt("dp", FAILED)

        weak = tracker.weak_topics(limit=2)
        assert weak[0].topic == "dp"

    def test_reason_explains_accuracy_ranking(self, tracker):
        for _ in range(6):
            tracker.log_attempt("dp", FAILED)
        weak = tracker.weak_topics(limit=1)
        assert "accuracy" in weak[0].reason

    def test_stale_topic_is_surfaced(self, tracker):
        """A topic solved perfectly but long ago should still get flagged."""
        for _ in range(8):
            tracker.log_attempt("arrays", SOLVED)

        future = datetime.now() + timedelta(days=45)
        weak = tracker.weak_topics(limit=1, now=future)
        assert weak[0].topic == "arrays"
        assert "not practiced" in weak[0].reason

    def test_recently_practiced_strong_topic_scores_low(self, tracker):
        for _ in range(10):
            tracker.log_attempt("arrays", SOLVED)
        weak = tracker.weak_topics(limit=1)
        assert weak[0].score < 0.3

    def test_low_volume_topic_flagged_for_volume(self, tracker):
        tracker.log_attempt("tries", SOLVED)
        weak = tracker.weak_topics(limit=1)
        assert weak[0].topic == "tries"

    def test_scores_stay_in_unit_range(self, tracker):
        tracker.log_attempt("a", FAILED)
        tracker.log_attempt("b", SOLVED)
        for weak in tracker.weak_topics(limit=10):
            assert 0.0 <= weak.score <= 1.0

    def test_limit_is_respected(self, tracker):
        for topic in ["a", "b", "c", "d", "e"]:
            tracker.log_attempt(topic, FAILED)
        assert len(tracker.weak_topics(limit=2)) == 2


class TestRecommendations:
    def test_cold_start_admits_no_data(self, tracker):
        result = tracker.recommend()
        assert result["has_data"] is False
        assert result["recommendations"] == []
        assert "guess" in result["message"].lower()

    def test_recommendations_include_reasons(self, tracker):
        for _ in range(4):
            tracker.log_attempt("dp", FAILED)
        result = tracker.recommend(limit=1)
        assert result["has_data"] is True
        assert result["recommendations"][0]["reason"]

    def test_format_recommendations_cold_start(self, tracker):
        rendered = academic_tracker.format_recommendations(tracker.recommend())
        assert "no practice logged" in rendered.lower()

    def test_format_recommendations_with_data(self, tracker):
        tracker.log_attempt("dp", FAILED)
        rendered = academic_tracker.format_recommendations(tracker.recommend())
        assert "dp" in rendered
        assert "Focus next on" in rendered


class TestSummary:
    def test_empty_summary(self, tracker):
        summary = tracker.summary()
        assert summary["has_data"] is False
        assert summary["total_attempts"] == 0

    def test_summary_totals(self, tracker):
        tracker.log_attempt("graphs", SOLVED, minutes=10)
        tracker.log_attempt("graphs", FAILED, minutes=5)
        tracker.log_attempt("dp", PARTIAL, minutes=8)

        summary = tracker.summary()
        assert summary["total_attempts"] == 3
        assert summary["solved"] == 1
        assert summary["partial"] == 1
        assert summary["failed"] == 1
        assert summary["total_minutes"] == pytest.approx(23.0)

    def test_overall_accuracy_counts_partial_as_half(self, tracker):
        tracker.log_attempt("graphs", SOLVED)
        tracker.log_attempt("graphs", PARTIAL)
        assert tracker.summary()["overall_accuracy"] == pytest.approx(0.75)

    def test_format_summary_lists_topics(self, tracker):
        tracker.log_attempt("graphs", SOLVED)
        rendered = academic_tracker.format_summary(tracker.summary())
        assert "graphs" in rendered
        assert "accuracy" in rendered.lower()


class TestStreak:
    def test_no_attempts_no_streak(self, tracker):
        assert tracker.practice_streak() == 0

    def test_single_day_streak(self, tracker):
        tracker.log_attempt("graphs", SOLVED)
        assert tracker.practice_streak() == 1

    def test_consecutive_days_counted(self, tracker):
        base = datetime.now()
        for days_ago in range(3):
            tracker._load()
            tracker._attempts.append(academic_tracker.PracticeAttempt(
                topic="graphs", result=SOLVED,
                timestamp=(base - timedelta(days=days_ago)).isoformat(),
            ))
        assert tracker.practice_streak(now=base) == 3

    def test_gap_breaks_streak(self, tracker):
        base = datetime.now()
        tracker._load()
        for days_ago in [0, 1, 5]:
            tracker._attempts.append(academic_tracker.PracticeAttempt(
                topic="graphs", result=SOLVED,
                timestamp=(base - timedelta(days=days_ago)).isoformat(),
            ))
        assert tracker.practice_streak(now=base) == 2

    def test_stale_practice_yields_zero_streak(self, tracker):
        base = datetime.now()
        tracker._load()
        tracker._attempts.append(academic_tracker.PracticeAttempt(
            topic="graphs", result=SOLVED,
            timestamp=(base - timedelta(days=10)).isoformat(),
        ))
        assert tracker.practice_streak(now=base) == 0

    def test_yesterday_only_still_counts(self, tracker):
        """The current day isn't over, so yesterday's practice keeps the streak."""
        base = datetime.now()
        tracker._load()
        tracker._attempts.append(academic_tracker.PracticeAttempt(
            topic="graphs", result=SOLVED,
            timestamp=(base - timedelta(days=1)).isoformat(),
        ))
        assert tracker.practice_streak(now=base) == 1


class TestOrchestratorIntegration:
    def test_log_action_records_attempt(self, tmp_path):
        import orchestrator
        from unittest.mock import patch

        tracker = AcademicTracker(path=str(tmp_path / "progress.json"))
        extracted = {"action": "log", "topic": "graphs", "result": "solved",
                     "problem": "BFS", "difficulty": "medium", "minutes": 20}

        with patch.object(orchestrator, "ACADEMIC_TRACKER", tracker), \
             patch("orchestrator._extract_academic_intent", return_value=extracted):
            out = orchestrator.execute({
                "function": "academic_tracking", "domain": "academic",
                "confidence": "high", "_original_input": "I solved a BFS problem",
            })

        assert len(tracker.all_attempts()) == 1
        assert "graphs" in out

    def test_unparseable_log_does_not_write_junk(self, tmp_path):
        import orchestrator
        from unittest.mock import patch

        tracker = AcademicTracker(path=str(tmp_path / "progress.json"))
        extracted = {"action": "log", "topic": "", "result": ""}

        with patch.object(orchestrator, "ACADEMIC_TRACKER", tracker), \
             patch("orchestrator._extract_academic_intent", return_value=extracted):
            out = orchestrator.execute({
                "function": "academic_tracking", "domain": "academic",
                "confidence": "high", "_original_input": "mumble mumble",
            })

        assert tracker.all_attempts() == []
        assert "couldn't tell" in out.lower()

    def test_review_action_returns_recommendations(self, tmp_path):
        import orchestrator
        from unittest.mock import patch

        tracker = AcademicTracker(path=str(tmp_path / "progress.json"))
        tracker.log_attempt("dp", FAILED)

        with patch.object(orchestrator, "ACADEMIC_TRACKER", tracker), \
             patch("orchestrator._extract_academic_intent", return_value={"action": "review"}):
            out = orchestrator.execute({
                "function": "academic_tracking", "domain": "academic",
                "confidence": "high", "_original_input": "what are my weak topics",
            })

        assert "dp" in out

    def test_extraction_failure_defaults_to_review(self):
        import orchestrator
        from unittest.mock import patch

        with patch("orchestrator.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            result = orchestrator._extract_academic_intent("anything at all")

        assert result["action"] == "review"
