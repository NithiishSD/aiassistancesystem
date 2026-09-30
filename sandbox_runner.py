"""
Tiered & Isolated Sandbox Runner for Zedek.

Provides multi-mode execution environments for Python snippets, test suites,
and project commands with strong security boundaries.

Key capabilities:
  1. Tiered Backend Architecture:
     - Bubblewrap (Linux namespaces, bind-mounts, unshare) when available and permitted.
     - Process Isolation + Resource Limits (setrlimit CPU, AS, NPROC, FSIZE) +
       Ephemeral Workspace Isolation when user namespaces are restricted by AppArmor.
  2. Multi-Mode Support:
     - SNIPPET: Stateless execution of single Python snippets in an empty sandbox.
     - PROJECT_READ_ONLY: Read-only access to project files without write pollution.
     - PROJECT_COPY_ON_WRITE: Ephemeral workspace mirror for running full test suites
       (pytest, unittest) so generated tests/caches never mutate the host repo.
  3. Strict Resource Bounds:
     - CPU limit (timeout), Memory limit (512MB default), Process limit (64),
       File size limit (10MB), Output truncation (32KB).
"""

from __future__ import annotations

import ast
import enum
import fnmatch
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any

from zedek_logger import get_logger

log = get_logger("sandbox_runner")


# ── Secret material (ROADMAP B3) ─────────────────────────────────────────────
# The child environment is already clean; these keep keys out through files and
# extra_env. The rlimit fallback has no filesystem isolation, so it is not a
# secret boundary: code there can read any file the user can by absolute path.

_HEAVY_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".venv", "zedek-env",
    "node_modules", ".backups", ".idea", ".vscode",
}
_SECRET_FILE_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ecdsa*", "id_ed25519*",
    ".netrc", ".pypirc", ".npmrc", "credentials*.json",
)
_SAFE_SUFFIXES = (".example", ".sample", ".template")
_SECRET_ENV_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")


def is_secret_file(name: str) -> bool:
    """Whether a file's base name marks it as holding credentials."""
    lowered = name.lower()
    if lowered.endswith(_SAFE_SUFFIXES):
        return False
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _SECRET_FILE_PATTERNS)


def _is_secret_env_name(name: str) -> bool:
    upper = name.upper()
    return upper.endswith(_SECRET_ENV_SUFFIXES) or "PASSWORD" in upper


def _secret_env_values() -> set[str]:
    return {v for k, v in os.environ.items() if v and _is_secret_env_name(k)}


def _secret_files(root: str) -> list[str]:
    """Secret files anywhere under root, skipping the dirs the mirror skips."""
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _HEAVY_DIRS]
        found.extend(os.path.join(current, f) for f in files if is_secret_file(f))
    return found


def _mirror_ignore(_directory: str, names: list[str]) -> set[str]:
    return {n for n in names if n in _HEAVY_DIRS or is_secret_file(n)}


_NO_ISOLATION_MESSAGE = (
    "Sandbox unavailable: bubblewrap is missing or cannot create namespaces, so the "
    "code was not run. Install bubblewrap (apt install bubblewrap), or set "
    "ZEDEK_SANDBOX_ALLOW_UNISOLATED=1 to run without filesystem or network isolation."
)


def _unisolated_allowed() -> bool:
    return os.getenv("ZEDEK_SANDBOX_ALLOW_UNISOLATED", "").strip().lower() in ("1", "true", "yes")


class SandboxMode(str, enum.Enum):
    SNIPPET = "snippet"
    PROJECT_READ_ONLY = "project_read_only"
    PROJECT_COPY_ON_WRITE = "project_copy_on_write"


@dataclass
class ExecutionResult:
    status: str  # "passed" | "failed" | "timeout" | "rejected" | "unavailable" | "error"
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: float
    isolation_backend: str  # "bubblewrap" | "rlimit_process"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "isolation_backend": self.isolation_backend,
        }


_SANDBOX_PROCESS_BUDGET = 64


def _process_limit() -> int:
    """RLIMIT_NPROC counts every thread of the user, not just the sandbox's, so
    a flat 64 fails the first fork on a desktop session (well over a thousand
    threads). Allow 64 beyond what the user already runs, computed in the
    parent; if /proc is unreadable, fall back to the flat budget."""
    uid, count = str(os.getuid()), 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return _SANDBOX_PROCESS_BUDGET
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/status", encoding="utf-8") as f:
                fields = dict(line.split(":", 1) for line in f if ":" in line)
        except OSError:
            continue
        if fields.get("Uid", "").split()[:1] == [uid]:
            count += int(fields.get("Threads", "1").strip() or 1)
    return count + _SANDBOX_PROCESS_BUDGET


def _apply_rlimits(timeout_seconds: int, max_memory_mb: int = 512,
                   max_processes: int = _SANDBOX_PROCESS_BUDGET) -> None:
    """Set hard and soft resource limits inside the child process."""
    # CPU Time limit
    cpu_limit = max(1, timeout_seconds + 1)
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))

    # Virtual Memory limit (Address Space)
    mem_bytes = max_memory_mb * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except (ValueError, OSError):
        pass

    # Process count limit (fork-bomb prevention)
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
    except (ValueError, OSError):
        pass

    # File size limit (prevents disk-filling attacks)
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    except (ValueError, OSError):
        pass


# Read-only system mounts shared by the probe and every run, so the probe tests
# exactly what a run needs (without /lib the dynamic loader is missing and
# every exec fails). -try: /lib64 does not exist on every architecture.
_BWRAP_SYSTEM_BINDS = [
    "--ro-bind", "/usr", "/usr",
    "--ro-bind", "/bin", "/bin",
    "--ro-bind", "/lib", "/lib",
    "--ro-bind-try", "/lib64", "/lib64",
    "--proc", "/proc",
    "--dev", "/dev",
    "--tmpfs", "/tmp",
]


def _is_bwrap_functional() -> bool:
    """Check if bubblewrap is installed AND can create namespaces without permission errors."""
    bwrap_path = shutil.which("bwrap")
    if not bwrap_path:
        return False

    try:
        # Test minimal bwrap invocation
        res = subprocess.run(
            [bwrap_path, "--unshare-all", *_BWRAP_SYSTEM_BINDS, "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2,
            check=False,
        )
        return res.returncode == 0
    except Exception:
        return False


class SandboxRunner:
    """Executes code, test suites, and commands inside an isolated environment."""

    def __init__(
        self,
        timeout_seconds: int = 10,
        max_output_bytes: int = 32_768,
        max_memory_mb: int = 512,
        allow_unisolated: bool | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_memory_mb = max_memory_mb
        self._has_bwrap = _is_bwrap_functional()
        # The rlimit fallback can read every file the user owns and has network
        # access, so it runs only when explicitly allowed (fail closed).
        self.allow_unisolated = _unisolated_allowed() if allow_unisolated is None else allow_unisolated
        backend = "bubblewrap" if self._has_bwrap else ("rlimit_process" if self.allow_unisolated else "none")
        log.info("sandbox_backend", extra={"backend": backend})
        log.info("sandbox_runner_init", extra={
            "bwrap_functional": self._has_bwrap,
            "timeout_seconds": self.timeout_seconds,
        })

    def verify_syntax(self, code: str) -> bool:
        """Statically verifies Python syntax before attempting execution."""
        if not code or not code.strip():
            return False
        try:
            ast.parse(code)
            compile(code, "<sandbox_verification>", "exec")
            return True
        except (SyntaxError, ValueError, TypeError):
            return False

    def run_code(
        self,
        code: str,
        mode: SandboxMode = SandboxMode.SNIPPET,
        project_root: str | None = None,
        allow_network: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run a Python snippet in the sandbox.

        Validates syntax first, sets up workspace according to mode, and executes.
        """
        if not self.verify_syntax(code):
            return ExecutionResult(
                status="rejected",
                returncode=None,
                stdout="",
                stderr="Invalid Python syntax or empty code snippet",
                duration_ms=0.0,
                isolation_backend="precheck",
            )

        with tempfile.TemporaryDirectory(prefix="zedek-sandbox-") as temp_dir:
            script_path = os.path.join(temp_dir, "main.py")
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(code)

            python_exe = sys.executable or "/usr/bin/python3"
            cmd = [python_exe, script_path]

            return self._execute_in_sandbox(
                cmd=cmd,
                work_dir=temp_dir,
                mode=mode,
                project_root=project_root,
                allow_network=allow_network,
                extra_env=extra_env,
            )

    def run_test_suite(
        self,
        project_root: str,
        test_args: list[str] | None = None,
        allow_network: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute pytest/unittest in an isolated Copy-on-Write workspace mirror.

        The source repository is untouched — all modifications/caches happen in
        an ephemeral directory discarded after test execution.
        """
        if not os.path.isdir(project_root):
            return ExecutionResult(
                status="error",
                returncode=None,
                stdout="",
                stderr=f"Project root '{project_root}' does not exist.",
                duration_ms=0.0,
                isolation_backend="precheck",
            )

        python_exe = sys.executable or "/usr/bin/python3"
        args = test_args if test_args is not None else ["-m", "pytest", "-v"]
        cmd = [python_exe] + args

        with tempfile.TemporaryDirectory(prefix="zedek-test-cow-") as cow_workspace:
            # Mirror project files (excluding heavy caches and virtual environments)
            self._mirror_project(project_root, cow_workspace)

            return self._execute_in_sandbox(
                cmd=cmd,
                work_dir=cow_workspace,
                mode=SandboxMode.PROJECT_COPY_ON_WRITE,
                project_root=cow_workspace,
                allow_network=allow_network,
                extra_env=extra_env,
            )

    def run_command(
        self,
        cmd: list[str],
        work_dir: str | None = None,
        mode: SandboxMode = SandboxMode.SNIPPET,
        project_root: str | None = None,
        allow_network: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run an arbitrary command inside the sandbox."""
        if not cmd:
            return ExecutionResult(
                status="error",
                returncode=None,
                stdout="",
                stderr="Empty command list provided",
                duration_ms=0.0,
                isolation_backend="precheck",
            )

        if work_dir is None:
            with tempfile.TemporaryDirectory(prefix="zedek-cmd-") as temp_dir:
                return self._execute_in_sandbox(
                    cmd=cmd,
                    work_dir=temp_dir,
                    mode=mode,
                    project_root=project_root,
                    allow_network=allow_network,
                    extra_env=extra_env,
                )

        return self._execute_in_sandbox(
            cmd=cmd,
            work_dir=work_dir,
            mode=mode,
            project_root=project_root,
            allow_network=allow_network,
            extra_env=extra_env,
        )

    # ── Internal Execution & Isolation Backends ───────────────────────────────

    def _execute_in_sandbox(
        self,
        cmd: list[str],
        work_dir: str,
        mode: SandboxMode,
        project_root: str | None,
        allow_network: bool,
        extra_env: dict[str, str] | None,
    ) -> ExecutionResult:
        """Dispatch to Bubblewrap or Resource-Limited Process backend."""
        start_time = time.perf_counter()

        if self._has_bwrap:
            res = self._execute_bwrap(cmd, work_dir, mode, project_root, allow_network, extra_env)
            if res.status != "unavailable":
                res.duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                return res

        if not self.allow_unisolated:
            log.info("sandbox_refused_unisolated", extra={"mode": mode.value})
            return ExecutionResult("unavailable", None, "", _NO_ISOLATION_MESSAGE, 0.0, "none")

        log.info("sandbox_unisolated_run", extra={"mode": mode.value})
        res = self._execute_rlimit(cmd, work_dir, mode, project_root, allow_network, extra_env)
        res.duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return res

    def _execute_bwrap(
        self,
        cmd: list[str],
        work_dir: str,
        mode: SandboxMode,
        project_root: str | None,
        allow_network: bool,
        extra_env: dict[str, str] | None,
    ) -> ExecutionResult:
        """Run command under bubblewrap namespace isolation."""
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return ExecutionResult("unavailable", None, "", "bwrap not found", 0.0, "bubblewrap")

        bwrap_cmd = [bwrap]
        if allow_network:
            bwrap_cmd += ["--unshare-pid", "--unshare-uts", "--unshare-ipc"]
        else:
            bwrap_cmd += ["--unshare-all"]

        # The child gets exactly the clean environment the rlimit path uses.
        clean_env = self._build_clean_env(extra_env, work_dir)
        if mode == SandboxMode.PROJECT_READ_ONLY and project_root:
            existing_pp = clean_env.get("PYTHONPATH", "")
            clean_env["PYTHONPATH"] = f"{project_root}:{existing_pp}" if existing_pp else project_root
        bwrap_cmd += ["--die-with-parent", "--new-session", "--clearenv"]
        for key, value in clean_env.items():
            bwrap_cmd += ["--setenv", key, value]
        bwrap_cmd += _BWRAP_SYSTEM_BINDS

        # Bind virtualenv if running in virtualenv
        venv_path = sys.prefix
        if os.path.isdir(venv_path):
            bwrap_cmd += ["--ro-bind", venv_path, venv_path]

        # Bind working directory (read-write inside sandbox)
        bwrap_cmd += ["--bind", work_dir, work_dir]

        secrets: list[str] = []
        if mode == SandboxMode.PROJECT_READ_ONLY and project_root and os.path.isdir(project_root):
            bwrap_cmd += ["--ro-bind", project_root, project_root]
            secrets = _secret_files(project_root)

        max_processes = _process_limit()
        # Secret files are masked with an empty regular file (later mounts win);
        # /dev/null cannot be used, the tmpfs holding the project is nodev.
        with tempfile.NamedTemporaryFile(prefix="zedek-mask-") as mask:
            for secret in secrets:
                bwrap_cmd += ["--ro-bind", mask.name, secret]
            bwrap_cmd += ["--chdir", work_dir, "--"] + cmd
            return self._run_bwrap(bwrap_cmd, max_processes)

    def _run_bwrap(self, bwrap_cmd: list[str], max_processes: int) -> ExecutionResult:
        try:
            proc = subprocess.run(
                bwrap_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                start_new_session=True,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},  # bwrap itself; the child gets --setenv
                preexec_fn=lambda: _apply_rlimits(self.timeout_seconds, self.max_memory_mb, max_processes),
            )
            stdout = proc.stdout[:self.max_output_bytes]
            stderr = proc.stderr[:self.max_output_bytes]
            status = "passed" if proc.returncode == 0 else "failed"
            return ExecutionResult(status, proc.returncode, stdout, stderr, 0.0, "bubblewrap")
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                "timeout",
                None,
                (exc.stdout or "")[:self.max_output_bytes],
                (exc.stderr or "")[:self.max_output_bytes],
                0.0,
                "bubblewrap",
            )
        except OSError as exc:
            return ExecutionResult("unavailable", None, "", str(exc), 0.0, "bubblewrap")

    def _execute_rlimit(
        self,
        cmd: list[str],
        work_dir: str,
        mode: SandboxMode,
        project_root: str | None,
        allow_network: bool,
        extra_env: dict[str, str] | None,
    ) -> ExecutionResult:
        """Run command under process isolation with OS resource limits."""
        clean_env = self._build_clean_env(extra_env, work_dir)
        max_processes = _process_limit()
        if project_root:
            existing_pp = clean_env.get("PYTHONPATH", "")
            clean_env["PYTHONPATH"] = f"{project_root}:{existing_pp}" if existing_pp else project_root

        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                start_new_session=True,
                env=clean_env,
                preexec_fn=lambda: _apply_rlimits(self.timeout_seconds, self.max_memory_mb, max_processes),
            )
            stdout = proc.stdout[:self.max_output_bytes]
            stderr = proc.stderr[:self.max_output_bytes]
            status = "passed" if proc.returncode == 0 else "failed"
            return ExecutionResult(status, proc.returncode, stdout, stderr, 0.0, "rlimit_process")
        except subprocess.TimeoutExpired as exc:
            return ExecutionResult(
                "timeout",
                None,
                (exc.stdout or "")[:self.max_output_bytes],
                (exc.stderr or "")[:self.max_output_bytes],
                0.0,
                "rlimit_process",
            )
        except OSError as exc:
            return ExecutionResult("error", None, "", str(exc), 0.0, "rlimit_process")

    def _build_clean_env(
        self,
        extra_env: dict[str, str] | None,
        work_dir: str,
    ) -> dict[str, str]:
        """Build a sanitized environment mapping."""
        base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": work_dir,
            "TMPDIR": work_dir,
        }
        if "VIRTUAL_ENV" in os.environ:
            base_env["VIRTUAL_ENV"] = os.environ["VIRTUAL_ENV"]

        if extra_env:
            secret_values = _secret_env_values()
            for k, v in extra_env.items():
                if k in ("LD_PRELOAD", "LD_LIBRARY_PATH"):  # block injection
                    continue
                if _is_secret_env_name(k) or (v and v in secret_values):
                    log.info("sandbox_env_secret_dropped", extra={"name": k})
                    continue
                base_env[k] = v
        return base_env

    def _mirror_project(self, src: str, dest: str) -> None:
        """Fast mirror of source project files to destination, ignoring caches/heavy directories."""
        for item in os.listdir(src):
            if _mirror_ignore(src, [item]):  # heavy dirs and secret files (B3)
                continue
            s_item = os.path.join(src, item)
            d_item = os.path.join(dest, item)
            try:
                if os.path.isdir(s_item):
                    shutil.copytree(s_item, d_item, ignore=_mirror_ignore)
                elif os.path.isfile(s_item):
                    shutil.copy2(s_item, d_item)
            except OSError:
                pass
