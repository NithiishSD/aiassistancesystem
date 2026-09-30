"""
Speaking answers as they are generated (ROADMAP D2, voice path).

A reply is split at sentence boundaries while it streams, and each sentence
goes to text-to-speech as soon as it is complete, so speech starts after the
first sentence instead of after the whole answer.

Three pieces:
  - SentenceStream: an llm_provider.StreamSink that turns streamed text into
    whole sentences.
  - Speaker: a queue and one worker thread that speaks sentences in order and
    knows when it is still speaking (the microphone loop must not listen to
    Zedek's own voice).
  - A backend: speech-dispatcher's `spd-say`, which ships with most Linux
    desktops. Local; nothing is sent anywhere.
"""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import threading
from typing import Callable

from zedek_logger import get_logger

log = get_logger("speech")

MIN_SENTENCE_CHARS = 12      # shorter pieces are held and joined to the next sentence
MAX_SENTENCE_CHARS = 300     # a run-on with no full stop is cut at a comma or space
SPEAK_TIMEOUT_SECONDS = 60

# A sentence ends at . ! ? followed by whitespace, or at a line break. Full stops
# inside numbers ("3.5") or followed by a letter ("e.g.x", "example.com") are
# not followed by whitespace, so they do not match.
_BOUNDARY_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n+")
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.", "no.", "fig.", "approx.")

_URL_RE = re.compile(r"https?://\S+")
_MARKDOWN_RE = re.compile(r"[*_`#>|]+")
_BULLET_RE = re.compile(r"^\s*(?:[-•]|\d+[.)])\s+", re.MULTILINE)


def speakable(text: str) -> str:
    """Text as it should be spoken: no markdown symbols, no spelled-out URLs."""
    text = _URL_RE.sub("a link", text or "")
    text = _BULLET_RE.sub("", text)
    text = _MARKDOWN_RE.sub("", text)
    return " ".join(text.split())


class SentenceStream:
    """StreamSink that calls `on_sentence` once per complete sentence."""

    def __init__(self, on_sentence: Callable[[str], None], on_restart: Callable[[], None] | None = None) -> None:
        self._on_sentence = on_sentence
        self._on_restart = on_restart
        self._buffer = ""
        self.emitted = 0

    def _emit(self, sentence: str) -> None:
        spoken = speakable(sentence)
        if spoken:
            self.emitted += 1
            self._on_sentence(spoken)

    def delta(self, text: str) -> None:
        self._buffer += text
        while True:
            cut = self._next_cut()
            if cut is None:
                return
            sentence, self._buffer = self._buffer[:cut], self._buffer[cut:]
            self._emit(sentence)

    def _next_cut(self) -> int | None:
        for match in _BOUNDARY_RE.finditer(self._buffer):
            head = self._buffer[:match.start()].rstrip()
            if len(head) < MIN_SENTENCE_CHARS or head.lower().endswith(_ABBREVIATIONS):
                continue
            return match.end()
        if len(self._buffer) > MAX_SENTENCE_CHARS:
            window = self._buffer[:MAX_SENTENCE_CHARS]
            return max(window.rfind(", ") + 2, window.rfind(" ") + 1) or MAX_SENTENCE_CHARS
        return None

    def restart(self) -> None:
        """A provider failed mid-answer and another is starting over."""
        self._buffer = ""
        if self._on_restart is not None:
            self._on_restart()

    def finish(self) -> None:
        """The reply is complete: speak whatever is left."""
        remainder, self._buffer = self._buffer, ""
        self._emit(remainder)


# ── Backend ──────────────────────────────────────────────────────────────────

def _spd_say() -> str | None:
    return shutil.which("spd-say")


def tts_available() -> bool:
    return _spd_say() is not None


def _speak_blocking(text: str) -> None:
    """Speak one sentence and return when it has been spoken."""
    binary = _spd_say()
    if binary is None:
        return
    # `--` ends option parsing, so a sentence starting with "-" is still text.
    subprocess.run([binary, "--wait", "--", text], check=False, timeout=SPEAK_TIMEOUT_SECONDS,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _cancel_backend() -> None:
    binary = _spd_say()
    if binary is not None:
        subprocess.run([binary, "--cancel"], check=False, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Speaker:
    """Speaks queued sentences in order on one worker thread."""

    def __init__(self, speak_fn: Callable[[str], None] | None = None,
                 cancel_fn: Callable[[], None] | None = None) -> None:
        self._speak = speak_fn or _speak_blocking
        self._cancel = cancel_fn or _cancel_backend
        self._queue: queue.Queue[str] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._worker = threading.Thread(target=self._run, name="zedek-speaker", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            text = self._queue.get()
            try:
                self._speak(text)
            except Exception as error:  # a TTS failure must never end the listener
                log.info("speech_failed", extra={"error_type": type(error).__name__})
            finally:
                with self._lock:
                    self._pending -= 1
                    if self._pending == 0:
                        self._idle.set()

    def say(self, text: str) -> None:
        text = speakable(text)
        if not text:
            return
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._queue.put(text)

    @property
    def busy(self) -> bool:
        """True while anything is queued or being spoken."""
        return not self._idle.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    def cancel(self) -> None:
        """Drop everything not yet spoken and stop the current sentence."""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        with self._lock:
            self._pending -= dropped
            if self._pending == 0:
                self._idle.set()
        try:
            self._cancel()
        except Exception as error:
            log.info("speech_cancel_failed", extra={"error_type": type(error).__name__})

    def stream(self) -> SentenceStream:
        """A sink that speaks a streamed reply sentence by sentence."""
        return SentenceStream(self.say, on_restart=self.cancel)
