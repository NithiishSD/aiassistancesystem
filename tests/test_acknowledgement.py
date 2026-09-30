"""Whole-word acknowledgement check (OpenSpec change: fix-acknowledgement-guard).

The old check matched substrings, so "type", "capacity", "typescript",
"facebook" and "book" read as "ty"/"ok", and any message containing "thanks"
was swallowed. Those requests skipped both the router and the LLM. The
misfire list below is exactly what the first routing baseline surfaced.
"""

from unittest.mock import patch

import pytest

import classifier
import orchestrator

ACKNOWLEDGEMENTS = [
    "okay thank you", "thanks a lot bro", "ok got it", "cool, thanks zedek", "ty",
    "sounds good", "Thanks!", "ok", "got it", "thank you so much",
    # Newly recognized on purpose (design D1): pure acknowledgements the old check missed.
    "noted", "cheers", "sounds good bro",
]

NOT_ACKNOWLEDGEMENTS = [
    # Misfires found by the routing baseline.
    "type hello world into the textbox on example.com",
    "disk capacity and free space please",
    "port this javascript to typescript",
    "open facebook",
    "book a movie ticket",
    "how empty is my disk",
    "you got it wrong, my exam is on tuesday",
    "type my query into the form on example.org",
    "look up the research on multitasking and productivity",
    # A request that also says thanks.
    "thanks, now find my resume file",
    "got it. what's my battery percentage",
    # Conversational replies that must reach routing with context.
    "cool", "yes", "fine",
    # Contains "got" but is not "got it".
    "i forgot my password file location",
]


@pytest.mark.parametrize("text", ACKNOWLEDGEMENTS)
def test_pure_acknowledgements_recognized(text):
    assert classifier.is_acknowledgement(text) is True


@pytest.mark.parametrize("text", NOT_ACKNOWLEDGEMENTS)
def test_requests_are_not_acknowledgements(text):
    assert classifier.is_acknowledgement(text) is False


def test_empty_and_none():
    assert classifier.is_acknowledgement("") is False
    assert classifier.is_acknowledgement(None) is False
    assert classifier.is_acknowledgement("   !!! ") is False


def test_overlong_polite_message_routes_normally():
    assert classifier.is_acknowledgement("ok ok thanks thanks so so much much really") is False


class TestOrchestratorUsesSharedCheck:
    def test_request_containing_ty_reaches_classifier(self):
        with patch.object(orchestrator.classifier, "classify_intent",
                          return_value={"function": "coding_task", "confidence": "high",
                                        "score": 0.9, "via_llm": False}) as mock_classify, \
             patch.object(orchestrator.classifier, "classify_domain", return_value="personal"), \
             patch.object(orchestrator, "should_ask_ambiguous_term_question", return_value=False), \
             patch.object(orchestrator, "_extract_args", return_value={}):
            # "port" is also an ambiguous term, which would stop routing for a
            # clarifying question; isolate the acknowledgement guard under test.
            decision = orchestrator.route_request("port this javascript to typescript")
        mock_classify.assert_called_once()
        assert decision.function == "coding_task"

    def test_pure_acknowledgement_short_circuits(self):
        with patch.object(orchestrator.classifier, "classify_intent") as mock_classify, \
             patch.object(orchestrator.classifier, "classify_domain", return_value="personal"):
            decision = orchestrator.route_request("okay thank you")
        mock_classify.assert_not_called()
        assert decision.function is None

    @pytest.mark.parametrize("text", ACKNOWLEDGEMENTS + NOT_ACKNOWLEDGEMENTS)
    def test_both_entry_points_agree(self, text):
        with patch.object(orchestrator.classifier, "classify_intent",
                          return_value={"function": "sentinel", "confidence": "high",
                                        "score": 0.9, "via_llm": False}) as mock_classify, \
             patch.object(orchestrator.classifier, "classify_domain", return_value="personal"), \
             patch.object(orchestrator, "should_ask_ambiguous_term_question", return_value=False), \
             patch.object(orchestrator, "_extract_args", return_value={}):
            orchestrator.route_request(text)
        short_circuited_by_orchestrator = not mock_classify.called
        assert short_circuited_by_orchestrator == classifier.is_acknowledgement(text)
