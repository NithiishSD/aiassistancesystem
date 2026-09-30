"""OPT-IN measurement of two-tier voice recognition (ROADMAP F9).

Synthesizes speech with ffmpeg's flite voices (there is no recorded speech of
the owner in a public repository), runs it through the real Vosk model, and
compares the free-form recognizer alone with the two-tier decision in
voice_commands.decide():

  - commands:  "<wake word> <phrase>" for every grammar phrase
  - questions: "<wake word> <question>", which must stay questions
  - room:      speech without the wake word, which must not wake the listener

Synthetic voices are not a person at a laptop microphone: read the numbers as
a comparison between the two approaches, not as field accuracy. Never part of
pytest (it needs ffmpeg with flite, the Vosk model, and about a minute).

Usage:
    python evals/voice_grammar_eval.py [--voices slt rms awb kal16] [--keep DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_commands as vc  # noqa: E402
import wake_word as ww  # noqa: E402

QUESTIONS = [
    "what is a binary search tree", "explain how quick sort works", "open the calculator",
    "delete all my files", "how does virtual memory work", "what is disk fragmentation",
    "why do computers need ram", "tell me about dynamic programming", "what is the capital of france",
    "how do i reverse a linked list", "what is the difference between a process and a thread",
    "remember that my exam is on friday", "search the web for train tickets", "what is a hash table",
    "how much does a new laptop cost", "which sorting algorithm is the fastest",
    "what should i study today", "is a folder the same as a directory", "how big is the sun",
    "write a python script that prints my name",
    # Close to a command phrase, but a question.
    "how much free disk space does a server need", "why is my memory so bad these days",
    "which folders should i back up first", "what is filling the oceans with plastic",
    "is my disk drive solid state", "how much storage does a phone need",
    "which apps should i learn for design", "where is my space heater", "what's eating my plants",
    "how much room does a piano need",
]
ROOM = [
    "what time is the meeting tomorrow", "i think we should order some food", "the deck is slippery when it rains",
    "said it could be a little later", "my disk is almost full i think", "zodiac signs are a bit silly",
    "did you finish the assignment", "this laptop has so much ram", "which folder did you put it in",
    "he said he would call back", "the dictionary is on the shelf", "is there any space left in the fridge",
    "that movie was really good", "set it back on the table please", "how much free time do you have",
    "she decked the halls already", "the biggest folders are on the top shelf", "stop listening to that song",
    "what is eating the plants in my garden", "let us go for a walk",
    # Added after the first run found "said it ..." and "set it ..." waking the
    # listener: sentences that start with sounds close to the wake word.
    "send it to me when you are done", "said he would be there at six", "that deck of cards is missing a king",
    "the medic arrived quickly", "steady rain all afternoon", "edit the second paragraph",
    "seven decades is a long time", "said a quick goodbye and left", "is that a good idea",
    "zebra crossings are safer", "the dead battery needs replacing", "z is the last letter",
    "they take the bus every day", "set a timer for ten minutes", "ahead of schedule for once",
    "instead of coffee i had tea", "credit cards are accepted here", "i said exactly that",
    "the desk is a mess", "said acknowledge and move on",
]


def synthesize(text: str, voice: str, path: str) -> None:
    if os.path.exists(path):
        return
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"flite=text='{text}':voice={voice}", "-ar", str(ww.SAMPLE_RATE), "-ac", "1", path], check=True)


def words_of(recognizer, path: str) -> tuple[list[dict], float]:
    audio = wave.open(path, "rb")
    started = time.perf_counter()
    while True:
        data = audio.readframes(4000)
        if not data:
            break
        recognizer.AcceptWaveform(data)
    words = json.loads(recognizer.FinalResult()).get("result", [])
    return words, time.perf_counter() - started


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--voices", nargs="+", default=["slt", "rms", "awb", "kal16"])
    parser.add_argument("--keep", help="directory to keep the synthesized audio in")
    parser.add_argument("--verbose", action="store_true", help="print every wrong decision")
    args = parser.parse_args()

    from vosk import KaldiRecognizer, Model, SetLogLevel
    SetLogLevel(-1)
    model = Model(ww.VOSK_MODEL_DIR)
    known = lambda w: model.vosk_model_find_word(w) >= 0  # noqa: E731
    wake = ww.DEFAULT_WAKE_WORD
    phrases = vc.command_phrases(known)
    decoys = [d for d in vc.WAKE_DECOYS if all(known(w) for w in d.split())]
    grammar = json.dumps(vc.build_grammar(phrases, wake, ww.SLEEP_PHRASES, decoys))
    listener = ww.WakeWordListener(handler=lambda q: "")
    folder = args.keep or tempfile.mkdtemp(prefix="zedek-voice-eval-")
    os.makedirs(folder, exist_ok=True)

    sets = {"commands": [f"{wake} {p}" for p in phrases],
            "questions": [f"{wake} {q}" for q in QUESTIONS],
            "room": ROOM}
    print(f"{len(phrases)} grammar phrases, {len(QUESTIONS)} questions, {len(ROOM)} room sentences, "
          f"{len(args.voices)} voices\n")

    free_times, grammar_times = [], []
    totals = {name: {"n": 0, "free_wake": 0, "two_wake": 0, "free_cmd": 0, "two_cmd": 0,
                     "free_right": 0, "two_right": 0, "two_cmd_no_agree": 0} for name in sets}
    for name, sentences in sets.items():
        for index, sentence in enumerate(sentences):
            for voice in args.voices:
                path = os.path.join(folder, f"{name}-{index}-{voice}.wav")
                synthesize(sentence, voice, path)
                free_rec, grammar_rec = KaldiRecognizer(model, ww.SAMPLE_RATE), KaldiRecognizer(
                    model, ww.SAMPLE_RATE, grammar)
                free_rec.SetWords(True)
                grammar_rec.SetWords(True)
                free_words, t_free = words_of(free_rec, path)
                grammar_words, t_grammar = words_of(grammar_rec, path)
                free_times.append(t_free)
                grammar_times.append(t_grammar)

                free_text = " ".join(w["word"] for w in free_words)
                free_wake = listener.detect_wake_word(free_text)
                free_capability = phrases.get(vc.normalize_phrase(listener.strip_wake_word(free_text))) \
                    if free_wake else None
                heard = vc.decide(free_words, grammar_words, phrases, wake, ww.SLEEP_PHRASES)
                two_wake = listener.detect_wake_word(heard.text)
                two_capability = heard.capability if two_wake else None
                saved, vc.MIN_AGREEMENT = vc.MIN_AGREEMENT, 0.0
                loose = vc.decide(free_words, grammar_words, phrases, wake, ww.SLEEP_PHRASES)
                vc.MIN_AGREEMENT = saved

                expected = phrases.get(sentence[len(wake) + 1:]) if name == "commands" else None
                t = totals[name]
                t["n"] += 1
                t["free_wake"] += free_wake
                t["two_wake"] += two_wake
                t["free_cmd"] += free_capability is not None
                t["two_cmd"] += two_capability is not None
                t["two_cmd_no_agree"] += loose.capability is not None
                t["free_right"] += free_capability == expected and (free_wake or name == "room")
                t["two_right"] += two_capability == expected and (two_wake or name == "room")
                wrong = (name == "commands" and two_capability != expected) or \
                        (name != "commands" and two_capability is not None) or (name == "room" and two_wake) or \
                        (name == "questions" and not two_wake)
                if args.verbose and wrong:
                    print(f"  [{name}/{voice}] said: {sentence!r}\n      free:    {free_text!r}\n"
                          f"      grammar: {' '.join(w['word'] for w in grammar_words)!r}\n"
                          f"      heard:   {heard}")

    def row(label: str, key: str, name: str) -> str:
        t = totals[name]
        return f"  {label:<44} {t[key]:>4} / {t['n']}"

    print("COMMANDS (wake word + a grammar phrase)")
    print(row("free-form alone: wake word heard", "free_wake", "commands"))
    print(row("free-form alone: right command", "free_right", "commands"))
    print(row("two-tier: wake word heard", "two_wake", "commands"))
    print(row("two-tier: right command", "two_right", "commands"))
    print("QUESTIONS (wake word + a question; none is a command)")
    print(row("free-form alone: wake word heard", "free_wake", "questions"))
    print(row("two-tier: wake word heard", "two_wake", "questions"))
    print(row("two-tier: wrongly taken as a command", "two_cmd", "questions"))
    print(row("  ... with the agreement check off", "two_cmd_no_agree", "questions"))
    print("ROOM SPEECH (no wake word; must be ignored)")
    print(row("free-form alone: wrongly woke", "free_wake", "room"))
    print(row("two-tier: wrongly woke", "two_wake", "room"))
    print(row("two-tier: a command, agreement check off", "two_cmd_no_agree", "room"))
    print(f"\nDecode time per utterance (median): free-form {statistics.median(free_times) * 1000:.0f} ms, "
          f"grammar {statistics.median(grammar_times) * 1000:.0f} ms")
    if not args.keep:
        for name in os.listdir(folder):
            os.remove(os.path.join(folder, name))
        os.rmdir(folder)


if __name__ == "__main__":
    main()
