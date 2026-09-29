"""Truthful Layer-1 score and the static router build (OpenSpec change: add-routing-eval)."""

import json
from unittest.mock import MagicMock, patch

import pytest

import classifier as clf


@pytest.fixture(scope="module")
def static_router():
    return clf._build_intent_router(include_dynamic=False)


class TestStaticRouterBuild:
    def test_static_build_ignores_learned_phrases(self, tmp_path, monkeypatch):
        learned = "zorblax the quantum flux capacitor please"
        path = tmp_path / "dynamic_utterances.json"
        path.write_text(json.dumps({"search_files": [learned]}))
        monkeypatch.setattr(clf, "DYNAMIC_UTTERANCES_PATH", str(path))

        def phrases(router):
            return {u for route in router.routes for u in route.utterances}

        assert learned in phrases(clf._build_intent_router())
        assert learned not in phrases(clf._build_intent_router(include_dynamic=False))


class TestRealScore:
    def test_clear_command_scores_above_threshold(self, static_router):
        name, score = clf._layer1_route_with(static_router, "how much free disk space do I have")
        assert name == "free_space_summary"
        assert score > clf.ROUTE_THRESHOLD

    def test_score_is_not_the_old_constant(self, static_router):
        _, score = clf._layer1_route_with(static_router, "how much free disk space do I have")
        assert score != clf.ROUTE_THRESHOLD

    def test_nonsense_escalates_without_error(self, static_router):
        name, score = clf._layer1_route_with(static_router, "qwpoeiru zmxncb")
        assert name is None
        assert 0.0 <= score <= clf.ROUTE_THRESHOLD

    def test_same_intent_different_scores(self, static_router):
        a = clf._layer1_route_with(static_router, "is my hard drive nearly full")
        b = clf._layer1_route_with(static_router, "check free storage space left")
        assert a[0] == b[0] == "free_space_summary"
        assert a[1] != b[1]

    def test_score_verified_against_public_decision(self, static_router):
        score, verified = clf._layer1_score(static_router, "find my resume file", "search_files")
        assert verified is True

    def test_mock_router_falls_back_without_raising(self):
        mock_result = MagicMock()
        mock_result.name = None
        router = MagicMock(return_value=mock_result)
        name, score = clf._layer1_route_with(router, "anything")
        assert name is None
        assert score == 0.0

    def test_mismatch_falls_back_to_previous_constant(self, static_router):
        score, verified = clf._layer1_score(static_router, "how much free disk space do I have", "web_task")
        assert verified is False
        assert score == clf.ROUTE_THRESHOLD


class TestClassifyIntentReportsRealScore:
    def test_layer1_hit_reports_real_score(self):
        clf._ROUTING_CACHE.pop("how much free disk space do i have", None)
        with patch("classifier.query_llm_with_tools") as mock_llm:
            result = clf.classify_intent("how much free disk space do I have")
        mock_llm.assert_not_called()
        assert result["function"] == "free_space_summary"
        assert result["score"] > clf.ROUTE_THRESHOLD
