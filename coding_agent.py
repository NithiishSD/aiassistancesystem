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
import os
import shutil
import subprocess
import tempfile
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


class SandboxedPythonRunner:
    """Run generated Python in a network-isolated bubblewrap sandbox."""

    def __init__(self, timeout_seconds: int = 5, max_output_bytes: int = 16_384) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.verifier = Verifier()

    def run(self, code: str) -> dict[str, Any]:
        """Execute valid Python without shell parsing or project filesystem access."""
        if not self.verifier.verify_python(code):
            return {"status": "rejected", "returncode": None, "stdout": "", "stderr": "invalid Python"}

        bubblewrap = shutil.which("bwrap")
        # Use the system interpreter because the active Conda launcher may be a
        # symlink whose target is outside the sandbox's explicitly mounted paths.
        python = "/usr/bin/python3"
        if bubblewrap is None or not os.path.exists(python):
            return {"status": "unavailable", "returncode": None, "stdout": "", "stderr": "bubblewrap or python3 is unavailable"}

        with tempfile.TemporaryDirectory(prefix="zedek-code-") as workspace:
            script_path = os.path.join(workspace, "main.py")
            with open(script_path, "w", encoding="utf-8") as script:
                script.write(code)

            command = [
                bubblewrap,
                "--unshare-all",
                "--die-with-parent",
                "--new-session",
                "--clearenv",
                "--setenv", "PATH", "/usr/bin:/bin",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/lib64", "/lib64",
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--dir", "/sandbox",
                "--ro-bind", script_path, "/sandbox/main.py",
                "--chdir", "/sandbox",
                "--", python, "/sandbox/main.py",
            ]

            try:
                completed = subprocess.run(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                return {
                    "status": "timeout",
                    "returncode": None,
                    "stdout": (error.stdout or "")[: self.max_output_bytes],
                    "stderr": (error.stderr or "")[: self.max_output_bytes],
                }
            except OSError as error:
                return {"status": "unavailable", "returncode": None, "stdout": "", "stderr": str(error)}

        return {
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[: self.max_output_bytes],
            "stderr": completed.stderr[: self.max_output_bytes],
        }


if __name__ == "__main__":
    specialist = CodingSpecialist()
    verifier = Verifier()

    sample = specialist.plan_task("Add a helper that uppercases text and validate it with a unit test")
    print(sample)
    print(verifier.verify_python("def normalize(text):\n    return text.strip().upper()\n"))
