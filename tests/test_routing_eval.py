"""Routing evaluation: golden-set integrity, harness behaviour, regression gate,
and the must-stay-local list (OpenSpec change: add-routing-eval, ROADMAP A4).

The gate compares today's Layer-1 routing on the held-out test slice against
evals/routing_baseline.json. To accept a deliberate change in routing quality,
regenerate the baseline: python evals/routing_eval.py --write-baseline
"""

import copy
import hashlib
import json
import os
import re
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

import classifier as clf  # noqa: E402
import routing_eval as rev  # noqa: E402


def _norm(text):
    return re.sub(r"\s+", " ", text.strip().lower())


@pytest.fixture(scope="module")
def static_router():
    return rev.build_static_router()


@pytest.fixture(scope="module")
def golden():
    return rev.load_golden()


@pytest.fixture(scope="module")
def test_slice_result(static_router):
    return rev.evaluate(rev.load_golden(split="test"), static_router)


# ── 2. Golden set integrity ─────────────────────────────────────────────────

class TestGoldenSet:
    def test_columns_and_labels(self, golden):
        assert set(golden[0]) == {"utterance", "intent", "split"}
        allowed = set(rev.classes())
        assert {r["intent"] for r in golden} <= allowed
        assert {r["split"] for r in golden} <= {"dev", "test"}

    def test_every_class_has_twenty_and_both_splits(self, golden):
        for label in rev.classes():
            rows = [r for r in golden if r["intent"] == label]
            assert len(rows) >= 20, f"{label} has {len(rows)} rows"
            assert {r["split"] for r in rows} == {"dev", "test"}, f"{label} missing a split"

    def test_no_leakage_from_classifier_phrases(self, golden):
        static = {_norm(p) for phrases in clf.INTENT_UTTERANCES.values() for p in phrases}
        leaked = [r["utterance"] for r in golden if _norm(r["utterance"]) in static]
        assert not leaked, f"golden rows duplicate classifier phrases: {leaked}"

    def test_no_duplicate_rows(self, golden):
        seen = [_norm(r["utterance"]) for r in golden]
        assert len(seen) == len(set(seen))


# ── 3. Harness ──────────────────────────────────────────────────────────────

class TestHarnessLogic:
    """Stubbed Layer 1, so these pin the scoring rules, not the model."""

    def _stub(self, mapping):
        return patch.object(clf, "_layer1_route_with",
                            side_effect=lambda router, text: mapping[text])

    def test_general_question_escalating_counts_as_correct(self):
        rows = [{"utterance": "what is recursion", "intent": "general_question", "split": "test"}]
        with self._stub({"what is recursion": (None, 0.3)}):
            result = rev.evaluate(rows, router=None)
        assert result["per_class"]["general_question"]["recall"] == 1.0
        assert result["accuracy"] == 1.0

    def test_general_question_claimed_by_intent_is_an_error(self):
        rows = [{"utterance": "what is a binary tree", "intent": "general_question", "split": "test"}]
        with self._stub({"what is a binary tree": ("coding_task", 0.71)}):
            result = rev.evaluate(rows, router=None)
        assert result["per_class"]["general_question"]["recall"] == 0.0
        assert result["errors"][0]["got"] == "coding_task"

    def test_escalation_is_a_miss_for_real_intents_and_visible_in_matrix(self):
        rows = [{"utterance": "code me a game", "intent": "coding_task", "split": "test"}]
        with self._stub({"code me a game": (None, 0.42)}):
            result = rev.evaluate(rows, router=None)
        assert result["per_class"]["coding_task"]["recall"] == 0.0
        labels = result["confusion_labels"]
        assert labels[-1] == rev.ESCALATE
        assert result["confusion_matrix"][labels.index("coding_task")][-1] == 1
        assert result["escalation_rate"] == 1.0

    def test_classify_offline_never_calls_llm(self):
        with self._stub({"anything at all here": (None, 0.1)}), \
             patch.object(clf, "query_llm_with_tools", side_effect=AssertionError("LLM called")):
            assert rev.classify_offline(None, "anything at all here") == (rev.ESCALATE, 0.1)


class TestHarnessOnRealRouter:
    def test_score_path_matches_public_decision_for_every_golden_row(self, static_router, golden):
        """Design D1 guard: the real score always belongs to the route the
        router actually chose. Fails on semantic-router internals drift."""
        unverified = []
        for row in golden:
            decided = static_router(row["utterance"]).name
            _, verified = clf._layer1_score(static_router, row["utterance"], decided)
            if not verified:
                unverified.append(row["utterance"])
        assert not unverified, f"score path disagrees with router decision: {unverified[:5]}"

    def test_runtime_state_untouched(self, static_router, golden):
        path = clf.DYNAMIC_UTTERANCES_PATH
        before_file = hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None
        before_router = clf._intent_router
        before_cache = copy.deepcopy(clf._ROUTING_CACHE)

        with patch.object(clf, "query_llm_with_tools", side_effect=AssertionError("LLM called")):
            rev.evaluate(golden, static_router)

        after_file = hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.exists(path) else None
        assert after_file == before_file
        assert clf._intent_router is before_router
        assert clf._ROUTING_CACHE == before_cache


# ── 4. Regression gate ──────────────────────────────────────────────────────

class TestRegressionGate:
    def test_baseline_matches_current_golden_set(self):
        with open(rev.BASELINE_PATH, encoding="utf-8") as handle:
            baseline = json.load(handle)
        assert baseline["meta"]["golden_sha256"] == rev.golden_sha256(), (
            "golden set changed; regenerate the baseline with "
            "`python evals/routing_eval.py --write-baseline`")

    def test_no_class_regressed_beyond_tolerance(self, test_slice_result):
        with open(rev.BASELINE_PATH, encoding="utf-8") as handle:
            baseline = json.load(handle)
        failures = rev.compare_to_baseline(test_slice_result, baseline)
        assert not failures, "routing regressed:\n" + "\n".join(failures)

    def test_gate_catches_a_doctored_regression(self, test_slice_result):
        with open(rev.BASELINE_PATH, encoding="utf-8") as handle:
            baseline = json.load(handle)
        doctored = copy.deepcopy(baseline)
        doctored["per_class"]["free_space_summary"]["recall"] += 0.10
        failures = rev.compare_to_baseline(test_slice_result, doctored)
        assert any(f.startswith("free_space_summary: recall") for f in failures)

    def test_improvement_passes(self, test_slice_result):
        with open(rev.BASELINE_PATH, encoding="utf-8") as handle:
            baseline = json.load(handle)
        lowered = copy.deepcopy(baseline)
        lowered["per_class"]["general_question"]["precision"] = 0.0
        assert rev.compare_to_baseline(test_slice_result, lowered) == []


# ── 5. Must stay local ──────────────────────────────────────────────────────

class TestMustStayLocal:
    def test_file_loads_with_core_intents_only(self):
        rows = rev.load_must_stay_local()
        assert rows
        assert {r["intent"] for r in rows} <= set(rev.CORE_LOCAL_INTENTS)

    def test_every_row_resolved_locally(self, static_router):
        failures = rev.must_stay_local_failures(rev.load_must_stay_local(), static_router)
        assert not failures, "must-stay-local rows left Layer 1:\n" + "\n".join(failures)

    def test_escalation_is_reported_with_the_row(self):
        rows = [{"utterance": "how big is my downloads folder", "intent": "directory_size"}]
        with patch.object(clf, "_layer1_route_with", return_value=(None, 0.4)):
            failures = rev.must_stay_local_failures(rows, router=None)
        assert failures and "how big is my downloads folder" in failures[0]
        assert "ESCALATE" in failures[0]
