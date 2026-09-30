"""The choosing model's risk label can raise a tier, never lower it
(OpenSpec change: llm-risk-label, ROADMAP B6)."""

import json
from types import SimpleNamespace

import pytest

import orchestrator
import tier_gate
from routing_decision import RoutingDecision


@pytest.mark.parametrize("risk,expected", [(None, 0), ("LOW", 0), ("MEDIUM", 1), ("HIGH", 2), ("bogus", 0), (5, 0)])
def test_label_raises_a_tier_0_function(risk, expected):
    assert tier_gate.classify("search_files", {"query": "x"}, llm_risk=risk) == expected


@pytest.mark.parametrize("risk", [None, "LOW", "MEDIUM", "HIGH"])
def test_label_never_lowers(risk):
    assert tier_gate.classify("open_application", {"app_name": "x"}, llm_risk=risk) >= 1
    assert tier_gate.classify("not_a_function", {}, llm_risk=risk) == 3          # unknown stays blocked
    assert tier_gate.classify("search_files", {"query": "my credit card"}, llm_risk=risk) == 3  # force-3 stays
    assert tier_gate.classify("search_files", {"query": "rm -rf /"}, llm_risk=risk) == 2  # force-2 stays


def test_high_means_confirm_not_block():
    assert tier_gate.gate("search_files", {"query": "x"}, llm_risk="HIGH")["action"] == "confirm"


@pytest.fixture
def tool(monkeypatch):
    spec = SimpleNamespace(qualified_name="mcp_demo_send", tool_name="send", description="Reads data.",
                           input_schema={"type": "object", "properties": {"to": {"type": "string"}},
                                         "required": ["to"]})
    monkeypatch.setattr(orchestrator, "_select_mcp_tool", lambda text, target=None: spec)
    monkeypatch.setattr(orchestrator.mcp_client, "get_tool_registry", lambda: {spec.qualified_name: spec})
    return spec


def _reply(payload):
    return lambda messages, **kw: {"message": {"content": json.dumps(payload)}}


def test_extraction_returns_args_and_risk_in_one_call(tool, monkeypatch):
    calls = []

    def fake(messages, **kw):
        calls.append(kw["format"])
        return {"message": {"content": json.dumps({"args": {"to": "a@b.c"}, "risk": "HIGH"})}}
    monkeypatch.setattr(orchestrator.llm_provider, "local_chat", fake)
    out = orchestrator._extract_mcp_args("send it to a@b.c")
    assert out == {"qualified_name": "mcp_demo_send", "tool_args": {"to": "a@b.c"}, "risk": "HIGH"}
    assert len(calls) == 1 and calls[0]["properties"]["args"] == tool.input_schema
    assert calls[0]["properties"]["risk"]["enum"] == ["LOW", "MEDIUM", "HIGH"]


def test_invalid_label_is_ignored_not_trusted(tool, monkeypatch):
    monkeypatch.setattr(orchestrator.llm_provider, "local_chat", _reply({"args": {"to": "x"}, "risk": "NONE"}))
    assert orchestrator._extract_mcp_args("send it to x")["risk"] is None


def test_mcp_call_is_gated_with_the_label(tool, monkeypatch):
    seen = {}
    monkeypatch.setattr(orchestrator, "gate", lambda name, args, user_input="", llm_risk=None, tool_description="": (
        seen.update(risk=llm_risk), {"action": "blocked", "message": "stopped", "tier": 3})[1])
    decision = RoutingDecision("mcp_tool", "send it to x",
                               args={"qualified_name": "mcp_demo_send", "tool_args": {"to": "x"}, "risk": "HIGH"})
    assert orchestrator.execute(decision) == "stopped" and seen["risk"] == "HIGH"


def test_fallback_extraction_keeps_the_label(tool, monkeypatch):
    seen = {}
    monkeypatch.setattr(orchestrator.llm_provider, "local_chat", _reply({"args": {"to": "x"}, "risk": "MEDIUM"}))
    monkeypatch.setattr(orchestrator, "gate", lambda name, args, user_input="", llm_risk=None, tool_description="": (
        seen.update(risk=llm_risk), {"action": "blocked", "message": "stopped", "tier": 3})[1])
    orchestrator.execute(RoutingDecision("mcp_tool", "send it to x", args={}))
    assert seen["risk"] == "MEDIUM"
