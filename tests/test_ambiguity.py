import pytest

from classifier import classify_intent
from orchestrator import should_treat_as_disambiguation, tone_for_prompt


def test_should_treat_as_disambiguation_for_astro():
    previous = "hey i want to study astro would you please tell me what it is"
    current = "no i am talking about astro frontend framework"

    assert should_treat_as_disambiguation(previous, current) is True


def test_tone_for_prompt_is_playful_for_casual_user():
    tone = tone_for_prompt("hey bro i need a quick answer pls")

    assert "fun" in tone.lower() or "playful" in tone.lower() or "casual" in tone.lower()


def test_thank_you_is_not_correct_fact():
    result = classify_intent("okay thank you")

    assert result["function"] is None
    assert result["confidence"] == "high"
