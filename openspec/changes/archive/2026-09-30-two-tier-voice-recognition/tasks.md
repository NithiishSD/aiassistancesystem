# Tasks

## 1. Registry

- [x] 1.1 Add `voice` to the capability definition, refused unless Tier 0 and run by `_run_native_function`; mark `free_space_summary` and `top_memory_processes`; verify with tests for each refused case and a test that pins the voice-enabled set

## 2. Recognition

- [x] 2.1 Create `voice_commands.py` (`command_phrases`, `build_grammar` with wake-word decoys, `decide`, `TwoTierRecognizer`, `make_recognizer`); verify with new `tests/test_voice_commands.py`: phrase limits, vocabulary filter, whole-phrase rule, unknown-speech marker, mixed phrases, confidence, agreement, wake word taken from the grammar recognizer, decoys, sleep phrases, early grammar segments
- [x] 2.2 Write `evals/voice_grammar_eval.py` (synthesized speech, free-form alone vs two-tier) and choose the confidence, agreement and decoy settings from its output

## 3. Listener

- [x] 3.1 `WakeWordListener`: run a recognized command through `run_voice_command` (execute + tier gate, confirmations refused, default arguments, no model call); use the two-tier recognizer in `listen_forever`; verify with tests, including every non-voice capability being refused
- [x] 3.2 Keep the last two spoken exchanges for follow-ups (`orchestrator._CHANNEL_TURNS`), never in the session buffer; count the activation window from the reply; verify with tests

## 4. Integrate

- [x] 4.1 Live check: synthesized audio through both real recognizers, the listener and the real `execute()`; record what was heard and how long each command took
- [x] 4.2 Update `ROADMAP.md` F9 and `TECH_STACK.md`; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
