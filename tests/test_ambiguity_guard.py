"""The clarifying-question guard asks only about bare ambiguous topics
(OpenSpec change: fix-ambiguity-guard)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evals"))

import routing_eval as rev  # noqa: E402
from orchestrator import should_ask_ambiguous_term_question as ask  # noqa: E402


@pytest.mark.parametrize("text", [
    "tell me about python", "what is rust", "explain astro", "what's swift?", "python",
    "i want to know about go", "spark", "what does java mean", "hey what is ruby",
])
def test_bare_topic_asks(text):
    assert ask(text) is True


@pytest.mark.parametrize("text", [
    "port this javascript to typescript",          # the A4 follow-up
    "write a python function to reverse a linked list",
    "is docker installed",
    "what is a binary tree",
    "memory usage by process please",
    "i want to know about go-kart racing",
])
def test_topic_with_context_does_not_ask(text):
    assert ask(text) is False


def test_correction_words_match_whole_words_only():
    # "no" must not match inside "know"; a real correction still skips the question.
    assert ask("i want to know about go") is True
    assert ask("no, python") is False


@pytest.mark.parametrize("split", ["dev", "test"])
def test_no_golden_request_gets_a_clarifying_question(split):
    # Every golden row is a clear request; was 37/384 before this change.
    rows = rev.load_golden(split=split)
    assert [r["utterance"] for r in rows if ask(r["utterance"])] == []
