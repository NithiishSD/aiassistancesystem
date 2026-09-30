# Proposal

Implements ROADMAP A5 (confidence calibration).

## Why

One global Layer-1 cutoff (0.55, with 0.65 for two costly intents) treats every intent alike, but
their score distributions differ: on the golden DEV slice `research_task` is never wrong above
0.34 yet is only trusted above 0.55, so 43-48% of requests still go to the LLM.

## What Changes

- `evals/calibrate_thresholds.py`: for each intent, a table of correct and wrong DEV matches at
  each threshold, and a recommended threshold (lowest value at least 0.05 above the best-scoring
  wrong match, never below 0.45, never when the route is already wrong above its current
  threshold, only when it gains correct matches). It reads the DEV slice only.
- Four intents get a lower threshold: `research_task`, `search_files`, `web_task` 0.45 and
  `academic_tracking` 0.50. The escalation check in `classify_intent()` uses the intent's own
  threshold instead of the global one.
- Measured on the held-out TEST slice: accuracy 0.561 -> 0.610, LLM escalation 48.1% -> 43.3%,
  no class loses precision. DEV: 0.558 -> 0.609, escalation 47.2% -> 42.1%.
- Tried and rejected by the held-out gate: `top_memory_processes` at 0.45 (precision 0.92 ->
  0.85) and `list_processes_detailed` at 0.45 (claimed a general question).
- Not lowered although DEV allows it: `remember_fact` and `correct_fact`, because a wrong match
  changes stored memory.
- The routing baseline is rewritten to the new numbers.

## Non-goals

- No change to which route wins, to router phrases, or to the Layer-2 prompt.
- No learned classifier (ROADMAP A4's "next coverage lever"); thresholds only.
- No change to the two strict intents (`open_application`, `unsupported`) or the domain router.

## Capabilities

### New Capabilities

### Modified Capabilities
- `intent-routing`: the Layer-1 trust threshold is per intent.

## Impact

- Code: `classifier.py` (`intent_threshold`), `evals/routing_eval.py`, four capability files,
  new `evals/calibrate_thresholds.py`, `evals/routing_baseline.json`.
- Tier gate: unchanged. The four intents are read-only or research (Tier 0) or, for `web_task`,
  confirmed action by action (Tier 2), so a wrong local match cannot act without the user.
- Watchdog: none. Untrusted-input paths: none.
