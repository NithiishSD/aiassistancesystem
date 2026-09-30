# Proposal

Implements ROADMAP F9 (two-tier voice recognition).

## Why

The listener used one free-form Vosk recognizer for everything. It mishears unusual words, and the
wake word most of all: on synthesized speech in four voices it wrote the wake word correctly in 13
of 184 utterances that began with it ("she addict", "zodiac", "derek"), so the listener would not
have woken. It also could not run anything: every spoken request was a question, so "how much free
disk space do I have" went to a language model that cannot know.

## What Changes

- New `voice_commands.py`. The same audio goes to two local recognizers over one Vosk model: the
  free-form one, and one restricted to a grammar (the wake word, the sleep phrases, the command
  phrases, a few decoy words that sound like the wake word, and a marker for other speech).
  `decide()` picks one reading per utterance:
  - a **command**, only when the restricted recognizer heard one whole command phrase, every word
    at confidence 0.9 or more, no other-speech marker, and the free-form recognizer heard at least
    a quarter of the same words;
  - otherwise a **question** in the free-form recognizer's words, with the wake word taken from the
    restricted recognizer when it heard it first.
- Command phrases come from the capability registry: a new `voice: true` flag, which loading
  refuses unless the capability is Tier 0 and run by the gated native handler. Enabled for
  `free_space_summary` and `top_memory_processes`; the set is pinned by a test. Phrases are the
  router utterances of 3 to 10 words without digits that the recognizer's vocabulary covers
  (16 phrases today).
- A spoken command maps straight to its capability: no router, no model call, default arguments.
  It runs through `execute()` and the tier gate under the deny-all confirmation channel.
- The last two spoken exchanges are given to question answering as recent conversation while the
  activation window is open, so "is that a lot" works after a command. They are never written to
  the session buffer or long-term memory.
- The activation window is now counted from the reply, not from the start of a slow answer.
- `evals/voice_grammar_eval.py` measures free-form alone against two-tier on synthesized speech.

## Measured

`evals/voice_grammar_eval.py`, flite voices slt / rms / awb / kal16 through the real Vosk model.
Synthetic voices are not a person at a laptop microphone; read these as a comparison.

| | free-form alone | two-tier |
|---|---|---|
| Commands: wake word heard | 9 / 64 | 64 / 64 |
| Commands: right capability | 3 / 64 | 64 / 64 |
| Questions: wake word heard | 4 / 120 | 109 / 120 |
| Questions wrongly run as a command (10 are near-copies of a phrase) | – | 0 / 120 |
| Room speech that wrongly woke the listener | 0 / 160 | 1 / 160 |
| Decode time per utterance (median) | 575 ms | +49 ms |

- Settings chosen from the eval. The agreement check at 0.5 lost 5 of 100 commands and caught
  nothing; at 0.25 it costs nothing measured and still refuses a phrase the free-form recognizer
  heard none of. Without decoys, 8 of 160 room clips woke the listener ("said it …", "set it …",
  "that deck …"); with them, 1 to 2. Half of the room sentences were written to sound like the
  wake word, and the first decoys were added after seeing the first failures, so the room figure
  is not a field false-wake rate.
- End to end (audio, both recognizers, listener, real `execute()`): "zedek is my disk full" gave
  the real free-space report in 1 ms after recognition; "is that a lot" then reached question
  answering with the command and its result as context; "zedek open the calculator" stayed a
  question.
- `disk_usage_by_folder` was tried and left out: it took 56 s, longer than the activation window,
  and it reads home-folder names aloud.
- Found: when the voice changes between two utterances in one stream, the second can be misheard
  (the recognizer adapts to the speaker). One voice in a row works.

## Non-goals

- No action by voice. Opening applications, files, the browser, memory changes and anything at
  Tier 1 or above stay typed. There is no spoken "yes": confirmations are refused.
- No spoken arguments ("top five", a folder name). Commands run with defaults.
- No learned or user-added phrases in the grammar; only the reviewed registry phrases.
- No new dependency, no cloud speech service, no larger Vosk model.
- No measurement with a human voice; that needs the owner at the microphone.

## Capabilities

### New Capabilities
- `voice-commands`: how a spoken request is recognized as a command or a question, and what a spoken command may do.

### Modified Capabilities
- `voice-replies`: "spoken requests remain questions only" now excepts recognized voice-enabled commands.
- `capability-registry`: the `voice` flag and its restriction.

## Impact

- Code: new `voice_commands.py`, `evals/voice_grammar_eval.py`, `tests/test_voice_commands.py`;
  `wake_word.py`; `capabilities/__init__.py` and two capability files; `orchestrator.py` (one
  context variable read by general Q&A).
- Tier gate: spoken commands pass through it unchanged. **This relaxes the earlier rule that no
  spoken request reaches `execute()`**: two Tier 0, read-only, argument-free functions now can.
  The registry, `run_voice_command` and a pinned test each enforce that limit.
- Watchdog: none.
- Untrusted-input path: speech is untrusted (anyone in the room, and mishearing). It can select
  only one of 16 fixed phrases; no spoken text becomes an argument. A question still goes only to
  question answering. Recent spoken exchanges enter the question-answering prompt as conversation
  turns, as typed turns already do, and are not stored.
- Privacy: free disk space and the names of the largest running programs are spoken aloud.
