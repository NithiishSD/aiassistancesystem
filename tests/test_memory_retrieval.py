"""Tests for relevance-ranked memory retrieval (OpenSpec change: add-memory-reranker).

The collection and the scorer are stubbed, so these pin down the selection
logic: ordering, the result-count bound, the relevance gate, empty results,
and the degraded fallback when the reranker is unavailable.
"""

import pytest

import memory
import reranker


def _candidates(*rows):
    """rows: (text, distance)"""
    return [{"text": t, "metadata": {}, "id": f"id{i}", "distance": d} for i, (t, d) in enumerate(rows)]


@pytest.fixture
def stub_retrieve(monkeypatch):
    calls = {}

    def install(rows):
        def fake_retrieve(query, domain="personal", user_id=memory.DEFAULT_USER_ID,
                          content_type=None, top_k=5):
            calls["top_k"] = top_k
            return _candidates(*rows)[:top_k]
        monkeypatch.setattr(memory, "retrieve", fake_retrieve)
        return calls
    return install


def _stub_scores(monkeypatch, scores):
    monkeypatch.setattr(reranker, "score", lambda q, texts: scores[: len(texts)])


class TestRanking:
    def test_widens_candidate_pool(self, stub_retrieve, monkeypatch):
        calls = stub_retrieve([("a", 0.5)])
        _stub_scores(monkeypatch, [0.9])
        memory.retrieve_relevant("q", top_k=3)
        assert calls["top_k"] == memory.DEFAULT_CANDIDATE_K

    def test_orders_by_relevance_not_distance(self, stub_retrieve, monkeypatch):
        stub_retrieve([("semester fact", 0.9), ("college fact", 1.06)])
        _stub_scores(monkeypatch, [0.00002, 0.02])
        result = memory.retrieve_relevant("what college do I study at", top_k=3, min_score=0.0)
        assert result[0]["text"] == "college fact"

    def test_result_count_bounded(self, stub_retrieve, monkeypatch):
        stub_retrieve([(f"f{i}", 0.5) for i in range(6)])
        _stub_scores(monkeypatch, [0.9, 0.8, 0.7, 0.6, 0.5, 0.4])
        assert len(memory.retrieve_relevant("q", top_k=3)) == 3

    def test_score_key_added_and_existing_keys_kept(self, stub_retrieve, monkeypatch):
        stub_retrieve([("a", 0.5)])
        _stub_scores(monkeypatch, [0.9])
        item = memory.retrieve_relevant("q")[0]
        assert {"text", "metadata", "id", "distance", "score"} <= set(item)


class TestRelevanceGate:
    def test_below_threshold_excluded(self, stub_retrieve, monkeypatch):
        stub_retrieve([("relevant", 0.5), ("junk", 1.9)])
        _stub_scores(monkeypatch, [0.5, 0.00001])
        result = memory.retrieve_relevant("q", min_score=0.01)
        assert [i["text"] for i in result] == ["relevant"]

    def test_empty_when_nothing_relevant(self, stub_retrieve, monkeypatch):
        stub_retrieve([("junk1", 1.5), ("junk2", 1.9)])
        _stub_scores(monkeypatch, [0.00001, 0.00002])
        assert memory.retrieve_relevant("what is a binary search tree", min_score=0.01) == []

    def test_correct_fact_beyond_old_l2_cutoff_is_kept(self, stub_retrieve, monkeypatch):
        """The spec scenario: 1.06 distance used to be dropped by the 1.0 cutoff."""
        stub_retrieve([("User's college: PSG College of Technology", 1.06)])
        _stub_scores(monkeypatch, [0.02])
        result = memory.retrieve_relevant("what college do I study at", min_score=0.01)
        assert len(result) == 1

    def test_empty_store_returns_empty(self, stub_retrieve, monkeypatch):
        stub_retrieve([])
        _stub_scores(monkeypatch, [])
        assert memory.retrieve_relevant("q") == []


class TestDegradedFallback:
    def test_falls_back_to_distance_ranking(self, stub_retrieve, monkeypatch):
        stub_retrieve([("far", 1.2), ("near", 0.4), ("junk", 1.9)])
        monkeypatch.setattr(reranker, "score", lambda q, texts: None)
        result = memory.retrieve_relevant("q", top_k=3)
        assert [i["text"] for i in result] == ["near", "far"]

    def test_fallback_keeps_the_1_06_case(self, stub_retrieve, monkeypatch):
        stub_retrieve([("college", 1.06)])
        monkeypatch.setattr(reranker, "score", lambda q, texts: None)
        assert len(memory.retrieve_relevant("q")) == 1

    def test_fallback_never_raises_and_logs(self, stub_retrieve, monkeypatch):
        stub_retrieve([("a", 0.5)])
        monkeypatch.setattr(reranker, "score", lambda q, texts: None)
        events = []
        monkeypatch.setattr(memory.log, "info", lambda msg, extra=None: events.append(msg))
        memory.retrieve_relevant("q")
        assert "memory_retrieval_degraded" in events


class TestThirdPersonRewrite:
    """Design D8: facts are "User's X: Y", so first-person questions are
    rewritten before scoring."""

    @pytest.mark.parametrize("query,expected", [
        ("where do I live", "where does the user live"),
        ("what is my name", "what is the user's name"),
        ("which semester am I in", "which semester is the user in"),
        ("tell me about my goals", "tell the user about the user's goals"),
        ("I'm preparing for what exam", "the user is preparing for what exam"),
        ("what is a binary search tree", "what is a binary search tree"),
        ("  What   college do I study at ", "what college does the user study at"),
    ])
    def test_rewrite(self, query, expected):
        assert memory._third_person_query(query) == expected

    def test_words_containing_i_are_untouched(self):
        assert memory._third_person_query("list icons in image") == "list icons in image"

    def test_scorer_receives_rewritten_query(self, stub_retrieve, monkeypatch):
        stub_retrieve([("User's Location: Greenfield", 1.55)])
        seen = {}

        def fake_score(query, texts):
            seen["query"] = query
            return [0.9]

        monkeypatch.setattr(reranker, "score", fake_score)
        memory.retrieve_relevant("where do I live")
        assert seen["query"] == "where does the user live"
