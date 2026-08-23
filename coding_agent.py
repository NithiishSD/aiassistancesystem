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
import json
import os
import resource
import shutil
import subprocess
import tempfile
from typing import Any

import llm_provider


def _cloud_coding_enabled() -> bool:
    """Require an explicit opt-in before sending coding context to the cloud."""
    return os.getenv("ALLOW_CLOUD_CODING", "false").strip().lower() in {"true", "1", "yes"}


def _apply_resource_limits(timeout_seconds: int) -> None:
    """Bound CPU, memory, process count, and file growth inside the child."""
    resource.setrlimit(resource.RLIMIT_CPU, (max(1, timeout_seconds), max(1, timeout_seconds)))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))


class CodingSpecialist:
    """A small, deterministic coding specialist that follows the agreed loop.

    This is intentionally not a fully autonomous code runner. It keeps the
    architecture explicit: it plans, patches, and validates within a bounded
    workflow rather than trying to be a one-shot general coding agent.
    """

    def __init__(self, model_name: str = "local-safety-specialist") -> None:
        self.model_name = model_name
        self._verifier = Verifier()
        self._runner = SandboxedPythonRunner()

    def plan_task(self, user_request: str) -> dict[str, Any]:
        """Generate a concrete plan for the specific coding request."""
        request = (user_request or "Complete the requested code task").strip()
        prompt = f"""Create a concise implementation plan for this coding request:
{request}

Return ONLY valid JSON with this shape:
{{"goal": "...", "steps": ["..."], "files": ["..."], "tests": ["..."]}}
The plan must be specific to the request, keep changes minimal, and include
verification steps. Do not include markdown."""
        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            force_local=not _cloud_coding_enabled(),
        )
        try:
            plan = json.loads(result["answer"])
        except (json.JSONDecodeError, TypeError):
            plan = {}

        return {
            "goal": plan.get("goal", request),
            "steps": plan.get("steps", ["Inspect the relevant code.", "Implement the smallest safe change.", "Verify the result."]),
            "files": plan.get("files", []),
            "tests": plan.get("tests", []),
            "constraints": [
                "Keep the change narrow and reviewable.",
                "Do not execute arbitrary shell commands from model-generated text.",
                "Validate with syntax and focused tests before claiming success.",
            ],
            "pattern": "plan -> patch -> test -> verify",
            "model": self.model_name,
        }

    def patch(self, request: str, current_code: str = "", plan: dict[str, Any] | None = None, error: str = "") -> str:
        """Generate a Python code patch using the request and plan as context."""
        prompt = f"""Implement this coding request:
{request}

Plan:
{json.dumps(plan or {}, indent=2)}

Current code:
{current_code}

Previous verification error, if any:
{error or '(none)'}

Return ONLY the complete Python code that should be verified. Do not use
markdown fences, explanations, shell commands, or code for unrelated files."""
        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}],
            force_local=not _cloud_coding_enabled(),
        )
        code = result["answer"].strip()
        if code.startswith("```"):
            lines = code.splitlines()
            code = "\n".join(lines[1:-1]).strip()
        return code

    def implement_and_verify(self, request: str, current_code: str = "", max_retries: int = 1) -> dict[str, Any]:
        """Generate code, verify syntax, run it in the sandbox, and retry on failure."""
        plan = self.plan_task(request)
        error = ""
        attempts = max_retries + 1
        for attempt in range(1, attempts + 1):
            code = self.patch(request, current_code, plan=plan, error=error)
            if not self._verifier.verify_python(code):
                error = "Generated code failed Python syntax verification."
                continue

            execution = self._runner.run(code)
            if execution["status"] in {"passed", "unavailable"}:
                return {"status": "passed" if execution["status"] == "passed" else "unverified", "attempts": attempt, "plan": plan, "code": code, "execution": execution}
            error = f"Sandbox execution failed: {execution.get('stderr', '')}"

        return {"status": "failed", "attempts": attempts, "plan": plan, "code": code, "error": error, "execution": execution if 'execution' in locals() else None}

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
                    start_new_session=True,
                    preexec_fn=lambda: _apply_resource_limits(self.timeout_seconds),
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
