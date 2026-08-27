"""
Evaluator & Verifier Agent for Zedek (Roadmap Item 8).

Provides independent, three-pillar verification for generated code, patches,
and complex task outputs to catch hallucinations, logic errors, regressions,
and security violations before delivery to the user.

Three-Pillar Architecture:
  1. Static & Security Analysis:
     - AST syntax parsing and structure verification.
     - Forbidden imports / dangerous system calls detection.
     - Scope & signature integrity diffing against original code.
  2. Dynamic Sandbox Execution:
     - Runs test assertions in the tiered SandboxRunner (Copy-on-Write mode).
     - Captures exit codes, timeouts, exceptions, and stdout/stderr.
  3. Dual-Model LLM Review:
     - Evaluates code using a complementary model family via task="evaluation".
     - Scores intent alignment, edge-case coverage, and hallucination flags.
     - Emits structured verdict: "accept", "retry", or "reject".
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any

import llm_provider
from sandbox_runner import SandboxRunner, SandboxMode
from zedek_logger import get_logger

log = get_logger("evaluator_agent")

# Dangerous module / call patterns that should never appear in generated patches
_FORBIDDEN_IMPORTS = {
    "pty", "posix", "syslog", "resource", "gc", "ctypes", "winreg", "_winreg"
}
_DANGEROUS_CALL_PATTERNS = [
    r"os\.system\s*\(",
    r"subprocess\.call\s*\(.*shell\s*=\s*True",
    r"subprocess\.Popen\s*\(.*shell\s*=\s*True",
    r"shutil\.rmtree\s*\(\s*['\"]\/",  # attempts to wipe root
    r"__import__\s*\(",
    r"eval\s*\(",
    r"exec\s*\(",
]


@dataclass
class EvaluationReport:
    """Comprehensive structured report produced by the EvaluatorAgent."""
    is_approved: bool
    verdict: str  # "accept" | "retry" | "reject"
    score: float  # 0.0 to 1.0 (1.0 = flawless)
    issues: list[str] = field(default_factory=list)
    remediation_advice: list[str] = field(default_factory=list)
    static_passed: bool = True
    sandbox_status: str | None = None
    reviewer_source: str = "none"
    execution_details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_approved": self.is_approved,
            "verdict": self.verdict,
            "score": self.score,
            "issues": self.issues,
            "remediation_advice": self.remediation_advice,
            "static_passed": self.static_passed,
            "sandbox_status": self.sandbox_status,
            "reviewer_source": self.reviewer_source,
            "execution_details": self.execution_details,
        }


class EvaluatorAgent:
    """Independent verifier ensuring code quality, security, and intent alignment."""

    def __init__(self, sandbox_timeout: int = 8) -> None:
        self.sandbox_runner = SandboxRunner(timeout_seconds=sandbox_timeout)

    # ── Pillar 1: Static & Security Analysis ──────────────────────────────────

    def check_static_security(self, code: str) -> tuple[bool, list[str]]:
        """Statically inspects code for syntax errors, forbidden imports, and risky calls."""
        issues: list[str] = []

        if not code or not code.strip():
            return False, ["Code is empty or whitespace only."]

        # 1. Syntax check
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            return False, [f"Syntax error at line {exc.lineno}: {exc.msg}"]
        except Exception as exc:
            return False, [f"AST parsing failed: {exc}"]

        # 2. Inspect AST for forbidden imports
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    if root_pkg in _FORBIDDEN_IMPORTS:
                        issues.append(f"Forbidden security-sensitive import: '{alias.name}'")
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    if root_pkg in _FORBIDDEN_IMPORTS:
                        issues.append(f"Forbidden security-sensitive import: '{node.module}'")

        # 3. Regex checks for dangerous patterns
        for pattern in _DANGEROUS_CALL_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                issues.append(f"Potentially unsafe call matching pattern: '{pattern}'")

        is_safe = len(issues) == 0
        return is_safe, issues

    def check_ast_scope(self, original_code: str, new_code: str) -> tuple[bool, list[str]]:
        """Verifies that modifications preserve existing required function and class signatures."""
        if not original_code.strip():
            return True, []  # New file, no baseline to check against

        issues: list[str] = []
        try:
            orig_tree = ast.parse(original_code)
            new_tree = ast.parse(new_code)
        except Exception:
            return True, []  # Syntax checked in check_static_security

        orig_funcs = {node.name for node in ast.walk(orig_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        new_funcs = {node.name for node in ast.walk(new_tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

        missing = orig_funcs - new_funcs
        # If more than 50% of existing functions disappeared, flag potential accidental deletion
        if orig_funcs and missing:
            if len(missing) > len(orig_funcs) * 0.5:
                issues.append(f"Major regression: {len(missing)} existing functions were removed: {list(missing)[:5]}")

        return len(issues) == 0, issues

    # ── Pillar 2: Dynamic Execution Verification ──────────────────────────────

    def run_dynamic_verification(
        self,
        code: str,
        test_code: str = "",
        project_root: str | None = None,
    ) -> dict[str, Any]:
        """Executes the code and any provided test assertions inside the sandbox."""
        if test_code and test_code.strip():
            # If self-contained test code was provided, run it in snippet sandbox
            res = self.sandbox_runner.run_code(
                code=test_code,
                mode=SandboxMode.SNIPPET,
            )
            return res.to_dict()

        if project_root and os.path.isdir(project_root):
            # Run test suite in Copy-on-Write project sandbox
            res = self.sandbox_runner.run_test_suite(project_root=project_root)
            return res.to_dict()

        # Run the code snippet directly
        res = self.sandbox_runner.run_code(code=code, mode=SandboxMode.SNIPPET)
        return res.to_dict()

    # ── Pillar 3: Dual-Model LLM Review ───────────────────────────────────────

    def evaluate_with_llm(
        self,
        request: str,
        code: str,
        plan: dict[str, Any] | None = None,
        test_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Uses a complementary model family to review the implementation against user intent."""
        test_summary = "(No dynamic test execution results available.)"
        if test_results:
            test_summary = json.dumps(test_results, indent=2, default=str)

        prompt = f"""You are the Zedek Verifier & Evaluator Agent.
Your job is to independently evaluate this generated implementation to ensure correctness, completeness, and safety.

User Request:
{request}

Plan Context:
{json.dumps(plan or {}, indent=2)}

Generated Code:
{code}

Dynamic Sandbox Execution Results:
{test_summary}

Analyze the solution strictly against these criteria:
1. Intent: Does this code genuinely and completely solve what the user requested?
2. Correctness: Are there logical flaws, unhandled edge cases, or broken assumptions?
3. Hallucination: Did the generator invent non-existent APIs, libraries, or return bogus types?
4. Safety: Is the code safe to run?

Return ONLY valid JSON matching this exact schema:
{{
  "approved": true/false,
  "verdict": "accept" | "retry" | "reject",
  "score": 0.0 to 1.0,
  "issues": ["issue 1", ...],
  "remediation_advice": ["specific instruction to fix issue 1", ...],
  "summary": "1-2 sentence overall evaluation"
}}"""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}],
                task="evaluation",
                json_mode=True,
            )
            data = json.loads(result["answer"])
            return {
                "approved": bool(data.get("approved", False)),
                "verdict": data.get("verdict", "retry" if not data.get("approved") else "accept"),
                "score": float(data.get("score", 0.8 if data.get("approved") else 0.4)),
                "issues": list(data.get("issues", [])),
                "remediation_advice": list(data.get("remediation_advice", [])),
                "summary": str(data.get("summary", "Evaluation complete.")),
                "reviewer_source": result.get("source", "unknown"),
            }
        except (json.JSONDecodeError, TypeError, KeyError) as err:
            log.info("evaluator_llm_parse_error", extra={"error": str(err)})
            return {
                "approved": False,
                "verdict": "retry",
                "score": 0.5,
                "issues": ["LLM evaluation returned unparseable or invalid JSON schema."],
                "remediation_advice": ["Re-run code generation cleanly."],
                "summary": "Review response parsing failed.",
                "reviewer_source": "error",
            }
        except Exception as err:
            log.info("evaluator_llm_call_error", extra={"error": str(err)})
            return {
                "approved": False,
                "verdict": "retry",
                "score": 0.5,
                "issues": [f"Evaluation service unavailable: {err}"],
                "remediation_advice": ["Retry generation pass."],
                "summary": "LLM review unavailable.",
                "reviewer_source": "error",
            }

    # ── Master Evaluation Pipeline ────────────────────────────────────────────

    def evaluate(
        self,
        request: str,
        code: str,
        original_code: str = "",
        plan: dict[str, Any] | None = None,
        test_code: str = "",
        project_root: str | None = None,
        run_dynamic_tests: bool = True,
    ) -> EvaluationReport:
        """Runs the full 3-pillar evaluation pipeline."""
        log.info("evaluator_pipeline_started", extra={"request": request[:60]})
        all_issues: list[str] = []
        remediations: list[str] = []

        # ── Pillar 1: Static & Security Analysis
        is_safe, sec_issues = self.check_static_security(code)
        all_issues.extend(sec_issues)
        if not is_safe:
            log.info("evaluator_static_security_rejected", extra={"issues": sec_issues})
            return EvaluationReport(
                is_approved=False,
                verdict="reject",
                score=0.0,
                issues=all_issues,
                remediation_advice=["Fix syntax errors and remove unsafe imports/calls."],
                static_passed=False,
                reviewer_source="static_security",
            )

        scope_ok, scope_issues = self.check_ast_scope(original_code, code)
        all_issues.extend(scope_issues)

        # ── Pillar 2: Dynamic Execution
        sandbox_res: dict[str, Any] = {}
        if run_dynamic_tests:
            sandbox_res = self.run_dynamic_verification(
                code=code,
                test_code=test_code,
                project_root=project_root,
            )
            sandbox_status = sandbox_res.get("status", "unknown")
            if sandbox_status == "failed":
                all_issues.append(f"Dynamic test execution failed: {sandbox_res.get('stderr') or 'AssertionError'}")
                remediations.append("Fix failing test assertions identified during sandbox run.")
            elif sandbox_status == "timeout":
                all_issues.append("Dynamic test execution timed out (potential infinite loop).")
                remediations.append("Ensure loops terminate and algorithms run within time limits.")

        # ── Pillar 3: Dual-Model LLM Review
        llm_review = self.evaluate_with_llm(
            request=request,
            code=code,
            plan=plan,
            test_results=sandbox_res if run_dynamic_tests else None,
        )

        all_issues.extend(llm_review.get("issues", []))
        remediations.extend(llm_review.get("remediation_advice", []))

        # Synthesize final decision
        has_critical_failure = not is_safe or sandbox_res.get("status") in ("failed", "timeout")
        is_approved = llm_review.get("approved", False) and not has_critical_failure

        if is_approved:
            verdict = "accept"
            score = max(0.8, llm_review.get("score", 0.9))
        else:
            verdict = "retry" if is_safe else "reject"
            score = min(0.6, llm_review.get("score", 0.4))

        report = EvaluationReport(
            is_approved=is_approved,
            verdict=verdict,
            score=score,
            issues=list(dict.fromkeys(all_issues)),  # deduplicate preserving order
            remediation_advice=list(dict.fromkeys(remediations)),
            static_passed=is_safe,
            sandbox_status=sandbox_res.get("status"),
            reviewer_source=llm_review.get("reviewer_source", "llm"),
            execution_details=sandbox_res,
        )

        log.info("evaluator_pipeline_completed", extra={
            "approved": report.is_approved,
            "verdict": report.verdict,
            "score": report.score,
        })
        return report
