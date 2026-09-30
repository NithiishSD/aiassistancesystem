# Tasks

## 1. Speech

- [x] 1.1 Create `speech.py` (`speakable`, `SentenceStream`, `Speaker`, `spd-say` backend); verify with new `tests/test_speech.py`: sentence emitted before the reply ends, numbers/domains/abbreviations, line breaks, run-on cut, markdown and URLs, restart, ordered speaking, busy flag, cancel, backend failure, and the argument form of the backend command

## 2. Listener

- [x] 2.1 `WakeWordListener(speaker=...)` streams the reply to the speaker, speaks unstreamed replies whole, cancels on failure and sleep, and leaves behaviour unchanged without a speaker; verify tests for each
- [x] 2.2 Discard microphone audio while the speaker is busy and add the `python wake_word.py` entry point; verify by reading the loop and by a test that the default handler streams through general Q&A and never calls `execute`

## 3. Integrate

- [x] 3.1 Live checks: `spd-say --wait --` accepts a sentence starting with a dash; time to first sentence vs whole answer through the real pipeline; record the numbers
- [x] 3.2 Update `ROADMAP.md` D2; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
