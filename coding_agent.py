"""Coding specialist scaffold for the Zedek assistant.

This module implements the same high-level pattern described in the roadmap:
plan -> read context -> patch -> test -> verify.

It is intentionally narrow and safety-first: it does not execute arbitrary
shell commands or mutate the system outside the project's own files. Instead,
it exposes a structured coding workflow meant to sit behind the main
orchestrator and the existing tier gate.
"""

from __future__ import annotations

import ast
from typing import Any


class CodingSpecialist:
    """A small, deterministic coding specialist that follows the agreed loop.

    This is intentionally not a fully autonomous code runner. It keeps the
    architecture explicit: it plans, patches, and validates within a bounded
    workflow rather than trying to be a one-shot general coding agent.
    """

    def __init__(self, model_name: str = "local-safety-specialist") -> None:
        self.model_name = model_name

    def plan_task(self, user_request: str) -> dict[str, Any]:
        """Return a concrete task plan that matches the project's roadmap."""
        goal = (user_request or "Complete the requested code task").strip() or "Complete the requested code task"

        steps = [
            "Read the relevant files and identify the exact root cause or missing feature.",
            "Create the smallest safe patch that addresses the request without broad rewrites.",
            "Add or run focused tests for the changed behavior.",
            "Review the result, fix any failing checks, and verify the final outcome.",
        ]

        if any(keyword in goal.lower() for keyword in ["bug", "fix", "error", "broken", "fail"]):
            steps[0] = "Trace the bug to the exact function or file before changing code."

        if any(keyword in goal.lower() for keyword in ["test", "unit", "pytest", "validate", "verify"]):
            steps[2] = "Write or update a focused test that captures the behavior to be validated."

        return {
            "goal": goal,
            "steps": steps,
            "constraints": [
                "Keep the change narrow and reviewable.",
                "Do not execute arbitrary shell commands from model-generated text.",
                "Validate with syntax and focused tests before claiming success.",
            ],
            "pattern": "plan -> patch -> test -> verify",
            "model": self.model_name,
        }

    def patch(self, request: str, current_code: str = "") -> str:
        """A lightweight stub for future code patch generation.

        This keeps the agent architecture explicit while remaining compatible with
        the current, safety-first system, which does not allow direct shell-based
        execution from the model.
        """
        if not current_code.strip():
            return f"No existing code was provided for: {request}"
        return f"Planned patch for: {request}\n\nReview the current implementation and apply the minimal change in the affected file(s)."


class Verifier:
    """Second-pass validation for generated Python code and patches."""

    def verify_python(self, code: str) -> bool:
        """Return True when the snippet parses and compiles cleanly."""
        if not code or not code.strip():
            return False
        try:
            ast.parse(code)
            compile(code, "<verification>", "exec")
            return True
        except (SyntaxError, ValueError):
            return False

    def review_patch(self, code: str) -> dict[str, Any]:
        """Return a simple structured result for a patch candidate."""
        valid = self.verify_python(code)
        return {
            "valid": valid,
            "issues": [] if valid else ["Python syntax error or invalid code snippet"],
            "status": "pass" if valid else "fail",
        }


if __name__ == "__main__":
    specialist = CodingSpecialist()
    verifier = Verifier()

    sample = specialist.plan_task("Add a helper that uppercases text and validate it with a unit test")
    print(sample)
    print(verifier.verify_python("def normalize(text):\n    return text.strip().upper()\n"))
