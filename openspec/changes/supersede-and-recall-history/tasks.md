# Tasks

## 1. Superseding

- [x] 1.1 Create `fact_attributes.py` (single-valued table, value comparison); verify with tests for listed attributes, look-alikes that must not match, and equal values written differently
- [x] 1.2 Add `memory.remember()` and use it in the remember-fact handler; verify with tests: supersede, several old values, same value, multi-valued, rejected fact, reply text
- [x] 1.3 Add `memory.conflicts()` and `python memory.py --conflicts`; verify with tests and a read-only run on the live store (counts only)

## 2. History for questions about the past

- [x] 2.1 Add `memory.past_facts()` and the history block in general Q&A; verify with tests: past questions about the user get it with dates, present-tense and general questions do not

## 3. Integrate

- [x] 3.1 Live check on a scratch store with the real models and fictional facts; record the result
- [x] 3.2 Update `ROADMAP.md` A2; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
