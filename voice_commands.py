"""
Two-tier voice recognition (ROADMAP F9).

The same audio goes to two local Vosk recognizers:

  - the free-form recognizer, which can write down any sentence but mishears
    unusual words (the wake word above all), and
  - a recognizer restricted to a grammar: the wake word, the sleep phrases and
    the short router phrases of the capabilities marked `voice: true`.

decide() then picks one reading per utterance. A spoken request becomes a
command only when the grammar recognizer heard one whole known phrase, word for
word, with confidence, and the free-form recognizer heard at least some of
the same words. Everything else stays a question, written down by the free-form
recognizer; the grammar recognizer still supplies the wake word, which it hears
far more reliably.

The phrase table maps each phrase straight to its capability, so a spoken
command needs no router and no model call.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import capabilities
from zedek_logger import get_logger

log = get_logger("voice_commands")

UNKNOWN = "[unk]"
MAX_PHRASE_WORDS = 10          # longer router utterances are descriptions, not things people say
MIN_PHRASE_WORDS = 3           # one or two words are too easy to hear by accident
WORD_CONFIDENCE = 0.9          # every word of a command, and the wake word, must reach this
MIN_AGREEMENT = 0.25           # share of a command's words the free-form recognizer must also hear
WAKE_OVERLAP_SECONDS = 0.15    # a free-form word starting this far before the wake word's end is part of it

# Ordinary words that sound like the wake word. Without them in the grammar,
# the restricted recognizer has nothing closer than the wake word to write for
# "said it ..." or "that deck ...", and the listener wakes up. Measured with
# evals/voice_grammar_eval.py.
WAKE_DECOYS = ("said", "set", "said it", "set it", "edit", "dead", "deck", "the deck", "that", "said he",
               "instead", "steady", "second", "credit", "medic")

_NOT_WORD_RE = re.compile(r"[^a-z' ]+")

Word = dict[str, Any]  # a Vosk word: {"word", "start", "end", "conf"}


def normalize_phrase(text: str) -> str:
    """A phrase as the recognizer writes it: lowercase words, apostrophes kept."""
    return " ".join(_NOT_WORD_RE.sub(" ", (text or "").lower()).split())


def command_phrases(known_word: Callable[[str], bool] | None = None) -> dict[str, str]:
    """Speakable phrase -> capability, from the registry's voice-enabled capabilities.

    A phrase is left out when it is too long or too short, contains something
    that is not a word (digits), contains a word the recognizer does not know,
    or belongs to two capabilities.
    """
    phrases: dict[str, str] = {}
    shared: set[str] = set()
    for name in capabilities.voice_capabilities():
        capability = capabilities.CAPABILITIES[name]
        for utterance in (*capability.utterances, *capability.short_examples):
            if any(ch.isdigit() for ch in utterance):
                continue
            phrase = normalize_phrase(utterance)
            words = phrase.split()
            if not MIN_PHRASE_WORDS <= len(words) <= MAX_PHRASE_WORDS:
                continue
            if known_word is not None and not all(known_word(w) for w in words):
                continue
            if phrases.get(phrase, name) != name:
                shared.add(phrase)
            phrases[phrase] = name
    for phrase in shared:
        del phrases[phrase]
    return phrases


def build_grammar(phrases: Iterable[str], wake_word: str, sleep_phrases: Iterable[str] = (),
                  decoys: Iterable[str] = WAKE_DECOYS) -> list[str]:
    """The phrase list for the restricted recognizer. `[unk]` stands for any other speech."""
    grammar = [wake_word, *sorted(phrases), *(normalize_phrase(p) for p in sleep_phrases),
               *(d for d in decoys if d != wake_word), UNKNOWN]
    return list(dict.fromkeys(g for g in grammar if g))


@dataclass(frozen=True)
class Heard:
    """One utterance, as the two recognizers together understood it."""

    text: str                       # starts with the wake word when it was spoken
    capability: str | None = None   # set only for a recognized spoken command
    source: str = "free"            # which recognizer's words `text` holds


def _confident(words: list[Word]) -> bool:
    return all(w.get("conf", 0.0) >= WORD_CONFIDENCE for w in words)


def _agreement(phrase_words: list[str], free_words: list[Word]) -> float:
    heard = {normalize_phrase(w["word"]) for w in free_words}
    return sum(1 for w in phrase_words if w in heard) / len(phrase_words)


def decide(free_words: list[Word], grammar_words: list[Word], phrases: dict[str, str],
           wake_word: str, sleep_phrases: Iterable[str] = ()) -> Heard:
    """Pick one reading of an utterance from the two recognizers' words."""
    free_text = " ".join(w["word"] for w in free_words)
    if not grammar_words:
        return Heard(free_text)

    has_wake = grammar_words[0]["word"] == wake_word and _confident(grammar_words[:1])
    rest = grammar_words[1:] if has_wake else grammar_words
    rest_words = [w["word"] for w in rest]
    rest_text = " ".join(rest_words)
    prefix = f"{wake_word} " if has_wake else ""

    if rest and UNKNOWN not in rest_words and _confident(rest):
        if rest_text in {normalize_phrase(p) for p in sleep_phrases}:
            return Heard(prefix + rest_text, source="grammar")
        capability = phrases.get(rest_text)
        if capability is not None and _agreement(rest_words, free_words) >= MIN_AGREEMENT:
            return Heard(prefix + rest_text, capability, "grammar")

    if has_wake:
        # The free-form recognizer wrote the question; its (often misheard) wake
        # word is replaced by the one the grammar recognizer heard.
        cut = grammar_words[0].get("end", 0.0) - WAKE_OVERLAP_SECONDS
        question = " ".join(w["word"] for w in free_words if w.get("start", 0.0) >= cut)
        return Heard((prefix + question).strip())
    return Heard(free_text)


class TwoTierRecognizer:
    """Feeds audio to both recognizers and returns a Heard at the end of each utterance.

    `free` and `grammar` are Vosk KaldiRecognizer objects (or stand-ins with
    AcceptWaveform / Result / FinalResult / Reset) created with word details on.
    The free-form recognizer decides where an utterance ends.
    """

    def __init__(self, free, grammar, phrases: dict[str, str], wake_word: str,
                 sleep_phrases: Iterable[str] = ()) -> None:
        self._free = free
        self._grammar = grammar
        self._phrases = phrases
        self._wake_word = wake_word
        self._sleep_phrases = tuple(sleep_phrases)
        self._grammar_words: list[Word] = []

    @staticmethod
    def _words(raw: str) -> list[Word]:
        return json.loads(raw).get("result", [])

    def accept(self, data: bytes) -> Heard | None:
        # The grammar recognizer may end its own segment early; keep its words.
        if self._grammar.AcceptWaveform(data):
            self._grammar_words += self._words(self._grammar.Result())
        if not self._free.AcceptWaveform(data):
            return None
        return self._finish(self._free.Result())

    def flush(self) -> Heard | None:
        """End of audio: whatever both recognizers still hold."""
        return self._finish(self._free.FinalResult())

    def _finish(self, free_raw: str) -> Heard | None:
        free_words = self._words(free_raw)
        grammar_words = self._grammar_words + self._words(self._grammar.FinalResult())
        self._grammar_words = []
        if not free_words and not grammar_words:
            return None
        heard = decide(free_words, grammar_words, self._phrases, self._wake_word, self._sleep_phrases)
        if not heard.text:
            return None
        log.info("voice_utterance_heard", extra={"source": heard.source, "capability": heard.capability,
                                                  "words": len(heard.text.split())})
        return heard

    def reset(self) -> None:
        self._free.Reset()
        self._grammar.Reset()
        self._grammar_words = []


def make_recognizer(model, sample_rate: int, wake_word: str, sleep_phrases: Iterable[str] = ()) -> TwoTierRecognizer:
    """Both Vosk recognizers over one loaded model."""
    from vosk import KaldiRecognizer

    known = lambda word: model.vosk_model_find_word(word) >= 0  # noqa: E731
    phrases = command_phrases(known)
    sleep = [p for p in sleep_phrases if all(known(w) for w in normalize_phrase(p).split())]
    decoys = [d for d in WAKE_DECOYS if all(known(w) for w in d.split())]
    free = KaldiRecognizer(model, sample_rate)
    grammar = KaldiRecognizer(model, sample_rate, json.dumps(build_grammar(phrases, wake_word, sleep, decoys)))
    free.SetWords(True)
    grammar.SetWords(True)
    log.info("voice_grammar_built", extra={"phrases": len(phrases), "sleep_phrases": len(sleep)})
    return TwoTierRecognizer(free, grammar, phrases, wake_word, sleep)
