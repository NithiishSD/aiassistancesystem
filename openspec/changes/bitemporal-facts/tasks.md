# Tasks

## 1. Baseline

- [x] 1.1 Run `./zedek-env/bin/python -m pytest -q` and record the passing count; verify 0 failures before any edit

## 2. Validity metadata and the retrieval filter (`memory.py`)

- [x] 2.1 `store()` writes `valid_at` for facts; verify with a test in new `tests/test_fact_history.py` (temp Chroma path, never the live store) that a stored fact carries it
- [x] 2.2 `_where(user_id, content_type, include_invalidated=False)` adds `{"invalidated": {"$ne": True}}`; `retrieve()` uses it and gains `include_invalidated`; verify a test that a row written with no validity fields is still retrieved
- [x] 2.3 Add `invalidate(ids, domain, user_id, superseded_by=None)` (ownership check, merge metadata, skip already-invalidated, bump keyword index version, log `memory_invalidated`); verify tests: fields set, other user's row untouched, second call keeps the first `invalid_at`
- [x] 2.4 Verify by test that an invalidated fact is absent from `retrieve()`, `_keyword_candidates()` and `retrieve_relevant()` on the next call, and present with `include_invalidated=True`
- [x] 2.5 Verify by test that 25 invalidated near-duplicates do not push one current fact out of a `top_k=20` dense query

## 3. Corrections keep history (`orchestrator.py`)

- [x] 3.1 `_handle_correction`: store the replacement first, then `invalidate(old, superseded_by=new_id)`; an empty `new_id` leaves the old fact current and returns a "nothing was changed" reply; verify tests for both outcomes
- [x] 3.2 Retraction or correction of a usable fact calls `invalidate` (even when the user says "delete"); an old row that hygiene rejects, or whose replacement has the same dedup key, calls `delete_by_ids` (storing first); verify tests for "that's outdated", "delete that from memory" on a real fact, a junk fact retracted, and a same-text replacement
- [x] 3.3 Reply wording distinguishes updated / marked no longer true / permanently deleted; verify by test on the three strings and update any existing assertion in `tests/test_structured_output.py` and `tests/test_self_correction.py` that pins the old wording or the `delete_by_ids` call

## 4. Hygiene and history tools

- [x] 4.1 `clean_store()` skips invalidated rows (no normalize, dedup or repair); verify a test where a retracted fact is stored again and the current copy survives `dry_run=False`
- [x] 4.2 Add `purge_invalidated(domain, dry_run=True)` and the `--purge-invalidated` CLI flag (applies only with `--apply`); verify tests for dry run (nothing deleted, count reported) and applied (history gone, current facts intact)
- [x] 4.3 Add `memory.history()` and `python memory.py --history "<query>"`; move the writing self-test behind `--self-test`; verify a test of `history()` ordering/labels and that running `--history` against a temp store adds no rows

## 5. Measure, document, integrate

- [x] 5.1 Run `tests/test_retrieval_fixture.py`, `evals/bench_hybrid_retrieval.py` and `evals/bench_reranker.py`; verify recall and general-question leaks are unchanged from before the change (the filter must not alter results on a store with no invalidated rows) and record the numbers
- [x] 5.2 Live check on a copy of the store (not `./chroma_db`): correct a fact, ask the question, run `--history`; verify the new value answers and the old one shows as superseded
- [x] 5.3 Update `ROADMAP.md` A2 and the "Correction and erasure" row of `COMPLIANCE.md`; verify both describe invalidate-by-default, erase-on-request and the purge command
- [x] 5.4 Run `./zedek-env/bin/python -m pytest -q`; verify 0 failures and report the count before and after
