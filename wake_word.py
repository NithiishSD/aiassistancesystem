"""Wake-word general Q&A mode (Roadmap Item 13).

An always-on lightweight listener that watches an utterance stream for the
wake word, opens a short activation window, and routes what follows to a
handler. Follow-ups inside the window don't need the wake word repeated.

Two boundaries this module deliberately enforces:

1. **Questions, plus a short list of read-only commands.** Voice is a
   low-confidence input channel: speech-to-text mishears, and anyone within
   earshot can speak. So a voice utterance is answered as a question unless it
   is, word for word, a known phrase of a capability marked `voice: true`
   (Tier 0, read-only, no arguments; ROADMAP F9). It cannot open an
   application, write a file, or drive a browser, and nothing spoken can
   approve a confirmation. Those stay on the typed channel where the tier
   gate's confirmations are meaningful.

2. **Separate from the security voice channel.** This listener never
   performs speaker verification and must never be used as an authorization
   signal. Voice-print verification lives in `security_module.py` on its own
   channel, by design — hearing the wake word proves someone spoke, not who.

The utterance-processing core below is backend-independent and fully
exercised by the test suite — it works the same whether utterances arrive
from a microphone transcript or typed text. `listen_forever()` refuses to
start when a prerequisite (capture library, Vosk, its model) is missing.
"""

from __future__ import annotations

import inspect
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import voice_commands
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

# Spoken exchanges kept for follow-up questions while the window is open. They
# are never written to the session buffer or long-term memory: a misheard
# sentence must not become a stored fact.
RECENT_VOICE_EXCHANGES = 2
MAX_RECENT_REPLY_CHARS = 600

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


def qa_only_handler(question: str, domain: str = "personal", stream=None, recent=()) -> str:
    """Default handler: answer the question, never execute an action.

    Routes straight to the orchestrator's general-question path, bypassing
    `execute()` entirely — so there is no code path from a spoken question
    to a system action. With `stream` (an llm_provider.StreamSink), the answer
    is also delivered as it is generated, so it can be spoken sentence by
    sentence. `recent` holds the last spoken exchanges, for follow-ups.
    """
    import orchestrator
    stream_token = orchestrator._REPLY_STREAM.set(stream)
    turns_token = orchestrator._CHANNEL_TURNS.set(tuple(recent))
    try:
        return orchestrator.answer_general_question(question, domain)
    finally:
        orchestrator._CHANNEL_TURNS.reset(turns_token)
        orchestrator._REPLY_STREAM.reset(stream_token)


VOICE_COMMAND_REFUSED = "I can only do that when you type it."


def run_voice_command(capability: str, text: str) -> str:
    """Default command handler: run one voice-enabled capability with its default arguments.

    The registry only lets a Tier 0 system_agent function be voice-enabled, and
    that is re-checked here. The call still goes through `execute()` and the
    tier gate, under a channel that refuses every confirmation: there is no
    spoken "yes".
    """
    import capabilities
    import confirmation
    import orchestrator
    from routing_decision import RoutingDecision

    definition = capabilities.CAPABILITIES.get(capability)
    if definition is None or not definition.voice or definition.tier != 0:
        log.info("voice_command_refused", extra={"capability": capability})
        return VOICE_COMMAND_REFUSED
    decision = RoutingDecision(function=capability, user_input=text, score=1.0)
    with confirmation.use_channel(confirmation.DenyChannel()):
        return orchestrator.execute(decision)


_PHRASES: dict[str, str] | None = None


def _command_phrases() -> dict[str, str]:
    global _PHRASES
    if _PHRASES is None:
        # Keyed the way process_utterance() normalizes text (no apostrophes).
        _PHRASES = {_normalize(p): c for p, c in voice_commands.command_phrases().items()}
    return _PHRASES


class WakeWordListener:
    """Watches an utterance stream for the wake word and handles what follows."""

    def __init__(
        self,
        wake_word: str = DEFAULT_WAKE_WORD,
        window_seconds: int = ACTIVATION_WINDOW_SECONDS,
        handler: Callable[[str], str] | None = None,
        speaker=None,
        command_handler: Callable[[str, str], str] | None = None,
    ) -> None:
        self.wake_word = wake_word.lower()
        self.window_seconds = window_seconds
        self.handler = handler or (
            lambda q, stream=None, recent=(): qa_only_handler(q, stream=stream, recent=recent))
        # Runs a recognized spoken command: (capability, spoken text) -> reply.
        self.command_handler = command_handler or run_voice_command
        # A speech.Speaker. With one, replies are spoken sentence by sentence as
        # they are generated (ROADMAP D2); without one, behaviour is unchanged.
        self.speaker = speaker
        try:
            parameters = inspect.signature(self.handler).parameters
        except (TypeError, ValueError):
            parameters = {}
        self._handler_streams = "stream" in parameters
        self._handler_takes_recent = "recent" in parameters
        self._activated_at: float = 0.0
        self._recent: deque[dict] = deque(maxlen=2 * RECENT_VOICE_EXCHANGES)

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
        self._recent.clear()

    def _remember_exchange(self, said: str, reply: str) -> None:
        self._recent.append({"role": "user", "content": said})
        self._recent.append({"role": "assistant", "content": (reply or "")[:MAX_RECENT_REPLY_CHARS]})

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

    def process_utterance(self, text: str, capability: str | None = None) -> ListenResult:
        """Handle one utterance from the stream.

        Backend-independent: the utterance may come from a speech-to-text
        transcript or from typed text — the logic is identical. `capability`
        is set when the two-tier recognizer heard a spoken command; without
        it, an utterance that is exactly a known command phrase counts too.
        """
        if not text or not text.strip():
            return ListenResult(IGNORED, active=self.is_active)

        currently_active = self.is_active
        if not currently_active:
            self._recent.clear()  # the window closed: earlier exchanges are over
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

        capability = capability or _command_phrases().get(_normalize(command))
        if capability is not None:
            return self._run_command(capability, command)

        sink = self.speaker.stream() if self.speaker is not None and self._handler_streams else None
        kwargs = {}
        if sink is not None:
            kwargs["stream"] = sink
        if self._handler_takes_recent:
            kwargs["recent"] = tuple(self._recent)
        try:
            response = self.handler(command, **kwargs)
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
        self._remember_exchange(command, response)
        self.activate()  # the window runs from the reply, not from a slow answer's start
        return ListenResult(HANDLED, response=response, command=command, active=True)

    def _run_command(self, capability: str, command: str) -> ListenResult:
        """A recognized spoken command: no router and no model call."""
        log.info("wake_word_running_command", extra={"capability": capability})
        try:
            response = self.command_handler(capability, command)
        except Exception as err:
            log.info("wake_word_command_failed", extra={"capability": capability,
                                                        "error_type": type(err).__name__})
            self._speak("Sorry, that didn't work.")
            return ListenResult(REFUSED, response=f"That didn't work: {err}", command=command, active=True)
        self._speak(response)
        self._remember_exchange(command, response)
        self.activate()
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

        import sounddevice as sd
        from vosk import Model

        # Two recognizers over one model: free-form for questions, and one
        # limited to the wake word and the command phrases (ROADMAP F9).
        recognizer = voice_commands.make_recognizer(Model(VOSK_MODEL_DIR), SAMPLE_RATE, self.wake_word, SLEEP_PHRASES)

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
                    recognizer.reset()
                    continue
                heard = recognizer.accept(bytes(data))
                if heard is None:
                    continue

                result = self.process_utterance(heard.text, capability=heard.capability)
                if on_result is not None:
                    on_result(result)
                elif result.status in (HANDLED, ACTIVATED, SLEPT, REFUSED):
                    print(f"Zedek: {result.response}")


if __name__ == "__main__":
    import argparse

    import speech

    parser = argparse.ArgumentParser(description="Zedek wake-word listener (questions and a few read-only commands, answered aloud)")
    parser.add_argument("--no-speech", action="store_true", help="print answers instead of speaking them")
    cli_args = parser.parse_args()

    voice = None
    if not cli_args.no_speech:
        if speech.tts_available():
            voice = speech.Speaker()
        else:
            print("No text-to-speech found (install speech-dispatcher for `spd-say`); answers will be printed.")
    print(f"Say '{DEFAULT_WAKE_WORD}' followed by a question, or a command such as "
          f"'{DEFAULT_WAKE_WORD} how much free disk space do I have'. Ctrl+C to stop.")
    try:
        WakeWordListener(speaker=voice).listen_forever(
            on_result=lambda r: print(f"Zedek: {r.response}") if r.response else None)
    except KeyboardInterrupt:
        pass
    except RuntimeError as problem:
        print(problem)
