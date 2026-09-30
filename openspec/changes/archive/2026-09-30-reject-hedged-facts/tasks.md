# Tasks

## 1. Reject hedged values

- [x] 1.1 Add a whole-word hedge check on the value half in `normalize_fact()` returning reason `hedged_value`; verify with new tests in `tests/test_memory_hygiene.py` for each spec scenario (guess in parentheses, guessed value, hedge inside another word, plain fact)
- [x] 1.2 Verify by test that `clean_store(dry_run=True)` reports a hedged row and deletes nothing
- [x] 1.3 Run the sweep as a dry run against the live store and record the count (no `--apply`)

## 2. Integrate

- [x] 2.1 Run `tests/test_retrieval_fixture.py`; verify it still passes (fixture facts must not be hedged)
- [x] 2.2 Tick the item in `ROADMAP.md` A1; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
