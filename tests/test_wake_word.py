"""
Unit tests for the wake-word listener (Roadmap Item 13).

The important properties:
- Ordinary room conversation is ignored; only the wake word activates.
- The activation window opens, refreshes, expires, and can be closed by voice.
- STT mangling of the wake word still activates (variant matching).
- The default handler is Q&A-only — there is NO path from a spoken utterance
  to orchestrator.execute(), so voice cannot trigger a system action.
"""

import time
from unittest.mock import patch

import pytest

import wake_word
from wake_word import (
    ACTIVATED,
    HANDLED,
    IGNORED,
    REFUSED,
    SLEPT,
    WakeWordListener,
)


def _echo_handler(command: str) -> str:
    return f"answered: {command}"


@pytest.fixture
def listener():
    return WakeWordListener(handler=_echo_handler)


class TestWakeWordDetection:
    def test_detects_plain_wake_word(self, listener):
        assert listener.detect_wake_word("zedek") is True

    def test_detects_wake_word_with_command(self, listener):
        assert listener.detect_wake_word("zedek what is a binary tree") is True

    def test_detects_stt_variants(self, listener):
        for variant in ["zedeck", "zedak", "sedek", "zedec"]:
            assert listener.detect_wake_word(f"{variant} hello") is True, variant

    def test_ignores_wake_word_mid_sentence(self, listener):
        """Only a leading wake word activates — otherwise every mention would."""
        assert listener.detect_wake_word("I was telling Bob about zedek yesterday") is False

    def test_ignores_unrelated_speech(self, listener):
        assert listener.detect_wake_word("what time is the meeting") is False

    def test_punctuation_tolerated(self, listener):
        assert listener.detect_wake_word("Zedek, what is recursion?") is True

    def test_empty_text_not_detected(self, listener):
        assert listener.detect_wake_word("") is False
        assert listener.detect_wake_word(None) is False


class TestStripWakeWord:
    def test_strips_leading_wake_word(self, listener):
        assert listener.strip_wake_word("zedek what is recursion") == "what is recursion"

    def test_bare_wake_word_yields_empty_command(self, listener):
        assert listener.strip_wake_word("zedek") == ""

    def test_strips_politeness_filler(self, listener):
        assert listener.strip_wake_word("zedek can you explain pointers") == "explain pointers"

    def test_strips_variant_spelling(self, listener):
        assert listener.strip_wake_word("zedeck tell me a fact") == "tell me a fact"


class TestActivationWindow:
    def test_starts_inactive(self, listener):
        assert listener.is_active is False

    def test_bare_wake_word_activates_and_waits(self, listener):
        result = listener.process_utterance("zedek")
        assert result.status == ACTIVATED
        assert result.active is True

    def test_speech_without_wake_word_ignored_when_inactive(self, listener):
        result = listener.process_utterance("so anyway I told him it was fine")
        assert result.status == IGNORED
        assert result.active is False

    def test_followup_handled_without_wake_word_while_active(self, listener):
        listener.process_utterance("zedek what is recursion")
        result = listener.process_utterance("and what about iteration")
        assert result.status == HANDLED
        assert "iteration" in result.command

    def test_window_expires(self):
        listener = WakeWordListener(window_seconds=0, handler=_echo_handler)
        listener.process_utterance("zedek what is recursion")
        time.sleep(0.01)
        assert listener.is_active is False
        result = listener.process_utterance("follow up question")
        assert result.status == IGNORED

    def test_handled_utterance_refreshes_window(self, listener):
        listener.process_utterance("zedek first question")
        first = listener._activated_at
        time.sleep(0.01)
        listener.process_utterance("second question")
        assert listener._activated_at > first

    def test_sleep_phrase_deactivates(self, listener):
        listener.process_utterance("zedek what is recursion")
        result = listener.process_utterance("stop listening")
        assert result.status == SLEPT
        assert listener.is_active is False

    def test_sleep_phrase_ignored_when_inactive(self, listener):
        result = listener.process_utterance("never mind")
        assert result.status == IGNORED

    def test_empty_utterance_ignored(self, listener):
        assert listener.process_utterance("   ").status == IGNORED


class TestHandling:
    def test_command_passed_to_handler(self, listener):
        result = listener.process_utterance("zedek what is a binary tree")
        assert result.status == HANDLED
        assert result.response == "answered: what is a binary tree"

    def test_handler_exception_is_contained(self):
        def boom(_command):
            raise RuntimeError("provider down")

        listener = WakeWordListener(handler=boom)
        result = listener.process_utterance("zedek explain this")
        assert result.status == REFUSED
        assert "provider down" in result.response
        # A failed answer must not close the window — the user can just retry.
        assert result.active is True


class TestQaOnlyBoundary:
    def test_default_handler_uses_general_qa_not_execute(self):
        """The critical security boundary: voice cannot run actions."""
        listener = WakeWordListener()

        with patch("orchestrator.answer_general_question", return_value="an answer") as mock_qa, \
             patch("orchestrator.execute") as mock_execute:
            result = listener.process_utterance("zedek delete all my files")

        assert result.status == HANDLED
        mock_qa.assert_called_once()
        mock_execute.assert_not_called()

    def test_action_phrasing_still_only_reaches_qa(self):
        listener = WakeWordListener()

        with patch("orchestrator.answer_general_question", return_value="I can't do that by voice") as mock_qa, \
             patch("orchestrator.execute") as mock_execute, \
             patch("orchestrator.handle") as mock_handle:
            listener.process_utterance("zedek open my browser and pay the bill")

        mock_qa.assert_called_once()
        mock_execute.assert_not_called()
        mock_handle.assert_not_called()


class TestAudioBackend:
    """Every missing prerequisite must refuse loudly, never silently no-op.

    These patch the availability probes rather than reading the real
    environment, so the fail-closed contract is pinned down whether or not
    the audio stack happens to be installed on this machine.
    """

    def test_refuses_without_capture_backend(self, listener, monkeypatch):
        monkeypatch.setattr(wake_word, "audio_available", lambda: False)
        with pytest.raises(RuntimeError, match="capture backend"):
            listener.listen_forever()

    def test_refuses_without_stt_engine(self, listener, monkeypatch):
        monkeypatch.setattr(wake_word, "audio_available", lambda: True)
        monkeypatch.setattr(wake_word, "stt_available", lambda: False)
        with pytest.raises(RuntimeError, match="[Vv]osk is not installed"):
            listener.listen_forever()

    def test_refuses_without_stt_model(self, listener, monkeypatch):
        monkeypatch.setattr(wake_word, "audio_available", lambda: True)
        monkeypatch.setattr(wake_word, "stt_available", lambda: True)
        monkeypatch.setattr(wake_word, "stt_model_available", lambda: False)
        with pytest.raises(RuntimeError, match="No Vosk model found"):
            listener.listen_forever()

    def test_availability_probes_return_booleans(self):
        assert isinstance(wake_word.audio_available(), bool)
        assert isinstance(wake_word.stt_available(), bool)
        assert isinstance(wake_word.stt_model_available(), bool)
