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


def test_unsupported_media_control_is_not_a_fact_correction():
    result = classify_intent("stop the music played in spotify")

    assert result["function"] == "unsupported"
    assert result["confidence"] == "high"


def test_open_application_routes_generic_app_request():
    result = classify_intent("can you open brave application")

    assert result["function"] == "open_application"
    assert result["confidence"] == "high"


def test_close_application_remains_unsupported():
    result = classify_intent("close brave application")

    assert result["function"] == "unsupported"
