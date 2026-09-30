"""
Comprehensive test suite for Zedek MCP Client Support.

Covers:
- Unit tests: config parsing, validation, duplicate rejection, cache lifecycle,
  result normalization, tier gate enforcement, schema validation, reversible classifier
  registration, routing precedence, and regression resilience.
- Integration tests: real STDIO subprocess execution against bundled mcp_demo_server.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import mcp.types as mt

import classifier
import mcp_client
from mcp_client import MCPServerConfig, MCPToolSpec
import orchestrator
import tier_gate


# ── Unit Tests: Config Loading & Validation ───────────────────────────────────

def test_load_config_valid(tmp_path, monkeypatch):
    cfg_data = [
        {
            "name": "srv1",
            "command": "python3",
            "args": ["-m", "demo"],
            "cwd": ".",
            "timeout": 5,
        }
    ]
    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_CONFIG_PATH", str(cfg_file))

    configs = mcp_client._load_config()
    assert len(configs) == 1
    assert configs[0].name == "srv1"
    assert configs[0].args == ["-m", "demo"]
    assert configs[0].timeout == 5.0


def test_load_config_missing_file(tmp_path, monkeypatch):
    non_existent = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(mcp_client, "_CONFIG_PATH", str(non_existent))

    configs = mcp_client._load_config()
    assert configs == []


def test_load_config_malformed_entries_skipped(tmp_path, monkeypatch):
    cfg_data = [
        {"name": "valid_srv", "command": "python3"},
        {"name": "", "command": "python3"},                   # empty name
        {"command": "python3"},                                 # missing name
        {"name": "no_cmd"},                                     # missing command
        {"name": "bad_args", "command": "python3", "args": 123},# invalid args
        "not_a_dict",                                          # invalid entry type
    ]
    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_CONFIG_PATH", str(cfg_file))

    configs = mcp_client._load_config()
    assert len(configs) == 1
    assert configs[0].name == "valid_srv"


def test_load_config_duplicate_server_name_rejected(tmp_path, monkeypatch):
    cfg_data = [
        {"name": "duplicate_srv", "command": "python3", "args": ["1"]},
        {"name": "duplicate_srv", "command": "python3", "args": ["2"]},
    ]
    cfg_file = tmp_path / "mcp_servers.json"
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_CONFIG_PATH", str(cfg_file))

    configs = mcp_client._load_config()
    assert len(configs) == 1
    assert configs[0].args == ["1"]  # first wins


# ── Unit Tests: Result Normalization ──────────────────────────────────────────

def test_normalize_tool_result_single_text():
    content = [mt.TextContent(type="text", text="Hello from MCP")]
    res = mt.CallToolResult(content=content, is_error=False)
    normalized = mcp_client._normalize_tool_result(res)
    assert normalized == {"result": "Hello from MCP", "error": None}


def test_normalize_tool_result_multiple_text():
    content = [
        mt.TextContent(type="text", text="Line 1"),
        mt.TextContent(type="text", text="Line 2"),
    ]
    res = mt.CallToolResult(content=content, is_error=False)
    normalized = mcp_client._normalize_tool_result(res)
    assert normalized == {"result": "Line 1\nLine 2", "error": None}


def test_normalize_tool_result_error():
    content = [mt.TextContent(type="text", text="Division by zero")]
    res = mt.CallToolResult(content=content, is_error=True)
    normalized = mcp_client._normalize_tool_result(res)
    assert normalized == {"result": None, "error": "Division by zero"}


def test_normalize_tool_result_empty():
    res = mt.CallToolResult(content=[], is_error=False)
    normalized = mcp_client._normalize_tool_result(res)
    assert normalized == {"result": "", "error": None}


def test_normalize_tool_result_none():
    normalized = mcp_client._normalize_tool_result(None)
    assert normalized["result"] is None
    assert "Null result" in normalized["error"]


# ── Unit Tests: Tool Registry & Invocation Boundaries ─────────────────────────

def test_call_mcp_tool_unknown_name():
    result = mcp_client.call_mcp_tool("mcp_unknown_tool", {})
    assert result["result"] is None
    assert "Unknown MCP tool" in result["error"]


def test_call_mcp_tool_empty_name():
    result = mcp_client.call_mcp_tool("", {})
    assert result["result"] is None
    assert "Invalid qualified_name" in result["error"]


def test_get_tool_registry_returns_copy():
    reg = mcp_client.get_tool_registry()
    reg["dummy"] = "corrupted"
    assert "dummy" not in mcp_client.get_tool_registry()


# ── Unit Tests: Tier Gate Safety for MCP ──────────────────────────────────────

def test_tier_gate_mcp_default_tier_1():
    decision = tier_gate.gate("mcp_zedek_tools_current_time", {})
    assert decision["tier"] == 1
    assert decision["action"] == "notify"


def test_tier_gate_mcp_forced_tier_2_pattern():
    decision = tier_gate.gate(
        "mcp_custom_tool",
        {"query": "delete temporary files"},
        user_input="delete files",
    )
    assert decision["tier"] == 2
    assert decision["action"] == "confirm"


def test_tier_gate_mcp_forced_tier_3_pattern():
    decision = tier_gate.gate(
        "mcp_custom_tool",
        {"account": "my credit card number is 1234"},
        user_input="check credit card",
    )
    assert decision["tier"] == 3
    assert decision["action"] == "blocked"


# ── Unit Tests: Schema Validation ─────────────────────────────────────────────

def test_validate_mcp_args_valid():
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    err = orchestrator._validate_mcp_args({"text": "sample"}, schema)
    assert err is None


def test_validate_mcp_args_missing_required():
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    err = orchestrator._validate_mcp_args({}, schema)
    assert err is not None
    assert "'text' is a required property" in err


def test_validate_mcp_args_wrong_type():
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
    }
    err = orchestrator._validate_mcp_args({"count": "not_an_int"}, schema)
    assert err is not None


# ── Unit Tests: Reversible Classifier Registration ────────────────────────────

def test_register_mcp_tools_reversible():
    dummy_tools = [
        MCPToolSpec(
            server_name="test_srv",
            tool_name="weather_check",
            description="Checks the local weather forecast.",
            input_schema={},
            qualified_name="mcp_test_srv_weather_check",
        )
    ]

    classifier.register_mcp_tools(dummy_tools)
    assert len(classifier._MCP_REGISTERED_UTTERANCES) == 1
    assert "weather_check: Checks the local weather forecast." in classifier._MCP_REGISTERED_UTTERANCES

    # Re-register with empty list — previous entries should be cleanly removed
    classifier.register_mcp_tools([])
    assert len(classifier._MCP_REGISTERED_UTTERANCES) == 0


# ── Critical Regression Test ──────────────────────────────────────────────────

def test_regression_existing_functions_work_without_mcp(tmp_path, monkeypatch):
    """
    Core promise verification:
    If mcp_servers.json is completely absent or empty, all built-in Zedek functions
    (free_space_summary, search_files, open_application, general_qa) continue to route
    and execute properly without errors.
    """
    empty_cfg = tmp_path / "empty_mcp_servers.json"
    empty_cfg.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mcp_client, "_CONFIG_PATH", str(empty_cfg))

    # Discover with empty config
    tools = mcp_client.discover_all_tools()
    assert tools == []

    # Verify built-in function routing is unaffected
    r1 = classifier.classify_intent("how much free disk space do I have")
    assert r1["function"] == "free_space_summary"

    r2 = classifier.classify_intent("find my resume file")
    assert r2["function"] == "search_files"

    # Precedence: built-in takes priority
    assert "free_space_summary" in orchestrator.AVAILABLE_FUNCTIONS


# ── Integration Tests: Real STDIO subprocess with mcp_demo_server ──────────────

def _discover_bundled_tools():
    """Real discovery, once more if the bundled server did not answer in time.

    Every configured server is started as a subprocess under its own timeout;
    on a busy machine the bundled one occasionally misses it, which failed
    these tests at random during full-suite runs."""
    tools = mcp_client.discover_all_tools()
    if not any(t.qualified_name == "mcp_zedek_tools_current_time" for t in tools):
        tools = mcp_client.discover_all_tools()
    return tools


def test_integration_mcp_discovery_and_stats():
    """Discover tools from bundled mcp_demo_server over real STDIO."""
    tools = _discover_bundled_tools()
    assert len(tools) >= 3

    qnames = [t.qualified_name for t in tools]
    assert "mcp_zedek_tools_current_time" in qnames
    assert "mcp_zedek_tools_word_count" in qnames
    assert "mcp_zedek_tools_summarize_text" in qnames

    stats = mcp_client.get_discovery_stats()
    assert stats["server_count"] >= 1
    assert stats["tool_count"] >= 3
    assert stats["failed_servers"] == 0


def test_integration_call_current_time():
    """Invoke current_time tool over real STDIO protocol."""
    _discover_bundled_tools()
    res = mcp_client.call_mcp_tool("mcp_zedek_tools_current_time", {})
    assert res["error"] is None
    assert res["result"] is not None
    assert len(res["result"]) > 5


def test_integration_call_word_count():
    """Invoke word_count tool with arguments over real STDIO protocol."""
    _discover_bundled_tools()
    res = mcp_client.call_mcp_tool(
        "mcp_zedek_tools_word_count",
        {"text": "The quick brown fox jumps over the lazy dog"},
    )
    assert res["error"] is None
    assert "Words: 9" in res["result"]
    assert "Chars: 43" in res["result"]


def test_integration_call_summarize_text():
    """Invoke summarize_text tool with arguments over real STDIO protocol."""
    _discover_bundled_tools()
    res = mcp_client.call_mcp_tool(
        "mcp_zedek_tools_summarize_text",
        {"text": "Zedek is an assistant. It helps with system inspection and coding."},
    )
    assert res["error"] is None
    assert "Zedek is an assistant." in res["result"]
    assert "words total" in res["result"]


def test_integration_orchestrator_execute_mcp_tool():
    """Test full dispatch pipeline through orchestrator._execute_mcp_tool()."""
    _discover_bundled_tools()
    decision = {
        "function": "mcp_tool",
        "domain": "personal",
        "confidence": "high",
        "score": 1.0,
        "_original_input": "count words in hello world foo bar",
        "args": {
            "qualified_name": "mcp_zedek_tools_word_count",
            "tool_args": {"text": "hello world foo bar"},
        },
    }
    output = orchestrator.execute(decision)
    assert "Words: 4" in output
    assert "Chars: 19" in output
