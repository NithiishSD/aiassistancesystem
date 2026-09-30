# Tasks

## 1. Eval set and harness

- [x] 1.1 Add `paraphrases` to `evals/retrieval_fixture.json`; verify by test that every labelled id exists and that `tests/test_retrieval_fixture.py` still passes on `queries`
- [x] 1.2 Add `evals/retrieval_eval.py` (embedding comparison and reranker threshold sweep); verify by test of `parse_model` and by running it for the installed models

## 2. Measure and decide

- [x] 2.1 Run the eval for MiniLM-L6, bge-small-en-v1.5 and snowflake-arctic-embed-s, and the sweep for the installed reranker, ms-marco-MiniLM-L-12 and bge-reranker-base; record the numbers in the proposal
- [x] 2.2 Update `ROADMAP.md` (A4 retrieval eval item, F7, rejected list); run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
