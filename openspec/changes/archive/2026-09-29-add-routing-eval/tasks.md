# Tasks

## 1. Truthful Layer-1 score and a reproducible router

- [x] 1.1 Add `include_dynamic: bool = True` to `classifier._build_intent_router()`. Verify: a test with an isolated data dir holding a learned phrase shows `include_dynamic=False` ignores it and the default still includes it.
- [x] 1.2 Add `_layer1_route_with(router, text) -> (name | None, score)` per design D1 (encode once, `_retrieve_top_route`, `_check_threshold`, score = `max(scores)` or 0.0), plus `_layer1_route(text)` over the global router. Verify: unit tests show a clear command returns its intent with score > 0.65 and a nonsense string returns `(None, score)` with no exception.
- [x] 1.3 Use it in `classify_intent()`: a Layer-1 hit reports the real score, and escalation logs `layer1_score`. Verify: `tests/test_classifier_score.py` shows two different utterances hitting the same intent can report different scores, every Layer-1 hit reports > 0.65, and the existing `tests/test_hybrid_router.py` and `tests/test_ambiguity.py` pass unchanged.

## 2. Golden set

- [x] 2.1 Write `evals/routing_golden.csv` per design D4: ≥ 20 rows for each of the 16 intents plus `general_question`, a fixed `dev`/`test` split of about 50/50 per class, realistic and messy phrasings, the hard negatives D4 lists, and no personal details. Verify: a loader test checks columns, the allowed intent names, ≥ 20 per class, and both splits present for every class.
- [x] 2.2 Add the leakage check to the test file. Verify: no golden utterance equals an `INTENT_UTTERANCES` phrase (case- and whitespace-insensitive).

## 3. Harness

- [x] 3.1 Create `evals/routing_eval.py` with `load_golden()`, `classify_offline(router, text)` per design D3 (guards → Layer 1 → `ESCALATE`, no cache, no LLM), and `evaluate(rows, router)`. `evaluate` returns per-class precision/recall/F1/support (with the general_question→ESCALATE remap), the raw confusion matrix including an `ESCALATE` column, overall accuracy, and escalation rate. Verify: unit tests on a tiny hand-made row set with a stub router check the remap, a general question grabbed by an intent counting as an error, and the ESCALATE column.
- [x] 3.2 CLI: `python evals/routing_eval.py [--slice dev|test|all]` prints the classification report and the confusion matrix. Verify: running it on the real golden set prints both, and a timing line shows the runtime.
- [x] 3.3 The equivalence guard from design D1: a test that, for every golden utterance, `_layer1_route_with(static_router, t)[0] == static_router(t).name`. Verify: it passes, which proves the score change altered no routing decision.
- [x] 3.4 Runtime-state test: after a full evaluation, `data/dynamic_utterances.json` (content hash), `classifier._intent_router` (identity), and `classifier._ROUTING_CACHE` (contents) are unchanged, and `query_llm_with_tools` was never called (patched to raise). Verify: the test passes.

## 4. Baseline and gate

- [x] 4.1 `--write-baseline` writes `evals/routing_baseline.json` per design D5 (meta with golden SHA-256, phrase count, semantic-router version, and date; per-class test-slice metrics; accuracy; escalation rate). Generate and commit the first baseline. Verify: the file exists, and every class has an entry.
- [x] 4.2 `tests/test_routing_eval.py` gate: fail if any class's test-slice precision or recall is more than 0.02 below baseline (the message names the class and metric), and fail with a "regenerate the baseline" message when the golden hash does not match `meta`. Verify: the gate passes on the committed baseline, and a unit test with a doctored baseline (one recall raised by 0.10) fails with the class named. Another with a mismatched hash fails with the regenerate message.

## 5. Must-stay-local list

- [x] 5.1 Seed `evals/must_stay_local.csv` per design D6 from dev rows that currently pass locally, up to 2 per core local intent. List intents with no passing row as gaps in the harness report. Verify: the file loads, and each row's intent is one of the D6 core intents.
- [x] 5.2 Test: every must-stay-local row classifies offline to its intent (never `ESCALATE`). Verify: it passes, and a unit test with a stub router returning `ESCALATE` fails and names the row.

## 6. Opt-in end-to-end script

- [x] 6.1 `evals/routing_eval_e2e.py` per design D7 (`--slice`, `--limit 60`, `--sleep 1.5`, `--router static|production`) runs `classify_intent` with the LLM on escalated rows and reports the rescue rate per class. Verify: `pytest --collect-only` shows it is not collected, and `--help` runs. A live run is **not** required, because it spends quota. It is optional for the owner.

## 7. Finish

- [x] 7.1 Pin `scikit-learn==1.9.0` in `requirements.txt` under a comment naming the eval. Verify: `pip install -r requirements.txt --dry-run` resolves.
      - The dry-run first **failed**, for a reason unrelated to this change. `numpy==2.5.3` (pinned earlier that day) conflicts with `semantic-router==0.0.72`'s declared `numpy<2`, while `librosa` 1.0 (from Resemblyzer) needs `numpy>=2.1`. No resolution exists, so a fresh `pip install -r requirements.txt` was broken. Fixed by moving semantic-router to a `--no-deps` step in `setup.sh` and pinning its 9 other runtime deps in `requirements.txt`. The router works on numpy 2.5.3 (full suite). The dry-run now exits 0 and all 162 requirements are already satisfied.
- [x] 7.2 Record the first baseline's headline numbers (overall accuracy, escalation rate, the weakest 3 classes, must-stay-local gaps) in `design.md` under a new Measurements section, and in `ROADMAP.md` A4. Verify: both files contain the numbers and they match `routing_baseline.json`.
- [x] 7.3 Run the full suite `./zedek-env/bin/python -m pytest -q`. Verify: 0 failures; report the test count before (596) and after, plus the added runtime of the routing tests.
