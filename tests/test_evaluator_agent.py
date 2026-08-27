"""
Unit & integration tests for EvaluatorAgent (Roadmap Item 8).

Covers:
- Pillar 1: Static syntax, forbidden imports, dangerous call patterns, AST scope check.
- Pillar 2: Dynamic sandbox verification (success, failure, timeout).
- Pillar 3: Dual-model LLM review parsing, schema enforcement, failure fallbacks.
- Master pipeline: evaluate() structured EvaluationReport generation.
"""

import json
from unittest.mock import patch

import pytest

from evaluator_agent import EvaluatorAgent, EvaluationReport


class TestEvaluatorAgent:
    def setup_method(self):
        self.evaluator = EvaluatorAgent(sandbox_timeout=3)

    # ── Pillar 1 Tests ────────────────────────────────────────────────────────

    def test_static_security_valid_code(self):
        code = "def add(a: int, b: int) -> int:\n    return a + b\n"
        is_safe, issues = self.evaluator.check_static_security(code)
        assert is_safe is True
        assert issues == []

    def test_static_security_rejects_empty_code(self):
        is_safe, issues = self.evaluator.check_static_security("")
        assert is_safe is False
        assert len(issues) > 0

    def test_static_security_rejects_syntax_error(self):
        is_safe, issues = self.evaluator.check_static_security("def broken(:")
        assert is_safe is False
        assert any("Syntax error" in iss for iss in issues)

    def test_static_security_rejects_forbidden_import(self):
        code = "import pty\npty.spawn('/bin/sh')"
        is_safe, issues = self.evaluator.check_static_security(code)
        assert is_safe is False
        assert any("Forbidden security-sensitive import" in iss for iss in issues)

    def test_static_security_rejects_dangerous_calls(self):
        code = "import os\nos.system('rm -rf /')\n"
        is_safe, issues = self.evaluator.check_static_security(code)
        assert is_safe is False
        assert any("Potentially unsafe call" in iss for iss in issues)

    def test_ast_scope_detects_mass_deletion(self):
        original = "def f1(): pass\ndef f2(): pass\ndef f3(): pass\ndef f4(): pass\n"
        new_code = "def f1(): pass\n"  # 75% functions deleted
        is_ok, issues = self.evaluator.check_ast_scope(original, new_code)
        assert is_ok is False
        assert any("Major regression" in iss for iss in issues)

    def test_ast_scope_allows_non_regressive_edit(self):
        original = "def f1(): pass\ndef f2(): pass\n"
        new_code = "def f1(): return 1\ndef f2(): return 2\ndef f3(): pass\n"
        is_ok, issues = self.evaluator.check_ast_scope(original, new_code)
        assert is_ok is True
        assert issues == []

    # ── Pillar 2 Tests ────────────────────────────────────────────────────────

    def test_dynamic_verification_success(self):
        code = "def square(x):\n    return x * x\n"
        test_code = code + "\nassert square(4) == 16\nprint('ALL_PASSED')\n"
        res = self.evaluator.run_dynamic_verification(code=code, test_code=test_code)
        assert res["status"] == "passed"
        assert "ALL_PASSED" in res["stdout"]

    def test_dynamic_verification_failure(self):
        code = "def square(x):\n    return x + x\n"
        test_code = code + "\nassert square(4) == 16\n"
        res = self.evaluator.run_dynamic_verification(code=code, test_code=test_code)
        assert res["status"] == "failed"

    # ── Pillar 3 Tests ────────────────────────────────────────────────────────

    @patch("evaluator_agent.llm_provider.generate_chat")
    def test_llm_evaluate_approved(self, mock_chat):
        mock_chat.return_value = {
            "answer": json.dumps({
                "approved": True,
                "verdict": "accept",
                "score": 0.95,
                "issues": [],
                "remediation_advice": [],
                "summary": "Implementation is clean and matches requirements.",
            }),
            "source": "gemini",
        }
        res = self.evaluator.evaluate_with_llm("write add function", "def add(a, b): return a + b")
        assert res["approved"] is True
        assert res["verdict"] == "accept"
        assert res["score"] == 0.95
        assert res["reviewer_source"] == "gemini"

    @patch("evaluator_agent.llm_provider.generate_chat")
    def test_llm_evaluate_rejected_with_advice(self, mock_chat):
        mock_chat.return_value = {
            "answer": json.dumps({
                "approved": False,
                "verdict": "retry",
                "score": 0.4,
                "issues": ["Division by zero not handled"],
                "remediation_advice": ["Add check if denominator == 0"],
                "summary": "Needs zero-division guard.",
            }),
            "source": "groq",
        }
        res = self.evaluator.evaluate_with_llm("write div function", "def div(a, b): return a / b")
        assert res["approved"] is False
        assert res["verdict"] == "retry"
        assert "Division by zero not handled" in res["issues"]
        assert "Add check if denominator == 0" in res["remediation_advice"]

    @patch("evaluator_agent.llm_provider.generate_chat")
    def test_llm_evaluate_handles_malformed_json(self, mock_chat):
        mock_chat.return_value = {"answer": "not valid json", "source": "local"}
        res = self.evaluator.evaluate_with_llm("write test", "x = 1")
        assert res["approved"] is False
        assert res["verdict"] == "retry"
        assert "invalid JSON" in res["issues"][0]

    # ── Master Pipeline Integration Tests ─────────────────────────────────────

    @patch("evaluator_agent.llm_provider.generate_chat")
    def test_evaluate_full_pipeline_pass(self, mock_chat):
        mock_chat.return_value = {
            "answer": json.dumps({
                "approved": True,
                "verdict": "accept",
                "score": 0.98,
                "issues": [],
                "remediation_advice": [],
                "summary": "Passed all checks.",
            }),
            "source": "gemini",
        }
        code = "def is_even(n: int) -> bool:\n    return n % 2 == 0\n"
        test_code = code + "assert is_even(2) is True\nassert is_even(3) is False\n"
        report = self.evaluator.evaluate(
            request="implement is_even function",
            code=code,
            test_code=test_code,
            run_dynamic_tests=True,
        )
        assert isinstance(report, EvaluationReport)
        assert report.is_approved is True
        assert report.verdict == "accept"
        assert report.static_passed is True
        assert report.sandbox_status == "passed"
        assert report.score >= 0.8

    def test_evaluate_pipeline_stops_on_security_violation(self):
        code = "import pty\npty.spawn('/bin/bash')"
        report = self.evaluator.evaluate(
            request="open shell",
            code=code,
            run_dynamic_tests=False,
        )
        assert report.is_approved is False
        assert report.verdict == "reject"
        assert report.static_passed is False
        assert any("Forbidden security-sensitive import" in iss for iss in report.issues)
