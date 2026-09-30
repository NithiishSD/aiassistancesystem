# Tasks

## 1. Measure

- [x] 1.1 Add `evals/calibrate_thresholds.py` (DEV slice only) and record its table; verify by unit tests of `recommend()` for floor, margin, already-wrong-above and no-gain cases

## 2. Apply

- [x] 2.1 `classifier.intent_threshold()` used by `classify_intent()` and the offline eval; verify by test that 0.47 is local for `research_task`, escalated for `coding_task`, and 0.60 escalates `open_application`
- [x] 2.2 Set `threshold:` for `research_task`, `search_files`, `web_task` (0.45) and `academic_tracking` (0.50); verify a test pins exactly these four below the default and that `remember_fact`/`correct_fact` stay at the default
- [x] 2.3 Run the routing eval on DEV and TEST before and after; verify accuracy rises, escalation falls, and no class loses more than 2 points of precision on TEST; rewrite `evals/routing_baseline.json`

## 3. Integrate

- [x] 3.1 Update `ROADMAP.md` A5; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
