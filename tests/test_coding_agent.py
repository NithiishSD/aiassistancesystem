"""Tests for the rewritten coding_agent.py.

All LLM calls are mocked — these tests run fully offline.
Tests cover:
  - Path validation (read/write boundaries)
  - File reading (size caps, invalid paths)
  - Markdown fence stripping
  - Plan generation structure
  - Patch generation structure
  - Test generation
  - LLM-assisted verification
  - File application (backups, write restrictions)
  - Sandbox modes (project-aware, network flags)
"""

import json
import os
import shutil
import tempfile
from unittest.mock import patch, MagicMock

import pytest

# Module under test
import coding_agent
from coding_agent import (
    CodingSpecialist,
    Verifier,
    SandboxedPythonRunner,
    _validate_read_path,
    _validate_write_path,
    _read_project_file,
    _find_relevant_files,
    _gather_file_context,
    _strip_markdown_fences,
    _create_backup,
    CODING_READ_ROOT,
    CODING_WRITE_ROOT,
    BACKUP_DIR,
    MAX_FILE_READ_BYTES,
)


# ═══════════════════════════════════════════════════════════════════════════
# Path validation
# ═══════════════════════════════════════════════════════════════════════════

class TestPathValidation:
    """Path boundaries must be enforced strictly."""

    def test_valid_read_path(self):
        """A path under CODING_READ_ROOT resolves without error."""
        result = _validate_read_path(CODING_READ_ROOT)
        assert result == os.path.realpath(CODING_READ_ROOT)

    def test_read_path_outside_boundary_raises(self):
        """Paths outside CODING_READ_ROOT must raise ValueError."""
        with pytest.raises(ValueError, match="outside the allowed read directory"):
            _validate_read_path("/etc/passwd")

    def test_read_path_traversal_blocked(self):
        """Path traversal attempts (../../) must be caught."""
        with pytest.raises(ValueError, match="outside the allowed read directory"):
            _validate_read_path(os.path.join(CODING_READ_ROOT, "../../etc/passwd"))

    def test_valid_write_path(self):
        """A path under CODING_WRITE_ROOT resolves without error."""
        result = _validate_write_path(os.path.join(CODING_WRITE_ROOT, "test.py"))
        assert result.startswith(CODING_WRITE_ROOT)

    def test_write_path_outside_boundary_raises(self):
        """Paths outside CODING_WRITE_ROOT must raise ValueError."""
        with pytest.raises(ValueError, match="outside the allowed write directory"):
            _validate_write_path("/tmp/malicious.py")

    def test_write_path_in_read_root_raises(self):
        """Writing to the read root (but outside write root) must fail."""
        # CODING_READ_ROOT is /home/nithiish/Documents
        # CODING_WRITE_ROOT is /home/nithiish/Documents/ai_assistanceworkspace
        # Writing to /home/nithiish/Documents/other_project/ should fail
        with pytest.raises(ValueError, match="outside the allowed write directory"):
            _validate_write_path(os.path.join(CODING_READ_ROOT, "other_project", "file.py"))


# ═══════════════════════════════════════════════════════════════════════════
# File reading
# ═══════════════════════════════════════════════════════════════════════════

class TestFileReading:
    """File reads must respect boundaries and size caps."""

    def test_read_nonexistent_file_returns_none(self):
        """Reading a file that doesn't exist should return None."""
        result = _read_project_file(os.path.join(CODING_READ_ROOT, "nonexistent_file_xyz.py"))
        assert result is None

    def test_read_outside_boundary_returns_none(self):
        """Reading outside the read boundary should return None, not raise."""
        result = _read_project_file("/etc/passwd")
        assert result is None

    @patch("coding_agent.os.path.getsize")
    @patch("coding_agent.os.path.isfile", return_value=True)
    def test_oversized_file_returns_none(self, mock_isfile, mock_getsize):
        """Files exceeding MAX_FILE_READ_BYTES should return None."""
        mock_getsize.return_value = MAX_FILE_READ_BYTES + 1
        result = _read_project_file(os.path.join(CODING_READ_ROOT, "huge_file.py"))
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Markdown stripping
# ═══════════════════════════════════════════════════════════════════════════

class TestMarkdownStripping:
    """LLM output often includes markdown fences that must be removed."""

    def test_strip_python_fences(self):
        code = "```python\ndef hello():\n    return 'hi'\n```"
        result = _strip_markdown_fences(code)
        assert result == "def hello():\n    return 'hi'"

    def test_strip_py_fences(self):
        code = "```py\nprint('hi')\n```"
        result = _strip_markdown_fences(code)
        assert result == "print('hi')"

    def test_strip_plain_fences(self):
        code = "```\nx = 1\n```"
        result = _strip_markdown_fences(code)
        assert result == "x = 1"

    def test_no_fences_unchanged(self):
        code = "def foo():\n    pass"
        result = _strip_markdown_fences(code)
        assert result == code

    def test_empty_string(self):
        assert _strip_markdown_fences("") == ""
        assert _strip_markdown_fences("   ") == ""


# ═══════════════════════════════════════════════════════════════════════════
# Verifier
# ═══════════════════════════════════════════════════════════════════════════

class TestVerifier:
    """Syntax checking and LLM review."""

    def setup_method(self):
        self.verifier = Verifier()

    def test_valid_python(self):
        assert self.verifier.verify_python("def foo(): return 1") is True

    def test_invalid_python(self):
        assert self.verifier.verify_python("def broken(:") is False

    def test_empty_code_invalid(self):
        assert self.verifier.verify_python("") is False
        assert self.verifier.verify_python("   ") is False

    def test_review_patch_valid(self):
        result = self.verifier.review_patch("x = 1")
        assert result["valid"] is True
        assert result["status"] == "pass"

    def test_review_patch_invalid(self):
        result = self.verifier.review_patch("def broken(:")
        assert result["valid"] is False
        assert len(result["issues"]) > 0

    @patch("coding_agent.llm_provider.generate_chat")
    def test_review_with_llm_approved(self, mock_chat):
        """LLM review returning approved=true should pass through."""
        mock_chat.return_value = {
            "answer": json.dumps({
                "approved": True,
                "issues": [],
                "summary": "Code looks correct.",
            }),
            "source": "gemini",
        }
        result = self.verifier.review_with_llm("write hello", "print('hello')", {})
        assert result["approved"] is True
        assert result["reviewer_source"] == "gemini"

    @patch("coding_agent.llm_provider.generate_chat")
    def test_review_with_llm_rejected(self, mock_chat):
        """LLM review returning approved=false should include issues."""
        mock_chat.return_value = {
            "answer": json.dumps({
                "approved": False,
                "issues": ["Missing edge case handling"],
                "summary": "Needs improvement.",
            }),
            "source": "groq",
        }
        result = self.verifier.review_with_llm("write sort", "def sort(x): pass", {})
        assert result["approved"] is False
        assert len(result["issues"]) > 0

    @patch("coding_agent.llm_provider.generate_chat")
    def test_review_with_llm_parse_failure(self, mock_chat):
        """If the LLM returns garbage, review should fail safe."""
        mock_chat.return_value = {"answer": "not json", "source": "local"}
        result = self.verifier.review_with_llm("test", "x=1", {})
        assert result["approved"] is False
        assert "unparseable" in result["issues"][0].lower()


# ═══════════════════════════════════════════════════════════════════════════
# Plan generation
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanGeneration:
    """Plan task should return structured, complete plans."""

    def setup_method(self):
        self.specialist = CodingSpecialist()

    @patch("coding_agent.llm_provider.generate_chat")
    def test_plan_returns_required_fields(self, mock_chat):
        """Plan must always include goal, steps, constraints, and pattern."""
        mock_chat.return_value = {
            "answer": json.dumps({
                "goal": "Write a prime checker",
                "steps": ["Create function", "Add tests"],
                "files_to_create": ["/home/nithiish/Documents/ai_assistanceworkspace/prime.py"],
                "tests": ["Test with prime number", "Test with non-prime"],
                "risks": [],
            }),
            "source": "nvidia_nim",
        }
        plan = self.specialist.plan_task("Write a function to check if a number is prime")
        assert "goal" in plan
        assert "steps" in plan
        assert "constraints" in plan
        assert "pattern" in plan
        assert len(plan["steps"]) >= 1
        assert CODING_WRITE_ROOT in plan["constraints"][0]

    @patch("coding_agent.llm_provider.generate_chat")
    def test_plan_handles_llm_failure(self, mock_chat):
        """If the LLM returns invalid JSON, plan should have sensible defaults."""
        mock_chat.return_value = {"answer": "this is not json", "source": "local"}
        plan = self.specialist.plan_task("do something")
        assert plan["goal"] == "do something"
        assert len(plan["steps"]) >= 1  # defaults provided


# ═══════════════════════════════════════════════════════════════════════════
# Patch generation
# ═══════════════════════════════════════════════════════════════════════════

class TestPatchGeneration:
    """Patch should return structured output with code and target file."""

    def setup_method(self):
        self.specialist = CodingSpecialist()

    @patch("coding_agent.llm_provider.generate_chat")
    def test_patch_returns_structured_result(self, mock_chat):
        """Patch must return code, target_file, and is_new_file."""
        mock_chat.return_value = {
            "answer": "def is_prime(n):\n    if n < 2: return False\n    return all(n % i for i in range(2, n))",
            "source": "nvidia_nim",
        }
        plan = {"files_to_create": [os.path.join(CODING_WRITE_ROOT, "prime.py")]}
        result = self.specialist.patch("write prime checker", plan=plan)
        assert "code" in result
        assert "target_file" in result
        assert "is_new_file" in result
        assert result["is_new_file"] is True

    @patch("coding_agent.llm_provider.generate_chat")
    def test_patch_strips_markdown_fences(self, mock_chat):
        """Markdown fences in LLM output should be removed."""
        mock_chat.return_value = {
            "answer": "```python\nprint('hello')\n```",
            "source": "local",
        }
        result = self.specialist.patch("print hello")
        assert "```" not in result["code"]
        assert "print('hello')" in result["code"]

    @patch("coding_agent.llm_provider.generate_chat")
    def test_patch_defaults_target_to_write_root(self, mock_chat):
        """If no files are in the plan, target defaults to CODING_WRITE_ROOT/solution.py."""
        mock_chat.return_value = {"answer": "x = 1", "source": "local"}
        result = self.specialist.patch("compute something")
        assert result["target_file"].startswith(CODING_WRITE_ROOT)


# ═══════════════════════════════════════════════════════════════════════════
# Test generation
# ═══════════════════════════════════════════════════════════════════════════

class TestTestGeneration:
    """Generated tests should be valid Python."""

    def setup_method(self):
        self.specialist = CodingSpecialist()

    @patch("coding_agent.llm_provider.generate_chat")
    def test_generates_valid_python_tests(self, mock_chat):
        """Generated test code should parse as valid Python."""
        mock_chat.return_value = {
            "answer": "def is_prime(n):\n    return n > 1 and all(n % i for i in range(2, n))\n\nassert is_prime(2) == True\nassert is_prime(4) == False\nassert is_prime(17) == True\nprint('ALL TESTS PASSED')",
            "source": "groq",
        }
        verifier = Verifier()
        test_code = self.specialist.generate_tests(
            "prime checker", "def is_prime(n): pass", {}
        )
        assert verifier.verify_python(test_code)


# ═══════════════════════════════════════════════════════════════════════════
# File application
# ═══════════════════════════════════════════════════════════════════════════

class TestFileApplication:
    """apply_patch must respect write boundaries and create backups."""

    def setup_method(self):
        self.specialist = CodingSpecialist()
        self.test_dir = tempfile.mkdtemp(prefix="zedek-test-")
        # Temporarily override write root for testing
        self._orig_write = coding_agent.CODING_WRITE_ROOT
        self._orig_backup = coding_agent.BACKUP_DIR
        coding_agent.CODING_WRITE_ROOT = self.test_dir
        coding_agent.BACKUP_DIR = os.path.join(self.test_dir, ".backups")

    def teardown_method(self):
        coding_agent.CODING_WRITE_ROOT = self._orig_write
        coding_agent.BACKUP_DIR = self._orig_backup
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_apply_creates_new_file(self):
        """Applying a patch to a new file should create it."""
        target = os.path.join(self.test_dir, "new_file.py")
        result = self.specialist.apply_patch({
            "code": "print('hello')",
            "target_file": target,
        })
        assert result["applied"] is True
        assert os.path.isfile(target)
        with open(target) as f:
            assert f.read() == "print('hello')"

    def test_apply_creates_backup(self):
        """Overwriting an existing file should create a backup first."""
        target = os.path.join(self.test_dir, "existing.py")
        with open(target, "w") as f:
            f.write("old content")

        result = self.specialist.apply_patch({
            "code": "new content",
            "target_file": target,
        })
        assert result["applied"] is True
        assert result["backup"] is not None
        assert os.path.isfile(result["backup"])
        with open(result["backup"]) as f:
            assert f.read() == "old content"

    def test_apply_blocks_outside_write_root(self):
        """Applying to a path outside CODING_WRITE_ROOT must fail."""
        result = self.specialist.apply_patch({
            "code": "malicious",
            "target_file": "/tmp/hack.py",
        })
        assert result["applied"] is False

    def test_apply_empty_code_fails(self):
        """Applying with empty code or no target should fail."""
        result = self.specialist.apply_patch({"code": "", "target_file": ""})
        assert result["applied"] is False


# ═══════════════════════════════════════════════════════════════════════════
# Sandbox runner modes
# ═══════════════════════════════════════════════════════════════════════════

class TestSandboxRunner:
    """Sandbox runner should handle both isolated and project-aware modes."""

    def setup_method(self):
        self.runner = SandboxedPythonRunner()

    def test_reject_invalid_python(self):
        """Invalid Python should be rejected before sandbox entry."""
        result = self.runner.run("def broken(:")
        assert result["status"] == "rejected"

    def test_run_valid_code(self):
        """Valid Python should execute (or report unavailable if no bwrap)."""
        result = self.runner.run("print('hello')")
        assert result["status"] in {"passed", "unavailable"}

    def test_timeout_protection(self):
        """Infinite loops should be caught by timeout."""
        runner = SandboxedPythonRunner(timeout_seconds=2)
        result = runner.run("while True: pass")
        assert result["status"] in {"timeout", "unavailable"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
