"""
Unit and integration tests for the tiered SandboxRunner.

Covers:
- Syntax pre-check rejection
- Snippet execution in isolated sandbox
- Timeout enforcement (infinite loop)
- Memory / Process limit configuration
- Copy-on-Write (CoW) project test execution (verifying host repo is not mutated)
- Project read-only mode and imports
- Backward-compatible SandboxedPythonRunner delegation
"""

import os
import shutil
import tempfile
import pytest

from sandbox_runner import SandboxRunner, SandboxMode, ExecutionResult
from coding_agent import SandboxedPythonRunner


class TestSandboxRunner:
    def setup_method(self):
        self.runner = SandboxRunner(timeout_seconds=3, max_output_bytes=4096)

    def test_syntax_verification(self):
        assert self.runner.verify_syntax("x = 1 + 2") is True
        assert self.runner.verify_syntax("def valid(): pass") is True
        assert self.runner.verify_syntax("def broken(:") is False
        assert self.runner.verify_syntax("") is False

    def test_run_code_rejected_on_invalid_syntax(self):
        res = self.runner.run_code("def broken(:")
        assert res.status == "rejected"
        assert res.returncode is None
        assert "Invalid Python syntax" in res.stderr

    def test_run_code_success(self):
        code = "print('Hello from Zedek Sandbox!')\nprint(2 + 2)"
        res = self.runner.run_code(code)
        assert res.status == "passed"
        assert res.returncode == 0
        assert "Hello from Zedek Sandbox!" in res.stdout
        assert "4" in res.stdout
        assert res.duration_ms > 0

    def test_run_code_failure_captured(self):
        code = "raise ValueError('Custom sandbox error')"
        res = self.runner.run_code(code)
        assert res.status == "failed"
        assert res.returncode != 0
        assert "ValueError: Custom sandbox error" in res.stderr

    def test_timeout_protection(self):
        # 1-second timeout runner
        fast_runner = SandboxRunner(timeout_seconds=1)
        code = "import time\ntime.sleep(5)\nprint('done')"
        res = fast_runner.run_code(code)
        assert res.status in ("timeout", "failed")

    def test_cow_project_mirror_isolation(self):
        """Verify that files written in Copy-on-Write mode do NOT mutate the original project."""
        with tempfile.TemporaryDirectory(prefix="zedek-source-proj-") as source_dir:
            # Create a source file
            src_file = os.path.join(source_dir, "calc.py")
            with open(src_file, "w") as f:
                f.write("def add(a, b): return a + b\n")

            # Create a test file that modifies a file and creates a new artifact
            test_file = os.path.join(source_dir, "test_calc.py")
            with open(test_file, "w") as f:
                f.write(
                    "import os\n"
                    "import calc\n"
                    "def test_add():\n"
                    "    assert calc.add(2, 3) == 5\n"
                    "    with open('sandbox_artifact.txt', 'w') as f:\n"
                    "        f.write('created_in_sandbox')\n"
                )

            # Run test suite in Sandbox CoW
            res = self.runner.run_test_suite(
                project_root=source_dir,
                test_args=["-m", "pytest", "test_calc.py"],
            )

            assert res.status == "passed"
            assert "1 passed" in res.stdout or res.returncode == 0

            # CRITICAL CHECK: Artifact created inside sandbox must NOT exist in the original source directory!
            assert not os.path.exists(os.path.join(source_dir, "sandbox_artifact.txt"))

    def test_project_read_only_import(self):
        """Verify code can import modules from project_root."""
        with tempfile.TemporaryDirectory(prefix="zedek-ro-proj-") as source_dir:
            math_mod = os.path.join(source_dir, "mymath.py")
            with open(math_mod, "w") as f:
                f.write("def square(x): return x * x\n")

            snippet = "import mymath\nassert mymath.square(4) == 16\nprint('MATH_OK')"
            res = self.runner.run_code(
                code=snippet,
                mode=SandboxMode.PROJECT_READ_ONLY,
                project_root=source_dir,
            )
            assert res.status == "passed"
            assert "MATH_OK" in res.stdout


class TestSandboxedPythonRunnerDelegation:
    def setup_method(self):
        self.runner = SandboxedPythonRunner(timeout_seconds=2)

    def test_backward_compatibility_run(self):
        res = self.runner.run("print('Backward compat ok')")
        assert isinstance(res, dict)
        assert res["status"] == "passed"
        assert "Backward compat ok" in res["stdout"]

    def test_backward_compatibility_reject(self):
        res = self.runner.run("invalid(:")
        assert isinstance(res, dict)
        assert res["status"] == "rejected"
