"""Layer-1 coverage: short phrases, general-question anchor, split thresholds
(OpenSpec change: improve-layer1-coverage)."""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

import classifier as clf  # noqa: E402
import routing_eval as rev  # noqa: E402


@pytest.fixture(scope="module")
def static_router():
    return clf._build_intent_router(include_dynamic=False)


class TestThresholds:
    def test_intent_routes_and_anchor_use_intent_threshold(self, static_router):
        for route in static_router.routes:
            expected = clf.STRICT_INTENT_THRESHOLDS.get(route.name, clf.ROUTE_THRESHOLD)
            assert route.score_threshold == expected, route.name
        assert clf.ROUTE_THRESHOLD == 0.55
        assert clf.CONFIDENCE_THRESHOLD == clf.ROUTE_THRESHOLD

    def test_costly_intents_stay_strict(self, static_router):
        by_name = {r.name: r.score_threshold for r in static_router.routes}
        assert by_name["open_application"] == 0.65
        assert by_name["unsupported"] == 0.65
        assert by_name[clf.DEFAULT_INTENT] == clf.ROUTE_THRESHOLD

    def test_calibrated_intents(self, static_router):
        """ROADMAP A5: lowered only where the DEV slice showed gain with no wrong
        match nearby, and the held-out gate passed (evals/calibrate_thresholds.py)."""
        by_name = {r.name: r.score_threshold for r in static_router.routes}
        assert {n: t for n, t in by_name.items() if t < clf.ROUTE_THRESHOLD} == {
            "academic_tracking": 0.50, "research_task": 0.45, "search_files": 0.45, "web_task": 0.45}
        # Intents whose wrong match acts on its own are never lowered.
        for name in ("remember_fact", "correct_fact"):
            assert by_name[name] == clf.ROUTE_THRESHOLD

    def test_classify_intent_honours_the_per_intent_threshold(self, monkeypatch):
        monkeypatch.setattr(clf, "_ROUTING_CACHE", {})
        monkeypatch.setattr(clf, "query_llm_with_tools",
                            lambda text: {"function": "LLM", "confidence": "high", "score": 1.0, "via_llm": True})
        monkeypatch.setattr(clf, "_layer1_route_with", lambda router, text: ("research_task", 0.47))
        assert clf.classify_intent("zz calibrated probe one")["function"] == "research_task"
        monkeypatch.setattr(clf, "_layer1_route_with", lambda router, text: ("coding_task", 0.47))
        assert clf.classify_intent("zz calibrated probe two")["function"] == "LLM"
        monkeypatch.setattr(clf, "_layer1_route_with", lambda router, text: ("open_application", 0.60))
        assert clf.classify_intent("zz calibrated probe three")["function"] == "LLM"
        assert clf.intent_threshold(None) == clf.intent_threshold("coding_task") == clf.ROUTE_THRESHOLD

    def test_calibration_never_recommends_below_the_floor_or_into_a_wrong_match(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))
        import calibrate_thresholds as cal

        assert cal.recommend([(0.50, True), (0.30, True)], 0.55) == cal.FLOOR          # clean: floor, not 0.30
        assert cal.recommend([(0.50, True), (0.46, False)], 0.55) == 0.55             # wrong match within margin
        assert cal.recommend([(0.52, True), (0.41, False)], 0.55) == 0.50             # 0.41 + 0.05, rounded up
        assert cal.recommend([(0.50, True), (0.70, False)], 0.55) == 0.55             # already wrong above: leave
        assert cal.recommend([(0.60, True)], 0.55) == 0.55                            # nothing to gain

    def test_domain_router_keeps_its_own_threshold(self):
        domain = clf._build_domain_router()
        assert {r.score_threshold for r in domain.routes} == {clf.DOMAIN_ROUTE_THRESHOLD}
        assert clf.DOMAIN_ROUTE_THRESHOLD == 0.65


class TestGeneralAnchor:
    def test_anchor_route_present_but_not_a_routable_intent(self, static_router):
        assert any(r.name == clf.DEFAULT_INTENT for r in static_router.routes)
        assert clf.DEFAULT_INTENT not in clf.INTENT_UTTERANCES

    def test_concept_question_not_turned_into_memory_action(self, static_router):
        name, _ = clf._layer1_route_with(static_router, "explain what ram is")
        assert name != "top_memory_processes"

    def test_anchored_question_escalates_like_today(self):
        # An anchor phrase is Layer 1's best match by construction.
        text = clf.GENERAL_ANCHOR_UTTERANCES[0]
        clf._ROUTING_CACHE.pop(text.strip().lower(), None)
        llm_decision = {"function": None, "confidence": "high", "score": 1.0, "via_llm": True}
        with patch.object(clf, "query_llm_with_tools", return_value=llm_decision) as mock_llm:
            result = clf.classify_intent(text)
        mock_llm.assert_called_once_with(text)
        assert result["via_llm"] is True

    def test_real_action_still_resolves_locally(self):
        text = "which folders take up the most space"
        clf._ROUTING_CACHE.pop(text, None)
        with patch.object(clf, "query_llm_with_tools") as mock_llm:
            result = clf.classify_intent(text)
        mock_llm.assert_not_called()
        assert result["function"] == "disk_usage_by_folder"


class TestNearDuplicateGuard:
    """Design D3: no router phrase may be a near-copy of a held-out test row."""

    def _router_phrases(self):
        return [p for phrases in clf.INTENT_UTTERANCES.values() for p in phrases] + list(
            clf.GENERAL_ANCHOR_UTTERANCES)

    def test_no_router_phrase_near_copies_a_test_row(self):
        test_rows = rev.load_golden(split="test")
        found = rev.near_duplicates(self._router_phrases(), test_rows)
        assert not found, f"router phrases too close to held-out test rows: {found}"

    def test_guard_detects_a_copied_test_row(self):
        test_rows = rev.load_golden(split="test")
        copied = test_rows[0]["utterance"] + " please"
        found = rev.near_duplicates(self._router_phrases() + [copied], test_rows)
        assert any(phrase == copied for phrase, _, _ in found)
