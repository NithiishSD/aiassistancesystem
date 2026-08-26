"""Unit tests for command_verifier.py and dynamic system inspection in orchestrator.py."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

import command_verifier as cv


class TestCommandVerifier:
    """Test safety filters, pattern detection, and pipeline verification."""

    def test_static_check_blocks_tier3(self):
        result = cv.static_check("rm -rf /home/nithiish")
        # In tier_gate, "rm -rf" is in FORCE_TIER_2_PATTERNS and high risk
        assert result["tier"] >= 2
        block_result = cv.static_check("send credit card payment")
        assert block_result["safe"] is False
        assert block_result["tier"] == 3

    def test_safe_readonly_pipeline_accepts_valid_tools(self):
        assert cv.is_safe_readonly_pipeline("uname -r") is True
        assert cv.is_safe_readonly_pipeline("dpkg -l | grep -c '^ii'") is True
        assert cv.is_safe_readonly_pipeline("lscpu | grep 'Model name' | head -n 1") is True
        assert cv.is_safe_readonly_pipeline("free -m | awk 'NR==2{print $2}'") is True

    def test_safe_readonly_pipeline_blocks_redirection(self):
        assert cv.is_safe_readonly_pipeline("echo hello > /tmp/test.txt") is False
        assert cv.is_safe_readonly_pipeline("dpkg -l >> log.txt") is False
        assert cv.is_safe_readonly_pipeline("uname -r | tee output.txt") is False

    def test_safe_readonly_pipeline_blocks_dangerous_commands(self):
        assert cv.is_safe_readonly_pipeline("sudo apt update") is False
        assert cv.is_safe_readonly_pipeline("chmod 777 script.sh") is False
        assert cv.is_safe_readonly_pipeline("eval $(echo dangerous)") is False
        assert cv.is_safe_readonly_pipeline("cat file.txt; rm file.txt") is False
        assert cv.is_safe_readonly_pipeline("uname -r && shutdown -h now") is False

    def test_dry_run_readonly_executes_real_safe_command(self):
        result = cv.dry_run_readonly("uname")
        assert result["success"] is True
        assert result["returncode"] == 0
        assert "linux" in result["stdout"].lower()

    def test_dry_run_readonly_rejects_unsafe_command(self):
        result = cv.dry_run_readonly("touch /tmp/malicious_file")
        assert result["success"] is False
        assert "rejected" in result["error"].lower()


class TestDynamicSystemInspection:
    """Test dynamic system inspection caching and routing."""

    def test_dynamic_system_tools_persistence(self, tmp_path, monkeypatch):
        import orchestrator as orch

        test_file = str(tmp_path / "dynamic_system_tools.json")
        monkeypatch.setattr(orch, "DYNAMIC_SYSTEM_TOOLS_PATH", test_file)

        orch._save_dynamic_system_tool("how many packages", "dpkg -l | grep -c '^ii'")
        loaded = orch._load_dynamic_system_tools()
        assert loaded.get("how many packages") == "dpkg -l | grep -c '^ii'"

    def test_handle_system_inspect_via_cache(self, tmp_path, monkeypatch):
        import orchestrator as orch

        test_file = str(tmp_path / "dynamic_system_tools.json")
        monkeypatch.setattr(orch, "DYNAMIC_SYSTEM_TOOLS_PATH", test_file)

        # Pre-seed the cache
        orch._save_dynamic_system_tool("test system query", "echo '42 packages'")

        with patch("llm_provider.generate_chat") as mock_llm:
            mock_llm.return_value = {"answer": "You have 42 packages installed.", "source": "mock"}
            result = orch._handle_system_inspect("test system query", "personal")
            assert "42 packages" in result
            # Should not call command generation LLM
            mock_llm.assert_called_once()  # Only called for synthesis
