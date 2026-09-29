"""Tests for the cross-encoder relevance scorer (OpenSpec change: add-memory-reranker)."""

import os

import pytest

import reranker


@pytest.fixture(autouse=True)
def _fresh_reranker():
    reranker._reset_for_tests()
    yield
    reranker._reset_for_tests()


class _FakeModel:
    def __init__(self, logits):
        self.logits = logits
        self.calls = 0

    def predict(self, pairs):
        self.calls += 1
        return self.logits[: len(pairs)]


class TestScoreContract:
    def test_empty_texts_returns_empty_list(self):
        assert reranker.score("anything", []) == []

    def test_missing_model_returns_none_without_raising(self, monkeypatch, tmp_path):
        monkeypatch.setattr(reranker, "MODEL_DIR", str(tmp_path / "absent"))
        assert reranker.score("q", ["a fact"]) is None
        assert reranker.is_available() is False

    def test_load_failure_is_remembered(self, monkeypatch, tmp_path):
        monkeypatch.setattr(reranker, "MODEL_DIR", str(tmp_path / "absent"))
        reranker.score("q", ["a"])
        # Even if the directory appears later, a failed load isn't retried per call.
        assert reranker._load_failed is True

    def test_scores_are_in_unit_interval_and_ordered_like_logits(self, monkeypatch):
        fake = _FakeModel([5.0, -3.9, -11.1])
        monkeypatch.setattr(reranker, "_model", fake)
        scores = reranker.score("q", ["a", "b", "c"])
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert scores[0] > scores[1] > scores[2]

    def test_model_loaded_once_across_calls(self, monkeypatch):
        loads = []

        def fake_load():
            if reranker._model is None:
                loads.append(1)
                reranker._model = _FakeModel([1.0, 1.0])
            return reranker._model

        monkeypatch.setattr(reranker, "_load_model", fake_load)
        reranker.score("q", ["a"])
        reranker.score("q", ["b"])
        assert len(loads) == 1

    def test_predict_exception_returns_none(self, monkeypatch):
        class Boom:
            def predict(self, pairs):
                raise RuntimeError("bad input")

        monkeypatch.setattr(reranker, "_model", Boom())
        assert reranker.score("q", ["a"]) is None

    def test_sigmoid_extremes_do_not_overflow(self):
        assert reranker._sigmoid(1000.0) == pytest.approx(1.0)
        assert reranker._sigmoid(-1000.0) == pytest.approx(0.0)


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(reranker.MODEL_DIR, "config.json")),
    reason="reranker model not installed (run setup.sh step 7)",
)
class TestRealModel:
    def test_relevant_fact_outscores_irrelevant(self):
        scores = reranker.score(
            "what college do I study at",
            ["User's college: PSG College of Technology", "User's Current Semester: 5th semester"],
        )
        assert scores[0] > scores[1]
