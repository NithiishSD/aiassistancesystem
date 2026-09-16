"""
Unit & integration tests for the task planner / decomposer (Roadmap Item 9).

Covers:
- should_decompose() heuristics: true positives, and the false-negative bias
  (simple compound requests should NOT trigger decomposition).
- decompose(): LLM-backed happy path, malformed JSON fallback, single-item
  passthrough, and total-failure fallback to punctuation splitting.
- Orchestrator integration: placeholder substitution and the decomposed
  execution pipeline running each sub-task through the normal single-task path.
"""

import json
from unittest.mock import patch

import pytest

import task_planner


class TestShouldDecompose:
    def test_short_request_never_decomposes(self):
        assert task_planner.should_decompose("open brave") is False

    def test_simple_compound_and_does_not_decompose(self):
        # A bare "and" joining two nouns/apps is ordinary English, not a
        # bundle of independent asks — must stay a single atomic task.
        assert task_planner.should_decompose("open brave and vscode") is False

    def test_sequence_word_then_triggers_decomposition(self):
        text = "check my free disk space and then find my resume file please"
        assert task_planner.should_decompose(text) is True

    def test_semicolon_triggers_decomposition(self):
        text = "find my resume file; remember that I study at PSG college"
        assert task_planner.should_decompose(text) is True

    def test_numbered_list_triggers_decomposition(self):
        text = "1. check free space 2. find my resume file 3. tell me the weather"
        assert task_planner.should_decompose(text) is True

    def test_three_plus_verbs_with_and_triggers_decomposition(self):
        text = "open brave, find my resume file, and remember my college name"
        assert task_planner.should_decompose(text) is True

    def test_empty_text_does_not_decompose(self):
        assert task_planner.should_decompose("") is False
        assert task_planner.should_decompose(None) is False


class TestDecompose:
    def test_happy_path_parses_llm_json(self):
        mock_response = {
            "answer": json.dumps({"subtasks": ["check my free disk space", "find my resume file"]}),
            "source": "gemini",
        }
        with patch("task_planner.llm_provider.generate_chat", return_value=mock_response):
            result = task_planner.decompose("check my free disk space and then find my resume file")
        assert len(result) == 2
        assert result[0]["description"] == "check my free disk space"
        assert result[1]["description"] == "find my resume file"

    def test_single_item_list_passthrough(self):
        mock_response = {"answer": json.dumps({"subtasks": ["find my resume file"]}), "source": "groq"}
        with patch("task_planner.llm_provider.generate_chat", return_value=mock_response):
            result = task_planner.decompose("find my resume file")
        assert len(result) == 1

    def test_malformed_json_falls_back_to_punctuation_split(self):
        mock_response = {"answer": "not valid json at all", "source": "groq"}
        with patch("task_planner.llm_provider.generate_chat", return_value=mock_response):
            result = task_planner.decompose("check free space and then find my resume file")
        assert len(result) == 2
        assert "check free space" in result[0]["description"]

    def test_llm_exception_falls_back_to_punctuation_split(self):
        with patch("task_planner.llm_provider.generate_chat", side_effect=RuntimeError("all providers down")):
            result = task_planner.decompose("check free space and then find my resume file")
        assert len(result) == 2

    def test_total_failure_still_returns_original_text(self):
        with patch("task_planner.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            result = task_planner.decompose("just one plain atomic request")
        assert len(result) == 1
        assert result[0]["description"] == "just one plain atomic request"

    def test_empty_input_returns_original(self):
        result = task_planner.decompose("")
        assert result == [{"description": ""}]

    def test_subtasks_capped_at_max(self):
        many = {"subtasks": [f"do thing {i}" for i in range(10)]}
        mock_response = {"answer": json.dumps(many), "source": "groq"}
        with patch("task_planner.llm_provider.generate_chat", return_value=mock_response):
            result = task_planner.decompose("do many things")
        assert len(result) == task_planner.MAX_SUBTASKS


class TestOrchestratorIntegration:
    def test_substitute_result_placeholders(self):
        import orchestrator
        text = "remember that {{result_of_1}} is my free space"
        out = orchestrator._substitute_result_placeholders(text, ["62GB"])
        assert out == "remember that 62GB is my free space"

    def test_substitute_out_of_range_placeholder_left_untouched(self):
        import orchestrator
        text = "remember {{result_of_5}}"
        out = orchestrator._substitute_result_placeholders(text, ["62GB"])
        assert out == "remember {{result_of_5}}"

    def test_handle_decomposed_runs_each_subtask_via_single_pipeline(self):
        import orchestrator

        fake_subtasks = [{"description": "task A"}, {"description": "task B"}]
        answers = iter(["answer A", "answer B"])

        with patch("orchestrator.task_planner.decompose", return_value=fake_subtasks), \
             patch("orchestrator._handle_single", side_effect=lambda text: next(answers)):
            result = orchestrator._handle_decomposed("do task A and then task B")

        assert "task A" in result
        assert "answer A" in result
        assert "task B" in result
        assert "answer B" in result

    def test_handle_decomposed_single_subtask_delegates_to_single(self):
        import orchestrator

        with patch("orchestrator.task_planner.decompose", return_value=[{"description": "only task"}]), \
             patch("orchestrator._handle_single", return_value="the answer") as mock_single:
            result = orchestrator._handle_decomposed("only task")

        mock_single.assert_called_once_with("only task")
        assert result == "the answer"

    def test_handle_routes_bundled_request_through_decomposition(self):
        import orchestrator

        with patch("orchestrator.task_planner.should_decompose", return_value=True), \
             patch("orchestrator._handle_decomposed", return_value="decomposed answer") as mock_decomp:
            result = orchestrator.handle("check free space and then find my resume file")

        mock_decomp.assert_called_once()
        assert result == "decomposed answer"
