"""Tests for the Hybrid Cascading Router with Dynamic Online Learning.

All LLM calls are mocked so these tests run fully offline and fast.
Tests cover:
  - Layer 1 local hit (no LLM involved)
  - Layer 2 LLM escalation for novel phrasing
  - Dynamic utterance persistence (≤ 15 words)
  - Word-count quality guard (> 15 words → not saved)
  - general_question from LLM is never persisted
  - add_utterance_dynamically deduplication
  - In-memory router hot-reload after new utterance
"""

import json
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_llm_response(intent_name: str) -> dict:
    """Build a fake llm_provider.generate_chat() return value."""
    return {
        "answer": json.dumps({"function_name": intent_name, "arguments": {}}),
        "source": "mock",
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Redirect DYNAMIC_UTTERANCES_PATH to a temp dir for each test."""
    import classifier as clf
    fake_path = str(tmp_path / "dynamic_utterances.json")
    monkeypatch.setattr(clf, "DYNAMIC_UTTERANCES_PATH", fake_path)
    yield fake_path
    # Cleanup is handled by tmp_path fixture automatically


# ── Layer 1: local high-confidence hits ───────────────────────────────────────

class TestLayer1LocalHits:
    """Verify well-known phrases resolve via semantic-router without the LLM."""

    @pytest.mark.parametrize("phrase, expected_intent", [
        ("how much free disk space do I have",  "free_space_summary"),
        ("find my resume file",                  "search_files"),
        ("show the processes using the most RAM","top_memory_processes"),
        ("help me fix this Python code",         "coding_task"),
        ("can you open Brave application",       "open_application"),
        ("play some music on Spotify",           "unsupported"),
        ("okay thank you",                       None),  # acknowledgement guard
    ])
    def test_layer1_no_llm_call(self, phrase, expected_intent):
        """Layer 1 hit: classify_intent must NOT call llm_provider."""
        with patch("classifier.query_llm_with_tools") as mock_llm:
            import classifier as clf
            result = clf.classify_intent(phrase)

        assert result["function"] == expected_intent
        assert result["via_llm"] is False
        mock_llm.assert_not_called()

    def test_layer1_returns_high_confidence(self):
        with patch("classifier.query_llm_with_tools"):
            import classifier as clf
            result = clf.classify_intent("find all files with extension .cpp")

        assert result["confidence"] == "high"
        assert result["via_llm"] is False


# ── Layer 2: LLM escalation ───────────────────────────────────────────────────

class TestLayer2LLMEscalation:
    """Novel/slang phrases with low cosine similarity should escalate to LLM."""

    def _no_match_router(self):
        """Return a callable that mimics a zero-score semantic-router result."""
        mock_result = MagicMock()
        mock_result.name = None  # explicit attribute assignment, not MagicMock name=
        mock_router = MagicMock(return_value=mock_result)
        return mock_router

    def test_novel_phrase_triggers_llm(self):
        """A phrase not in any utterance list should escalate to LLM."""
        novel = "show me memory pigs running on CPU"

        import classifier as clf
        with patch.object(clf, "_intent_router", self._no_match_router()), \
             patch.object(clf, "query_llm_with_tools",
                          return_value={
                              "function": "top_memory_processes",
                              "confidence": "high",
                              "score": 1.0,
                              "via_llm": True,
                              "llm_args": {},
                          }) as mock_llm:
            result = clf.classify_intent(novel)

        assert result["function"] == "top_memory_processes"
        assert result["via_llm"] is True
        assert result["confidence"] == "high"
        mock_llm.assert_called_once_with(novel)

    def test_llm_escalation_on_no_layer1_match(self):
        """If semantic-router returns no route (score=0), LLM is called."""
        phrase = "yo how much juice is left on the disk"

        import classifier as clf
        with patch.object(clf, "_intent_router", self._no_match_router()), \
             patch.object(clf, "query_llm_with_tools",
                          return_value={
                              "function": "free_space_summary",
                              "confidence": "high",
                              "score": 1.0,
                              "via_llm": True,
                              "llm_args": {},
                          }) as mock_llm:
            result = clf.classify_intent(phrase)

        assert result["function"] == "free_space_summary"
        assert result["via_llm"] is True
        mock_llm.assert_called_once_with(phrase)

    def test_llm_failure_returns_general_question(self):
        """If the LLM call fails, fall back to general_question (function=None)."""
        import classifier as clf
        with patch.object(clf, "_intent_router", self._no_match_router()), \
             patch.object(clf, "query_llm_with_tools",
                          return_value={
                              "function": None,
                              "confidence": "low",
                              "score": 0.0,
                              "via_llm": True,
                              "llm_args": {},
                          }):
            result = clf.classify_intent("incomprehensible xyzzy blorp")

        assert result["function"] is None
        assert result["via_llm"] is True


# ── Dynamic online learning ───────────────────────────────────────────────────

class TestDynamicLearning:
    """Verify utterances are persisted correctly and excluded when appropriate."""

    def test_short_phrase_is_saved(self, isolated_data_dir):
        """A phrase ≤ 15 words with a real intent should be written to disk."""
        import classifier as clf
        clf.add_utterance_dynamically("show me memory pigs running on CPU",
                                      "top_memory_processes")

        assert os.path.isfile(isolated_data_dir)
        with open(isolated_data_dir) as f:
            data = json.load(f)

        assert "top_memory_processes" in data
        assert "show me memory pigs running on CPU" in data["top_memory_processes"]

    def test_long_phrase_over_15_words_is_not_saved(self, isolated_data_dir):
        """Phrases > 15 words must be excluded to prevent vector space pollution."""
        import classifier as clf
        long_phrase = (
            "can you please show me which processes on this computer "
            "are consuming the most amount of RAM right now"
        )
        assert len(long_phrase.split()) > 15

        clf.add_utterance_dynamically(long_phrase, "top_memory_processes")

        assert not os.path.isfile(isolated_data_dir)

    def test_general_question_is_never_saved(self, isolated_data_dir):
        """general_question results must never be written to dynamic_utterances.json."""
        import classifier as clf
        clf.add_utterance_dynamically("what is recursion", "general_question")

        assert not os.path.isfile(isolated_data_dir)

    def test_duplicate_phrase_is_not_saved_twice(self, isolated_data_dir):
        """Calling add_utterance_dynamically twice with the same phrase deduplicates."""
        import classifier as clf
        phrase = "how much juice is left on disk"
        clf.add_utterance_dynamically(phrase, "free_space_summary")
        clf.add_utterance_dynamically(phrase, "free_space_summary")

        with open(isolated_data_dir) as f:
            data = json.load(f)

        assert data["free_space_summary"].count(phrase) == 1

    def test_static_phrase_is_not_saved_to_dynamic(self, isolated_data_dir):
        """Phrases already in INTENT_UTTERANCES must not be duplicated in the JSON."""
        import classifier as clf
        static_phrase = "find my resume file"  # already in search_files utterances
        clf.add_utterance_dynamically(static_phrase, "search_files")

        assert not os.path.isfile(isolated_data_dir)

    def test_dynamic_phrases_loaded_into_router_at_startup(self, isolated_data_dir):
        """Phrases saved to disk must appear in the merged utterance set."""
        import classifier as clf

        # Write a phrase directly to disk
        data = {"free_space_summary": ["how much juice is left on disk"]}
        with open(isolated_data_dir, "w") as f:
            json.dump(data, f)

        loaded = clf._load_dynamic_utterances()
        assert "free_space_summary" in loaded
        assert "how much juice is left on disk" in loaded["free_space_summary"]

    def test_router_hot_reload_after_save(self, isolated_data_dir):
        """After add_utterance_dynamically, the in-memory router is rebuilt."""
        import classifier as clf

        original_router_id = id(clf._intent_router)
        clf.add_utterance_dynamically("show RAM hogs right now", "top_memory_processes")
        new_router_id = id(clf._intent_router)

        # Router object must have been replaced (new RouteLayer instance)
        assert new_router_id != original_router_id

    def test_feedback_loop_triggered_after_llm_classification(self, isolated_data_dir):
        """When the LLM identifies a specific intent, it should be persisted."""
        import classifier as clf

        novel_phrase = "show me memory pigs running on CPU"

        mock_result = MagicMock()
        mock_result.name = None
        mock_router = MagicMock(return_value=mock_result)

        with patch.object(clf, "_intent_router", mock_router), \
             patch.object(clf, "query_llm_with_tools",
                          return_value={
                              "function": "top_memory_processes",
                              "confidence": "high",
                              "score": 1.0,
                              "via_llm": True,
                              "llm_args": {},
                          }):
            clf.classify_intent(novel_phrase)

        assert os.path.isfile(isolated_data_dir)
        with open(isolated_data_dir) as f:
            data = json.load(f)
        assert novel_phrase in data.get("top_memory_processes", [])

    def test_general_question_llm_result_not_persisted(self, isolated_data_dir):
        """If the LLM returns general_question, nothing should be saved."""
        import classifier as clf

        mock_result = MagicMock()
        mock_result.name = None
        mock_router = MagicMock(return_value=mock_result)

        with patch.object(clf, "_intent_router", mock_router), \
             patch.object(clf, "query_llm_with_tools",
                          return_value={
                              "function": None,  # general_question → None
                              "confidence": "high",
                              "score": 1.0,
                              "via_llm": True,
                              "llm_args": {},
                          }):
            clf.classify_intent("what is the meaning of life")

        assert not os.path.isfile(isolated_data_dir)


# ── ROUTER_TOOLS schema integrity ─────────────────────────────────────────────

class TestRouterToolsSchema:
    """Sanity-check the ROUTER_TOOLS declaration in classifier_tools.py."""

    def test_all_intents_present_in_tools(self):
        """Every intent in INTENT_UTTERANCES must have a tool entry."""
        from classifier_tools import VALID_INTENT_NAMES
        import classifier as clf

        for intent in clf.INTENT_UTTERANCES:
            assert intent in VALID_INTENT_NAMES, \
                f"Intent '{intent}' is missing from ROUTER_TOOLS"

    def test_general_question_in_tools(self):
        from classifier_tools import VALID_INTENT_NAMES
        assert "general_question" in VALID_INTENT_NAMES

    def test_all_tools_have_name_and_description(self):
        from classifier_tools import ROUTER_TOOLS
        for tool in ROUTER_TOOLS:
            assert "name" in tool and tool["name"], \
                f"Tool missing 'name': {tool}"
            assert "description" in tool and tool["description"], \
                f"Tool '{tool['name']}' missing 'description'"


# ── Existing regression tests (smoke-check they still pass) ──────────────────

class TestExistingRegressions:
    """Ensure none of the previous bug fixes were broken by this change."""

    def test_thank_you_is_not_correct_fact(self):
        import classifier as clf
        result = clf.classify_intent("okay thank you")
        assert result["function"] is None
        assert result["confidence"] == "high"
        assert result["via_llm"] is False

    def test_unsupported_media_control_is_not_fact_correction(self):
        import classifier as clf
        result = clf.classify_intent("stop the music played in spotify")
        assert result["function"] == "unsupported"
        assert result["confidence"] == "high"
        assert result["via_llm"] is False

    def test_open_application_routes_generic_app_request(self):
        import classifier as clf
        result = clf.classify_intent("can you open brave application")
        assert result["function"] == "open_application"
        assert result["confidence"] == "high"

    def test_close_application_remains_unsupported(self):
        import classifier as clf
        result = clf.classify_intent("close brave application")
        assert result["function"] == "unsupported"
