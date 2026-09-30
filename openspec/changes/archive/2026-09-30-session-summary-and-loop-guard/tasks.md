# Tasks

## 1. Session summary

- [x] 1.1 `summarize_and_flush_session` reviews only departing turns, parses a `SUMMARY:` line into `SESSION_SUMMARY` (bounded, previous summary carried in, cleared at session end); verify with tests in `tests/test_ambiguity.py` for each `session-context` scenario, including model failure
- [x] 1.2 `answer_general_question` includes the summary in the per-turn message only when one exists, leaving the static system message unchanged; verify by test
- [x] 1.3 Live check with the local model on a fictional transcript: verify facts are extracted and a summary line is produced and parsed

## 2. Loop guard

- [x] 2.1 `WebAgent.browse` stops when the resolved action equals one already run (ok or error); verify tests for a repeated successful action, a repeated failed action, and that two different pages still reach the step limit

## 3. Integrate

- [x] 3.1 Update `ROADMAP.md` F8; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
