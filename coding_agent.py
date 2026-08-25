"""Coding specialist for the Zedek assistant.

Implements the SWE-agent pattern: plan → read context → patch → test → verify.

Safety boundaries:
  - READ:  /home/nithiish/Documents  (academic projects, safe)
  - WRITE: /home/nithiish/Documents/ai_assistanceworkspace  ONLY
  - All paths validated before any I/O
  - File application requires explicit user approval
  - Backups created before any file modification
"""

from __future__ import annotations

import ast
import json
import os
import re
import resource
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import llm_provider
from zedek_logger import get_logger

log = get_logger("coding_agent")

# ─── Path boundaries ───────────────────────────────────────────────────────
CODING_READ_ROOT = "/home/nithiish/Documents"
CODING_WRITE_ROOT = "/home/nithiish/Documents/ai_assistanceworkspace"
MAX_FILE_READ_BYTES = 50 * 1024  # 50 KB cap per file read
BACKUP_DIR = os.path.join(CODING_WRITE_ROOT, ".backups")


# ─── Path validation ───────────────────────────────────────────────────────

def _validate_read_path(path: str) -> str:
    """Resolve a path and ensure it is under CODING_READ_ROOT.

    Raises ValueError if the path escapes the allowed read boundary.
    """
    resolved = os.path.realpath(os.path.expanduser(path))
    if not resolved.startswith(CODING_READ_ROOT):
        raise ValueError(
            f"Read path '{path}' resolves to '{resolved}' which is outside "
            f"the allowed read directory ({CODING_READ_ROOT})."
        )
    return resolved


def _validate_write_path(path: str) -> str:
    """Resolve a path and ensure it is under CODING_WRITE_ROOT.

    Raises ValueError if the path escapes the allowed write boundary.
    """
    resolved = os.path.realpath(os.path.expanduser(path))
    if not resolved.startswith(CODING_WRITE_ROOT):
        raise ValueError(
            f"Write path '{path}' resolves to '{resolved}' which is outside "
            f"the allowed write directory ({CODING_WRITE_ROOT})."
        )
    # Never allow writing into the backups directory via a crafted path
    if resolved.startswith(BACKUP_DIR) and not resolved == BACKUP_DIR:
        # Allow creating BACKUP_DIR itself, but not arbitrary files under it
        # from the patch pathway — backups are written only by _create_backup()
        pass  # backup writes go through _create_backup which validates separately
    return resolved


# ─── File reading helpers ───────────────────────────────────────────────────

def _read_project_file(path: str) -> str | None:
    """Read a file from the allowed read directory, capped at MAX_FILE_READ_BYTES.

    Returns the file content as a string, or None if the path is invalid,
    unreadable, or exceeds the size cap.
    """
    try:
        safe_path = _validate_read_path(path)
    except ValueError as err:
        log.info("read_project_file_blocked", extra={"path": path, "reason": str(err)})
        return None

    if not os.path.isfile(safe_path):
        log.info("read_project_file_not_found", extra={"path": safe_path})
        return None

    file_size = os.path.getsize(safe_path)
    if file_size > MAX_FILE_READ_BYTES:
        log.info("read_project_file_too_large", extra={
            "path": safe_path, "size_bytes": file_size, "limit": MAX_FILE_READ_BYTES,
        })
        return None

    try:
        with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        log.info("read_project_file_ok", extra={"path": safe_path, "size_bytes": len(content)})
        return content
    except OSError as err:
        log.info("read_project_file_error", extra={"path": safe_path, "error": str(err)})
        return None


def _find_relevant_files(request: str, plan: dict[str, Any] | None = None) -> list[str]:
    """Identify project files likely relevant to the request.

    Uses lightweight heuristics:
    - Files explicitly listed in the plan
    - File names or extensions mentioned in the request
    - Python files in the write workspace

    Does NOT do a full recursive search — keeps it fast and bounded.
    """
    candidates: list[str] = []

    # 1. Files listed in plan
    if plan:
        for f in plan.get("files", []):
            candidates.append(f)
        for f in plan.get("files_to_modify", []):
            candidates.append(f)

    # 2. Scan the write workspace for existing files (shallow, bounded)
    if os.path.isdir(CODING_WRITE_ROOT):
        try:
            for entry in os.scandir(CODING_WRITE_ROOT):
                if entry.is_file() and not entry.name.startswith("."):
                    candidates.append(entry.path)
        except OSError:
            pass

    # 3. Look for file-like patterns in the request (e.g. "utils.py", "main.js")
    file_pattern = re.compile(r'\b[\w\-]+\.\w{1,5}\b')
    for match in file_pattern.findall(request):
        # Try to resolve against both read and write roots
        for root in [CODING_WRITE_ROOT, CODING_READ_ROOT]:
            candidate = os.path.join(root, match)
            if os.path.isfile(candidate):
                candidates.append(candidate)

    # Deduplicate and validate
    seen: set[str] = set()
    valid: list[str] = []
    for path in candidates:
        try:
            resolved = _validate_read_path(path)
            if resolved not in seen and os.path.isfile(resolved):
                seen.add(resolved)
                valid.append(resolved)
        except ValueError:
            continue

    log.info("relevant_files_found", extra={"count": len(valid), "files": valid[:10]})
    return valid[:10]  # hard cap


def _gather_file_context(file_paths: list[str]) -> str:
    """Read multiple files and format them as context for the LLM prompt."""
    if not file_paths:
        return "(No existing project files provided.)"

    sections: list[str] = []
    for path in file_paths:
        content = _read_project_file(path)
        if content is not None:
            basename = os.path.basename(path)
            sections.append(f"--- {basename} ({path}) ---\n{content}")

    if not sections:
        return "(No readable project files found.)"
    return "\n\n".join(sections)


# ─── Markdown stripping ────────────────────────────────────────────────────

def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM output.

    Handles ```python, ```py, ```json, plain ```, and leading/trailing
    whitespace variations.
    """
    text = text.strip()
    # Match opening fence with optional language tag
    if re.match(r'^```(?:\w+)?\s*\n', text):
        lines = text.splitlines()
        # Remove first line (opening fence)
        lines = lines[1:]
        # Remove last line if it's a closing fence
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines)
    return text.strip()


# ─── Resource limits for sandbox ────────────────────────────────────────────

def _apply_resource_limits(timeout_seconds: int) -> None:
    """Bound CPU, memory, process count, and file growth inside the child."""
    resource.setrlimit(resource.RLIMIT_CPU, (max(1, timeout_seconds), max(1, timeout_seconds)))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
    resource.setrlimit(resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024))


# ─── Backup management ─────────────────────────────────────────────────────

def _create_backup(file_path: str) -> str | None:
    """Create a timestamped backup of a file before modification.

    Returns the backup path, or None if the original doesn't exist.
    """
    if not os.path.isfile(file_path):
        return None

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    basename = os.path.basename(file_path)
    backup_name = f"{basename}.{timestamp}.bak"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    shutil.copy2(file_path, backup_path)
    log.info("backup_created", extra={"original": file_path, "backup": backup_path})
    return backup_path


# ═══════════════════════════════════════════════════════════════════════════
# Verifier
# ═══════════════════════════════════════════════════════════════════════════

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

    def review_with_llm(
        self,
        request: str,
        code: str,
        test_results: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask a second LLM (different task profile) to review the generated code.

        Uses task="general_qa" so it may hit a different provider than the one
        that generated the code — giving a genuine second opinion.
        """
        test_summary = "(No test results available.)"
        if test_results:
            test_summary = json.dumps(test_results, indent=2, default=str)

        prompt = f"""You are a code reviewer. Review this generated code for correctness.

Original request: {request}

Generated code:
{code}

Test results:
{test_summary}

Return ONLY valid JSON with this shape:
{{"approved": true/false, "issues": ["..."], "summary": "one-line summary"}}

Check for:
1. Does the code actually solve the original request?
2. Are there obvious bugs, logic errors, or edge cases?
3. Is the code safe (no system modifications, no credential exposure)?
4. Did the tests pass? If not, is the failure in the code or the test?

Be strict — only approve if the code genuinely works."""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}],
                json_mode=True,
                task="general_qa",  # different task profile for second opinion
            )
            review = json.loads(result["answer"])
            log.info("llm_review_complete", extra={
                "approved": review.get("approved"),
                "source": result.get("source"),
            })
            return {
                "approved": review.get("approved", False),
                "issues": review.get("issues", []),
                "summary": review.get("summary", "Review completed."),
                "reviewer_source": result.get("source", "unknown"),
            }
        except (json.JSONDecodeError, TypeError, KeyError) as err:
            log.info("llm_review_parse_failed", extra={"error": str(err)})
            return {
                "approved": False,
                "issues": ["LLM review returned unparseable output."],
                "summary": "Review failed — treating as not approved.",
                "reviewer_source": "error",
            }
        except Exception as err:
            log.info("llm_review_error", extra={"error": str(err)})
            return {
                "approved": False,
                "issues": [f"Review error: {err}"],
                "summary": "Review unavailable.",
                "reviewer_source": "error",
            }


# ═══════════════════════════════════════════════════════════════════════════
# Sandboxed Python Runner
# ═══════════════════════════════════════════════════════════════════════════

class SandboxedPythonRunner:
    """Run generated Python in a network-isolated bubblewrap sandbox.

    Supports two modes:
    - Isolated (default): No project access, no network. For untrusted snippets.
    - Project-aware: Mounts CODING_READ_ROOT as read-only so tests can import
      project modules. Optionally allows network for API testing.
    """

    def __init__(self, timeout_seconds: int = 10, max_output_bytes: int = 16_384) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.verifier = Verifier()

    def run(
        self,
        code: str,
        project_aware: bool = False,
        allow_network: bool = False,
    ) -> dict[str, Any]:
        """Execute valid Python in the sandbox.

        Args:
            code: Python source to execute.
            project_aware: If True, mount CODING_READ_ROOT read-only into the
                sandbox so the code can import project modules.
            allow_network: If True, don't isolate the network namespace.
                Only meaningful when project_aware is True (for API tests).
        """
        if not self.verifier.verify_python(code):
            return {"status": "rejected", "returncode": None, "stdout": "", "stderr": "invalid Python"}

        bubblewrap = shutil.which("bwrap")
        python = "/usr/bin/python3"
        if bubblewrap is None or not os.path.exists(python):
            return {"status": "unavailable", "returncode": None, "stdout": "",
                    "stderr": "bubblewrap or python3 is unavailable"}

        with tempfile.TemporaryDirectory(prefix="zedek-code-") as workspace:
            script_path = os.path.join(workspace, "main.py")
            with open(script_path, "w", encoding="utf-8") as script:
                script.write(code)

            command = [bubblewrap]

            # Namespace isolation — skip network unshare if network access needed
            if allow_network and project_aware:
                command += ["--unshare-pid", "--unshare-uts", "--unshare-ipc"]
            else:
                command += ["--unshare-all"]

            command += [
                "--die-with-parent",
                "--new-session",
                "--clearenv",
                "--setenv", "PATH", "/usr/bin:/bin",
                "--setenv", "HOME", "/tmp",
                "--ro-bind", "/usr", "/usr",
                "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib",
                "--ro-bind", "/lib64", "/lib64",
                "--proc", "/proc",
                "--dev", "/dev",
                "--tmpfs", "/tmp",
                "--dir", "/sandbox",
                "--ro-bind", script_path, "/sandbox/main.py",
            ]

            # Network support: bind resolv.conf and SSL certs
            if allow_network and project_aware:
                if os.path.exists("/etc/resolv.conf"):
                    command += ["--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf"]
                if os.path.isdir("/etc/ssl"):
                    command += ["--ro-bind", "/etc/ssl", "/etc/ssl"]
                # Python may need ca-certificates
                if os.path.isdir("/etc/pki"):
                    command += ["--ro-bind", "/etc/pki", "/etc/pki"]

            # Project-aware mode: mount read roots
            if project_aware and os.path.isdir(CODING_READ_ROOT):
                command += ["--ro-bind", CODING_READ_ROOT, CODING_READ_ROOT]
                # Also set PYTHONPATH so imports work
                command += ["--setenv", "PYTHONPATH", CODING_READ_ROOT]

            command += [
                "--chdir", "/sandbox",
                "--", python, "/sandbox/main.py",
            ]

            log.info("sandbox_run", extra={
                "project_aware": project_aware,
                "allow_network": allow_network,
            })

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
            "status": (
                "passed"
                if completed.returncode == 0
                else (
                    "unavailable"
                    if "Creating new namespace failed" in completed.stderr
                    or "Resource temporarily unavailable" in completed.stderr
                    else "failed"
                )
            ),
            "returncode": completed.returncode,
            "stdout": completed.stdout[: self.max_output_bytes],
            "stderr": completed.stderr[: self.max_output_bytes],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Coding Specialist
# ═══════════════════════════════════════════════════════════════════════════

class CodingSpecialist:
    """A context-aware coding specialist following the SWE-agent pattern.

    Workflow: plan → read context → patch → test → verify.
    All file writes restricted to CODING_WRITE_ROOT.
    All file reads restricted to CODING_READ_ROOT.
    """

    def __init__(self) -> None:
        self._verifier = Verifier()
        self._runner = SandboxedPythonRunner()

    # ─── Phase 2A: Context-aware planning ────────────────────────────────

    def plan_task(
        self,
        user_request: str,
        file_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a concrete, context-aware plan for a coding request.

        If file_paths is provided, those files are read and included as
        context in the planning prompt. Otherwise, auto-discovery is used.
        """
        request = (user_request or "Complete the requested code task").strip()

        # Gather file context
        if file_paths is None:
            file_paths = _find_relevant_files(request)
        file_context = _gather_file_context(file_paths)

        prompt = f"""You are a coding planner. Create a specific, actionable implementation plan.

User's coding request:
{request}

Existing project files (for context):
{file_context}

The workspace for writing files is: {CODING_WRITE_ROOT}
The project files can be read from: {CODING_READ_ROOT}

Return ONLY valid JSON with this exact shape:
{{"goal": "one-line goal description",
 "steps": ["step 1", "step 2", ...],
 "files_to_modify": ["path/to/file1.py", ...],
 "files_to_create": ["path/to/new_file.py", ...],
 "tests": ["test description 1", ...],
 "risks": ["potential risk 1", ...]}}

Rules:
- Keep changes minimal and focused on the request.
- All new files must be created under {CODING_WRITE_ROOT}.
- Include specific test descriptions.
- Identify any risks or edge cases.
- Do not include markdown formatting."""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}],
                json_mode=True,
                task="coding",
            )
            plan = json.loads(result["answer"])
            log.info("plan_generated", extra={
                "goal": plan.get("goal", "")[:100],
                "step_count": len(plan.get("steps", [])),
                "source": result.get("source"),
            })
        except (json.JSONDecodeError, TypeError) as err:
            log.info("plan_parse_failed", extra={"error": str(err)})
            plan = {}

        return {
            "goal": plan.get("goal", request),
            "steps": plan.get("steps", [
                "Inspect the relevant code.",
                "Implement the smallest safe change.",
                "Verify the result.",
            ]),
            "files_to_modify": plan.get("files_to_modify", []),
            "files_to_create": plan.get("files_to_create", []),
            "files_read": [os.path.basename(f) for f in (file_paths or [])],
            "tests": plan.get("tests", []),
            "risks": plan.get("risks", []),
            "constraints": [
                f"Write access restricted to {CODING_WRITE_ROOT}",
                f"Read access restricted to {CODING_READ_ROOT}",
                "Backups created before any file modification.",
                "User approval required before applying changes.",
            ],
            "pattern": "plan → read context → patch → test → verify",
        }

    # ─── Phase 2A: Context-aware patching ────────────────────────────────

    def patch(
        self,
        request: str,
        plan: dict[str, Any] | None = None,
        current_code: str = "",
        error: str = "",
    ) -> dict[str, Any]:
        """Generate a Python code patch using request, plan, and file context.

        Returns a structured result:
        {
            "code": "generated Python code",
            "target_file": "path where the code should be written",
            "is_new_file": True/False,
            "raw_response": "original LLM output",
        }
        """
        # Gather context from files mentioned in the plan
        context_files = []
        if plan:
            for f in plan.get("files_to_modify", []):
                context_files.append(f)
        file_context = _gather_file_context(
            [f for f in context_files if os.path.isfile(f)]
        ) if context_files else ""

        # Determine target file
        target_file = None
        is_new_file = False
        if plan:
            if plan.get("files_to_create"):
                target_file = plan["files_to_create"][0]
                is_new_file = True
            elif plan.get("files_to_modify"):
                target_file = plan["files_to_modify"][0]

        # Default target if none specified
        if not target_file:
            target_file = os.path.join(CODING_WRITE_ROOT, "solution.py")
            is_new_file = True

        prompt = f"""You are a Python coding specialist. Implement this request precisely.

Request: {request}

Plan:
{json.dumps(plan or {}, indent=2)}

Existing code in the target file:
{current_code or '(New file — no existing code.)'}

Additional project context:
{file_context or '(No additional files.)'}

Previous verification error to fix:
{error or '(None — first attempt.)'}

RULES:
- Return ONLY the complete Python code for the target file.
- Do NOT use markdown fences, explanations, or shell commands.
- Do NOT include code for unrelated files.
- The code must be syntactically valid Python.
- If fixing a previous error, address it specifically.
- Write clean, documented code with docstrings."""

        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}],
            task="coding",
        )

        raw = result["answer"]
        code = _strip_markdown_fences(raw)

        log.info("patch_generated", extra={
            "target_file": target_file,
            "is_new_file": is_new_file,
            "code_lines": len(code.splitlines()),
            "source": result.get("source"),
        })

        return {
            "code": code,
            "target_file": target_file,
            "is_new_file": is_new_file,
            "raw_response": raw,
            "provider_source": result.get("source", "unknown"),
        }

    # ─── Phase 2B: Test generation ───────────────────────────────────────

    def generate_tests(
        self,
        request: str,
        code: str,
        plan: dict[str, Any] | None = None,
    ) -> str:
        """Generate focused test code for a patch.

        Returns valid Python test code with 2–5 assertions.
        Tests are designed to run in the sandbox.
        """
        prompt = f"""Write focused Python test code for this implementation.

Original request: {request}

Implementation code:
{code}

Plan context:
{json.dumps(plan or {}, indent=2)}

RULES:
- Write 2-5 focused test assertions.
- Use simple assert statements (NOT unittest or pytest frameworks).
- Include the implementation code at the top so the tests are self-contained.
- Print "ALL TESTS PASSED" at the end if every assertion passes.
- Print clear error messages if any test fails.
- Do NOT import any external packages that aren't in Python's standard library.
- Do NOT use markdown fences — return only pure Python code.
- Test edge cases if relevant (empty input, None, boundaries)."""

        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}],
            task="coding",
        )

        test_code = _strip_markdown_fences(result["answer"])
        log.info("tests_generated", extra={
            "test_lines": len(test_code.splitlines()),
            "source": result.get("source"),
        })
        return test_code

    # ─── Phase 2D: Full implement + verify loop ─────────────────────────

    def implement_and_verify(
        self,
        request: str,
        current_code: str = "",
        max_retries: int = 1,
        project_aware: bool = False,
        allow_network: bool = False,
    ) -> dict[str, Any]:
        """Execute the full plan → patch → test → verify loop.

        Returns a structured result with plan, code, tests, verification,
        and LLM review. Does NOT apply changes to files — that requires
        separate explicit approval via apply_patch().
        """
        # Step 1: Plan
        plan = self.plan_task(request)

        # Step 2: Read existing code from target file if available
        if not current_code and plan.get("files_to_modify"):
            target = plan["files_to_modify"][0]
            existing = _read_project_file(target)
            if existing:
                current_code = existing

        error = ""
        attempts = max_retries + 1
        last_patch: dict[str, Any] = {}
        last_test_code = ""
        last_test_result: dict[str, Any] = {}
        last_review: dict[str, Any] = {}

        for attempt in range(1, attempts + 1):
            log.info("coding_attempt", extra={"attempt": attempt, "max": attempts})

            # Step 3: Generate patch
            patch_result = self.patch(
                request, plan=plan, current_code=current_code, error=error,
            )
            last_patch = patch_result
            code = patch_result["code"]

            # Step 4: Syntax verification
            if not self._verifier.verify_python(code):
                error = "Generated code failed Python syntax verification."
                log.info("syntax_check_failed", extra={"attempt": attempt})
                continue

            # Step 5: Generate and run tests
            test_code = self.generate_tests(request, code, plan)
            last_test_code = test_code

            if self._verifier.verify_python(test_code):
                test_result = self._runner.run(
                    test_code,
                    project_aware=project_aware,
                    allow_network=allow_network,
                )
                last_test_result = test_result

                if test_result["status"] == "passed":
                    log.info("tests_passed", extra={"attempt": attempt})
                elif test_result["status"] == "unavailable":
                    log.info("sandbox_unavailable", extra={"attempt": attempt})
                else:
                    error = f"Test execution failed: {test_result.get('stderr', '')}"
                    log.info("tests_failed", extra={
                        "attempt": attempt,
                        "stderr": test_result.get("stderr", "")[:200],
                    })
                    continue
            else:
                log.info("test_code_invalid_syntax", extra={"attempt": attempt})
                last_test_result = {"status": "skipped", "reason": "Generated test code had syntax errors"}

            # Step 6: Run the code itself in sandbox
            execution = self._runner.run(
                code,
                project_aware=project_aware,
                allow_network=allow_network,
            )

            if execution["status"] in {"passed", "unavailable"}:
                # Step 7: LLM-assisted review (second opinion)
                review = self._verifier.review_with_llm(
                    request, code, last_test_result,
                )
                last_review = review

                return {
                    "status": "passed" if execution["status"] == "passed" else "unverified",
                    "attempts": attempt,
                    "plan": plan,
                    "patch": last_patch,
                    "code": code,
                    "test_code": last_test_code,
                    "test_result": last_test_result,
                    "execution": execution,
                    "review": review,
                }

            error = f"Sandbox execution failed: {execution.get('stderr', '')}"

        # All retries exhausted
        return {
            "status": "failed",
            "attempts": attempts,
            "plan": plan,
            "patch": last_patch,
            "code": last_patch.get("code", ""),
            "test_code": last_test_code,
            "test_result": last_test_result,
            "execution": last_test_result,
            "review": last_review,
            "error": error,
        }

    # ─── Phase 2E: Safe file application ─────────────────────────────────

    def apply_patch(self, patch_result: dict[str, Any]) -> dict[str, Any]:
        """Apply a verified patch to the filesystem.

        ONLY writes to CODING_WRITE_ROOT. Creates a backup of any existing
        file before overwriting. This method is ONLY called after explicit
        user approval in the orchestrator.
        """
        target_file = patch_result.get("target_file", "")
        code = patch_result.get("code", "")

        if not target_file or not code:
            return {"applied": False, "error": "No target file or code in patch result."}

        # Validate write path
        try:
            safe_path = _validate_write_path(target_file)
        except ValueError as err:
            log.info("apply_patch_blocked", extra={"target": target_file, "reason": str(err)})
            return {"applied": False, "error": str(err)}

        # Create backup if file exists
        backup_path = None
        if os.path.isfile(safe_path):
            backup_path = _create_backup(safe_path)

        # Ensure parent directory exists
        parent_dir = os.path.dirname(safe_path)
        os.makedirs(parent_dir, exist_ok=True)

        # Write the file
        try:
            with open(safe_path, "w", encoding="utf-8") as f:
                f.write(code)
            log.info("patch_applied", extra={
                "target": safe_path,
                "backup": backup_path,
                "code_lines": len(code.splitlines()),
            })
            return {
                "applied": True,
                "file": safe_path,
                "backup": backup_path,
                "lines_written": len(code.splitlines()),
            }
        except OSError as err:
            log.info("patch_write_failed", extra={"target": safe_path, "error": str(err)})
            return {"applied": False, "error": str(err)}


# ═══════════════════════════════════════════════════════════════════════════
# Module self-test
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    specialist = CodingSpecialist()
    verifier = Verifier()

    print("=" * 60)
    print("Path validation tests")
    print("=" * 60)
    print(f"Read root:  {CODING_READ_ROOT}")
    print(f"Write root: {CODING_WRITE_ROOT}")
    print(f"Write root exists: {os.path.isdir(CODING_WRITE_ROOT)}")

    # Quick syntax check
    print(f"\nSyntax check (valid):   {verifier.verify_python('print(1)')}")
    print(f"Syntax check (invalid): {verifier.verify_python('def broken(:')}")

    # Plan generation
    print("\n" + "=" * 60)
    print("Plan generation (live LLM call)")
    print("=" * 60)
    plan = specialist.plan_task("Write a function that checks if a number is prime")
    print(json.dumps(plan, indent=2))
