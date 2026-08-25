import pytest

from classifier import classify_intent
from orchestrator import should_ask_ambiguous_term_question, should_treat_as_disambiguation, tone_for_prompt


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


def test_meta_question_about_astro_is_not_ambiguity_request():
    question = "you confused astro with so many words like that what are other words you would get confused"

    assert should_ask_ambiguous_term_question(question) is False


def test_correction_not_blocked_by_ambiguous_term():
    text = "no you mistook astro frontend framework is not part of datastructure course and also there is no course of datastructure that i am currently learning so remove that from your memory"
    assert should_ask_ambiguous_term_question(text) is False


def test_session_flush_preserves_recent_turns(monkeypatch):
    import orchestrator
    orchestrator.SESSION_HISTORY = [
        {"role": "user", "content": f"msg {i}"} for i in range(12)
    ]
    monkeypatch.setattr("orchestrator.ollama.chat", lambda **kwargs: {"message": {"content": "NONE"}})
    orchestrator.summarize_and_flush_session(keep_recent=4)
    assert len(orchestrator.SESSION_HISTORY) == 4
    assert orchestrator.SESSION_HISTORY[-1]["content"] == "msg 11"

