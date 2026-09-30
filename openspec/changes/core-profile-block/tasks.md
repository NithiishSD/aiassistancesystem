# Tasks

## 1. Core profile

- [x] 1.1 Create `user_profile.py` (`select`, `refers_to_user`, `for_question`); verify with new `tests/test_user_profile.py`: core facts in stable order, non-core and sensitive attributes excluded, newest per attribute, size limits, distractor facts do not crowd the block, store failure means no profile
- [x] 1.2 `answer_general_question` adds the block for questions about the user without repeating retrieved facts; verify tests for a reworded question, a general question (no facts, store not read), no repetition, and a sensitive fact arriving only through retrieval

## 2. Measure and isolate

- [x] 2.1 Add the `+profile` coverage column to `evals/retrieval_eval.py`; verify it reports more labelled facts in context than retrieval alone at all three store sizes, with leaks unchanged, and record the numbers
- [x] 2.2 Set `ZEDEK_CHROMA_PATH` to a temp directory in `tests/conftest.py`; verify the full suite passes and the live `chroma_db` is unchanged by a test run

## 3. Integrate

- [x] 3.1 Update `ROADMAP.md` F6 and `COMPLIANCE.md` (what is sent to cloud models); run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
