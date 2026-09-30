"""Wake-word general Q&A mode (Roadmap Item 13).

An always-on lightweight listener that watches an utterance stream for the
wake word, opens a short activation window, and routes what follows to a
handler. Follow-ups inside the window don't need the wake word repeated.

Two boundaries this module deliberately enforces:

1. **Q&A only.** Voice is a low-confidence input channel: speech-to-text
   mishears, and anyone within earshot can speak. So the default handler
   answers questions and nothing else — a voice utterance cannot open an
   application, write a file, or drive a browser. Executing actions stays
   on the typed channel where the tier gate's confirmations are meaningful.

2. **Separate from the security voice channel.** This listener never
   performs speaker verification and must never be used as an authorization
   signal. Voice-print verification lives in `security_module.py` on its own
   channel, by design — hearing the wake word proves someone spoke, not who.

Audio backend status: no capture library is installed in this environment,
so `audio_available()` is False and `listen_forever()` refuses to start.
The utterance-processing core below is backend-independent and fully
exercised by the test suite — it works the same whether utterances arrive
from a microphone transcript or typed text.
"""

from __future__ import annotations

import inspect
import os
import re
import time
from dataclasses import dataclass
from typing import Callable

from zedek_logger import get_logger

log = get_logger("wake_word")

DEFAULT_WAKE_WORD = "zedek"

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
# Local speech-to-text model. Kept offline on purpose — see listen_forever().
VOSK_MODEL_DIR = os.path.join(_PROJECT_ROOT, "models", "vosk-model-small-en-us-0.15")
SAMPLE_RATE = 16_000
BLOCK_SIZE = 8_000

# Speech-to-text reliably mangles an unusual proper noun, so accept close
# variants. These are matched only at the START of an utterance, so ordinary
# sentences containing a similar word don't trip activation.
WAKE_WORD_VARIANTS = [
    "zedek", "zedec", "zedeck", "zedak", "zaydek", "zedik", "sedek", "zed eck",
]

# Phrases that explicitly end the activation window.
SLEEP_PHRASES = ["go to sleep", "stop listening", "never mind", "nevermind", "sleep now", "that's all"]

ACTIVATION_WINDOW_SECONDS = 30

# Outcome statuses returned by process_utterance().
IGNORED = "ignored"
ACTIVATED = "activated"
HANDLED = "handled"
SLEPT = "slept"
REFUSED = "refused"


@dataclass
class ListenResult:
    """What the listener did with one utterance."""

    status: str
    response: str = ""
    command: str = ""
    active: bool = False


def _normalize(text: str) -> str:
    """Lowercase and strip punctuation so STT punctuation noise doesn't matter."""
    return re.sub(r"[^\w\s]", "", (text or "").lower()).strip()


def audio_available() -> bool:
    """Whether a microphone capture backend is importable."""
    try:
        import sounddevice  # noqa: F401
        return True
    except (ImportError, OSError):
        # OSError covers sounddevice importing without libportaudio present.
        try:
            import pyaudio  # noqa: F401
            return True
        except ImportError:
            return False


def stt_available() -> bool:
    """Whether the local speech-to-text engine is importable."""
    try:
        import vosk  # noqa: F401
        return True
    except ImportError:
        return False


def stt_model_available() -> bool:
    """Whether the Vosk acoustic model has been downloaded."""
    return os.path.isdir(VOSK_MODEL_DIR)


def qa_only_handler(question: str, domain: str = "personal", stream=None) -> str:
    """Default handler: answer the question, never execute an action.

    Routes straight to the orchestrator's general-question path, bypassing
    `execute()` entirely — so there is no code path from a spoken utterance
    to a system action. With `stream` (an llm_provider.StreamSink), the answer
    is also delivered as it is generated, so it can be spoken sentence by
    sentence.
    """
    import orchestrator
    token = orchestrator._REPLY_STREAM.set(stream)
    try:
        return orchestrator.answer_general_question(question, domain)
    finally:
        orchestrator._REPLY_STREAM.reset(token)


class WakeWordListener:
    """Watches an utterance stream for the wake word and handles what follows."""

    def __init__(
        self,
        wake_word: str = DEFAULT_WAKE_WORD,
        window_seconds: int = ACTIVATION_WINDOW_SECONDS,
        handler: Callable[[str], str] | None = None,
        speaker=None,
    ) -> None:
        self.wake_word = wake_word.lower()
        self.window_seconds = window_seconds
        self.handler = handler or (lambda q, stream=None: qa_only_handler(q, stream=stream))
        # A speech.Speaker. With one, replies are spoken sentence by sentence as
        # they are generated (ROADMAP D2); without one, behaviour is unchanged.
        self.speaker = speaker
        try:
            self._handler_streams = "stream" in inspect.signature(self.handler).parameters
        except (TypeError, ValueError):
            self._handler_streams = False
        self._activated_at: float = 0.0

    def _speak(self, text: str) -> None:
        if self.speaker is not None and text:
            self.speaker.say(text)

    # ── Activation state ─────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        """Whether the activation window is currently open."""
        if self._activated_at == 0.0:
            return False
        return (time.time() - self._activated_at) <= self.window_seconds

    def activate(self) -> None:
        self._activated_at = time.time()

    def deactivate(self) -> None:
        self._activated_at = 0.0

    # ── Wake-word detection ──────────────────────────────────────────────

    def detect_wake_word(self, text: str) -> bool:
        """Whether the utterance opens with the wake word (or a close variant)."""
        normalized = _normalize(text)
        if not normalized:
            return False
        variants = set(WAKE_WORD_VARIANTS) | {self.wake_word}
        return any(
            normalized == variant or normalized.startswith(variant + " ")
            for variant in variants
        )

    def strip_wake_word(self, text: str) -> str:
        """Remove the leading wake word, returning just the command."""
        normalized = _normalize(text)
        variants = sorted(set(WAKE_WORD_VARIANTS) | {self.wake_word}, key=len, reverse=True)
        for variant in variants:
            if normalized == variant:
                return ""
            if normalized.startswith(variant + " "):
                remainder = normalized[len(variant):].strip()
                # Drop a leading filler comma/word left over from "Zedek, what is…"
                return re.sub(r"^(please|can you|could you)\s+", "", remainder).strip()
        return normalized

    def is_sleep_command(self, text: str) -> bool:
        normalized = _normalize(text)
        return any(phrase in normalized for phrase in SLEEP_PHRASES)

    # ── Core processing ──────────────────────────────────────────────────

    def process_utterance(self, text: str) -> ListenResult:
        """Handle one utterance from the stream.

        Backend-independent: the utterance may come from a speech-to-text
        transcript or from typed text — the logic is identical.
        """
        if not text or not text.strip():
            return ListenResult(IGNORED, active=self.is_active)

        currently_active = self.is_active
        has_wake_word = self.detect_wake_word(text)

        if not currently_active and not has_wake_word:
            # Ordinary room conversation — not addressed to the assistant.
            return ListenResult(IGNORED, active=False)

        if self.is_sleep_command(text):
            self.deactivate()
            log.info("wake_word_deactivated", extra={})
            if self.speaker is not None:
                self.speaker.cancel()
            self._speak("Going quiet.")
            return ListenResult(SLEPT, response="Going quiet. Say the wake word when you need me.", active=False)

        command = self.strip_wake_word(text) if has_wake_word else _normalize(text)

        # Wake word with nothing after it: open the window and wait.
        if not command:
            self.activate()
            log.info("wake_word_activated_awaiting_command", extra={})
            self._speak("Listening.")
            return ListenResult(ACTIVATED, response="Listening.", active=True)

        self.activate()  # each handled utterance refreshes the window
        log.info("wake_word_handling_utterance", extra={"chars": len(command)})

        sink = self.speaker.stream() if self.speaker is not None and self._handler_streams else None
        try:
            response = self.handler(command, stream=sink) if sink is not None else self.handler(command)
        except Exception as err:
            log.info("wake_word_handler_failed", extra={"error_type": type(err).__name__})
            if self.speaker is not None:
                self.speaker.cancel()
            self._speak("Sorry, I couldn't answer that.")
            return ListenResult(REFUSED, response=f"I couldn't answer that: {err}",
                                command=command, active=True)

        if sink is not None:
            sink.finish()
        if sink is None or sink.emitted == 0:
            # Nothing was streamed (no speaker-aware handler, or a non-streaming
            # provider answered): speak the finished reply instead.
            self._speak(response)
        return ListenResult(HANDLED, response=response, command=command, active=True)

    # ── Audio loop (requires a capture backend) ──────────────────────────

    def listen_forever(self, on_result: Callable[[ListenResult], None] | None = None) -> None:
        """Start the always-on microphone loop.

        Transcription runs locally via Vosk rather than a cloud API. That is a
        deliberate choice for an always-on microphone: streaming everything the
        room says to a third party is a very different privacy proposition from
        sending it a typed question, and it would also break whenever the
        network does.

        Refuses to start on any missing prerequisite rather than silently doing
        nothing, so a missing dependency is visible instead of looking like a
        listener that simply never hears anything.
        """
        if not audio_available():
            raise RuntimeError(
                "No microphone capture backend installed. Install `sounddevice` "
                "(or `pyaudio`). The utterance-processing logic is ready and tested — "
                "feed it transcripts via process_utterance() in the meantime."
            )
        if not stt_available():
            raise RuntimeError(
                "Vosk is not installed. Run: pip install vosk"
            )
        if not stt_model_available():
            raise RuntimeError(
                f"No Vosk model found at {VOSK_MODEL_DIR}. Download a small English "
                "model from https://alphacephei.com/vosk/models (vosk-model-small-en-us-0.15) "
                f"and unpack it there."
            )

        import json as _json

        import sounddevice as sd
        from vosk import KaldiRecognizer, Model

        model = Model(VOSK_MODEL_DIR)
        recognizer = KaldiRecognizer(model, SAMPLE_RATE)

        log.info("wake_word_listener_started", extra={"wake_word": self.wake_word})

        with sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=BLOCK_SIZE,
            dtype="int16", channels=1,
        ) as stream:
            while True:
                data, overflowed = stream.read(BLOCK_SIZE)
                if overflowed:
                    log.info("wake_word_audio_overflow", extra={})
                if self.speaker is not None and self.speaker.busy:
                    # Do not listen to Zedek's own voice: inside the activation
                    # window it would be taken as the user's next question.
                    recognizer.Reset()
                    continue
                if not recognizer.AcceptWaveform(bytes(data)):
                    continue

                transcript = _json.loads(recognizer.Result()).get("text", "").strip()
                if not transcript:
                    continue

                result = self.process_utterance(transcript)
                if on_result is not None:
                    on_result(result)
                elif result.status in (HANDLED, ACTIVATED, SLEPT, REFUSED):
                    print(f"Zedek: {result.response}")


if __name__ == "__main__":
    import argparse

    import speech

    parser = argparse.ArgumentParser(description="Zedek wake-word listener (questions only, answered aloud)")
    parser.add_argument("--no-speech", action="store_true", help="print answers instead of speaking them")
    cli_args = parser.parse_args()

    voice = None
    if not cli_args.no_speech:
        if speech.tts_available():
            voice = speech.Speaker()
        else:
            print("No text-to-speech found (install speech-dispatcher for `spd-say`); answers will be printed.")
    print(f"Say '{DEFAULT_WAKE_WORD}' followed by a question. Ctrl+C to stop.")
    try:
        WakeWordListener(speaker=voice).listen_forever(
            on_result=lambda r: print(f"Zedek: {r.response}") if r.response else None)
    except KeyboardInterrupt:
        pass
    except RuntimeError as problem:
        print(problem)
