"""The daily digest (OpenSpec change: proactive-digest). Temp files only."""

from datetime import datetime, timedelta

import pytest

import digest
from academic_tracker import AcademicTracker

NOW = datetime(2026, 9, 30, 9, 0)


@pytest.fixture
def tracker(tmp_path):
    return AcademicTracker(path=str(tmp_path / "progress.json"))


def _log(tracker, topic, result, days_ago):
    tracker.log_attempt(topic=topic, result=result)
    tracker._attempts[-1].timestamp = (NOW - timedelta(days=days_ago)).isoformat()
    tracker._save()


def _fact(text, days_ago=0):
    return {"text": text, "metadata": {"timestamp": (NOW - timedelta(days=days_ago)).timestamp()}, "id": text}


class TestPractice:
    def test_stale_topic_with_tracker_figures(self, tracker):
        for result in ("solved", "solved", "failed", "failed", "failed"):
            _log(tracker, "graphs", result, days_ago=12)
        _log(tracker, "arrays", "solved", days_ago=0)
        text = digest.build_digest(tracker, NOW, facts_fn=list)
        assert "graphs: not practised in 12 days (40% over 5 attempt(s))" in text
        assert "Practice streak: 1 day." in text
        assert text.startswith("Daily digest — Wednesday 30 September")

    def test_weak_topic_not_listed_twice(self, tracker):
        _log(tracker, "graphs", "failed", days_ago=12)
        assert digest.build_digest(tracker, NOW, facts_fn=list).count("graphs") == 1

    def test_no_practice_data_invents_nothing(self, tracker):
        text = digest.build_digest(tracker, NOW, facts_fn=lambda: [_fact("User's OS exam: next week")])
        assert "Practice" not in text and "exam" in text


class TestCommitments:
    def test_commitment_with_age(self, tracker):
        facts = [_fact("User's data structures exam: next week", 5), _fact("User's hobby: cricket", 1),
                 _fact("User's project deadline: Friday", 0), _fact("User's contestant number: 4", 1)]
        text = digest.build_digest(tracker, NOW, facts_fn=lambda: facts)
        assert "- User's data structures exam: next week (noted 5 days ago)" in text
        assert "- User's project deadline: Friday (noted today)" in text
        assert "cricket" not in text and "contestant" not in text
        assert text.index("deadline") < text.index("exam")  # newest first

    def test_unknown_date(self):
        assert digest.commitment_lines([{"text": "User's viva: soon", "metadata": {}}], NOW) == [
            "- User's viva: soon (noted at an unknown date)"]

    def test_capped(self, tracker):
        facts = [_fact(f"User's quiz {n}: soon", n) for n in range(20)]
        assert len(digest.commitment_lines(facts, NOW)) == digest.MAX_COMMITMENTS

    def test_superseded_commitment_is_not_listed(self, tracker, monkeypatch):
        import hashlib
        import uuid

        import chromadb

        import memory

        class Embed:
            def __call__(self, input):
                return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:16]] for t in input]

        col = chromadb.EphemeralClient().get_or_create_collection(f"dg_{uuid.uuid4().hex[:8]}", embedding_function=Embed())
        monkeypatch.setattr(memory, "_get_collection", lambda domain: col)
        old = memory.store("User's OS exam: next week")
        memory.store("User's DBMS exam: Monday")
        memory.invalidate([old])
        text = digest.build_digest(tracker, NOW)
        assert "DBMS exam" in text and "OS exam" not in text


def test_nothing_to_report_is_empty(tracker):
    assert digest.build_digest(tracker, NOW, facts_fn=list) == ""
    assert digest.build_digest(tracker, NOW, facts_fn=lambda: [_fact("User's hobby: cricket")]) == ""
