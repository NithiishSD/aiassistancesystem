"""Typed routing decision (OpenSpec change: typed-routing-decision, ROADMAP E2)."""

from unittest.mock import patch

import pytest

import orchestrator
from routing_decision import RoutingDecision


def test_from_dict_maps_legacy_keys():
    d = RoutingDecision.from_dict({"function": "search_files", "_original_input": "find x",
                                   "args": {"query": "x"}, "via_llm": True})
    assert (d.function, d.user_input, d.args, d.via_llm) == ("search_files", "find x", {"query": "x"}, True)


def test_unknown_key_is_an_error_not_a_silent_default():
    with pytest.raises(TypeError, match="orignal_input"):
        RoutingDecision.from_dict({"function": None, "orignal_input": "typo"})
    with pytest.raises(TypeError):
        orchestrator.execute({"function": None, "vialm": True})


def test_domain_normalized_and_confidence_checked():
    assert RoutingDecision(None, domain="work").domain == "personal"
    with pytest.raises(ValueError):
        RoutingDecision(None, confidence="medium")


def test_args_never_shared_between_decisions():
    a, b = RoutingDecision(None), RoutingDecision(None)
    a.args["x"] = 1
    assert b.args == {}


def test_log_fields_exclude_args():
    fields = RoutingDecision("search_files", "find my cv", args={"query": "cv"}).log_fields()
    assert "args" not in fields and "user_input" not in fields


def test_route_request_returns_typed_decision_with_input():
    with patch.object(orchestrator.classifier, "is_acknowledgement", return_value=False), \
         patch.object(orchestrator, "should_ask_ambiguous_term_question", return_value=False), \
         patch.object(orchestrator.classifier, "classify_intent",
                      return_value={"function": "free_space_summary", "confidence": "high", "score": 0.9}), \
         patch.object(orchestrator.classifier, "classify_domain", return_value="academic"), \
         patch.object(orchestrator, "_extract_args", return_value={}):
        d = orchestrator.route_request("how much space is left")
    assert isinstance(d, RoutingDecision)
    assert (d.function, d.user_input, d.domain, d.via_llm) == ("free_space_summary", "how much space is left", "academic", False)


def test_handle_single_passes_the_decision_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(orchestrator, "route_request", lambda text: RoutingDecision("unsupported", text))
    monkeypatch.setattr(orchestrator, "execute", lambda d: (seen.update(d=d), "ok")[1])
    monkeypatch.setattr(orchestrator, "SESSION_HISTORY", [])
    monkeypatch.setattr(orchestrator, "LAST_ROUTING_DECISION", None)  # restored after the test
    assert orchestrator._handle_single("turn on the ac") == "ok"
    assert seen["d"].user_input == "turn on the ac" and orchestrator.LAST_ROUTING_DECISION is seen["d"]
