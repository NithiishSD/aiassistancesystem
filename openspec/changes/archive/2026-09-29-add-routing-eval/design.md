# Design

## Context

See proposal.md for why. Current state (verified during planning):

- `classifier.classify_intent(text)` runs in this order: acknowledgement guard → unsupported-action guard → `_ROUTING_CACHE` → Layer 1 `_intent_router(text)` → Layer 2 `query_llm_with_tools(text)` when Layer 1 returns no name or `general_question`.
- `_intent_router` is a semantic-router 0.0.72 `RouteLayer` (`aggregation="sum"`). It is built by `_build_intent_router()` from `INTENT_UTTERANCES` (16 intents, 102 phrases) **merged with** `data/dynamic_utterances.json`. That file holds runtime-learned phrases and, via `register_mcp_tools()`, MCP tool descriptions. It is gitignored and differs per machine.
- Inside `RouteLayer.__call__`: `_retrieve_top_route(vector)` picks the class with the highest *sum* of top-k phrase similarities and returns that class's scores. `_check_threshold()` passes if `max(scores) > route.score_threshold` (0.65). `RouteChoice.similarity_score` is never populated (observed `None`).
- `classify_intent` reports `score = ROUTE_THRESHOLD` on any Layer-1 hit.
- Spot check (planning, current router): "what is a binary tree" → `coding_task` at Layer 1 (a general question wrongly claimed). "open vs code", "log that I solved a graphs problem", and "research papers on spaced repetition" all escalate to Layer 2. The baseline will record these as they are.
- scikit-learn 1.9.0 is installed transitively but is not pinned. There is no CI; pytest is the gate.

## Goals / Non-Goals

**Goals:**
- A deterministic, offline, per-class measurement of Layer-1 routing that a solo developer runs as part of `pytest`.
- A truthful Layer-1 score, with a mechanical guarantee that routing decisions did not change.

**Non-Goals:**
- Improving routing. The first baseline intentionally records today's weaknesses.
- Measuring Layer 2 inside pytest.

## Decisions

### D1. Real score via the router's own internals, guarded by an equivalence test
- New `classifier._layer1_route(text) -> (name | None, score)`. It encodes once, calls `_intent_router._retrieve_top_route(vector)`, then `_check_threshold(scores, route)`. `name` is `route.name` when the check passes; `score` is `max(scores)` (0.0 if empty). This matches `__call__` exactly for routes without function schemas, and Zedek's routes have none.
- `classify_intent` uses it: a Layer-1 hit reports that `score`. On escalation the Layer-1 score is logged (`layer1_score`), which is useful for A5.
- **Guard test:** for every golden utterance, `_layer1_route(t)[0] == _intent_router(t).name`. It fails loudly if a semantic-router upgrade changes the internals. `semantic-router==0.0.72` is already pinned.
- **Alternatives rejected:** recomputing cosine similarity by hand (duplicates library logic and would drift silently); upgrading semantic-router to get a populated `similarity_score` (a behavior change outside scope).

**D1 refinement (during apply):** the routing *decision* stays on the router's public `router(text)` call, unchanged from today. The private methods are used only to compute the *score*, and their route must equal the public decision. If they disagree or raise (as a test's `MagicMock` router does), the score falls back to the previous value and `layer1_score_unverified` is logged. The existing tests that mock `_intent_router` keep passing unchanged, and "no routing decision changes" holds by construction. The guard test asserts the scores are *verified* (no mismatch) for every golden utterance, which catches library drift. Cost: one extra encode per first-time classification. Repeats hit `_ROUTING_CACHE`.

### D2. Static router built separately, never swapped into global state
- `_build_intent_router(include_dynamic: bool = True)`. `False` skips `data/dynamic_utterances.json`, which also excludes MCP-registered phrases, because they live in that file.
- The harness builds its own instance and calls a pure function, `_layer1_route_with(router, text)`, rather than replacing the module-global `_intent_router`. `_layer1_route(text)` becomes a thin wrapper over the global.
- **Why:** the spec requires that runtime state is left untouched. Not mutating globals is simpler and safer than save/restore.

### D3. The harness evaluates guards + Layer 1, and records escalation explicitly
- `evals/routing_eval.py: classify_offline(router, text) -> (label, score)`:
  - acknowledgement guard → `general_question`
  - unsupported guard → `unsupported`
  - Layer 1 hit → the intent
  - otherwise `ESCALATE`
- It never touches `_ROUTING_CACHE` and never calls `query_llm_with_tools`. It mirrors `classify_intent`'s pre-LLM path by calling the same guard functions.
- **Scoring rule for general questions:** before computing metrics, a `general_question` label predicted as `ESCALATE` is counted as `general_question` (correct), because escalation is the correct local behavior. The raw confusion matrix keeps the `ESCALATE` column so escalations stay visible for every class.
- Metrics use `sklearn.metrics.precision_recall_fscore_support` and `confusion_matrix` over the 17 true labels. `ESCALATE` is a prediction-only column, not a class.

### D4. Golden set format and authorship
- `evals/routing_golden.csv` columns: `utterance,intent,split`. `intent` is one of the 16 intent names or `general_question`, and `split` is `dev` or `test`.
- **≥ 20 per class, 340+ rows**, split roughly 50/50 per class. Assignment is fixed in the file, not random at runtime.
- **Authorship:** written during apply as realistic, varied phrasings: casual, misspelled, indirect, and multi-clause. Phrasing patterns come from past misroutes documented in `zedek_context.md`. No personal details are copied from logs or memory, because the repo is public, so names, colleges, and places are generic.
- Hard negatives are included deliberately: general questions that mention code or files ("what is a binary tree", "how do file systems work"), facts that sound like commands ("I always open Brave first thing"), and near-miss pairs between `research_task`, `web_task`, and `mcp_tool`.
- **Leakage check:** a test asserts that no golden utterance equals an `INTENT_UTTERANCES` phrase (case- and whitespace-insensitive).

### D5. Baseline and gate
- `evals/routing_baseline.json` holds:
  - `meta`: golden-file SHA-256, static phrase count, semantic-router version, date
  - per-class `{precision, recall, f1, support}` on the **test** slice
  - overall accuracy and escalation rate, for reporting only
- **Gate (pytest):** recompute on the test slice. Fail if any class's precision or recall is more than 0.02 below baseline, and name the class and metric in the message.
- **Stale-baseline guard:** if the golden-file hash differs from `meta`, fail with "golden set changed; regenerate the baseline with `python evals/routing_eval.py --write-baseline`". Otherwise a golden edit would silently change what the gate compares.
- The baseline is written only by `--write-baseline`, never by pytest.

### D6. Must-stay-local list
- `evals/must_stay_local.csv` (`utterance,intent`) is seeded only from golden **dev** rows that Layer 1 already resolves correctly today, for the core local actions: `search_files`, `disk_usage_by_folder`, `top_memory_processes`, `free_space_summary`, `directory_size`, `list_processes_detailed`, `open_application`, `system_inspect`. Up to 2 per intent.
- An intent with no passing row today is **recorded as a gap in the report**, not forced into the list. A list that fails on day one would just be deleted.
- The pytest check requires each row to classify locally to its intent, never `ESCALATE`.

### D7. Opt-in end-to-end script
- `evals/routing_eval_e2e.py [--slice test] [--limit N] [--sleep S]` runs `classify_intent` (with the LLM) on escalated rows only. It reports the rescue rate and a per-class result. Defaults: `--limit 60 --sleep 1.5`, to respect free-tier limits.
- Not collected by pytest; it lives under `evals/` with no `test_` prefix.

## Risks / Trade-offs

- [Synthetic phrasing is cleaner than real speech, so the metrics are optimistic] → Hard negatives and messy phrasings are included deliberately. Real misroutes are promoted into the dev slice as they happen, and the baseline is re-generated deliberately.
- [The gate depends on private semantic-router methods] → The guard test (D1) fails immediately on drift, and the version is pinned.
- [The baseline includes today's known weaknesses, e.g. "what is a binary tree" → `coding_task`] → That is intended. The gate prevents *regressions*; improvements show up as metrics rising above baseline.
- [The static router differs from production, which adds learned and MCP phrases] → The gated number is reproducible but not identical to production. The e2e script can optionally report the production router (`--router production`) for comparison.
- [Test runtime: 340+ encodings] → The encoder is already warm in the suite. The run is estimated at seconds and will be measured during apply.

## Migration Plan

- Additive. The only production code change is the reported score and the internal refactor of the Layer-1 call, and the D1 guard test proves routing is unchanged.
- Rollback: revert the commit. No stored data changes.

## Measurements (first baseline, 2026-09-29)

Layer 1 on the static router, held-out **test** slice (187 rows), `evals/routing_baseline.json`:

- **Accuracy 0.299. 74.3% of requests escalate to the LLM.** The "cheap local router" resolves about a quarter of traffic,
  so most requests spend free-tier quota.
- **F1 = 0.00:** `coding_task`, `correct_fact`, `list_processes_detailed`, `remember_fact`, `research_task`. Real
  requests in these classes score below the 0.65 bar against the long description-style training phrases
  (e.g. "write a python function to reverse a linked list" scores 0.42). The only `coding_task` prediction in the golden
  set is a general question ("what is a binary tree" at 0.71).
- **Best:** `general_question` 0.96, `top_memory_processes` 0.90, `free_space_summary` 0.78.
- **Must-stay-local:** 13 rows, and every core local intent has at least one. `search_files`, `list_processes_detailed`, and
  `open_application` have only one row each.
- **Runtime:** the full routing test file takes about 16 s including the model load. The CLI over all 374 rows takes 6.5 s.

**Production bug found by the first run: the acknowledgement guard misfires on substrings.**
`_is_acknowledgement_or_confirmation()` checks `"ty" in cleaned`, `"ok" in cleaned`, and `"got it" in cleaned` as raw
substrings. Requests containing *type*, *empty*, *capacity*, *typescript*, *productivity*, *facebook*, or *book* are
classified as "okay/thanks" and skip BOTH Layer 1 and the LLM. Observed: "type hello world into the textbox on
example.com" (web task), "disk capacity and free space please", "port this javascript to typescript", "open facebook",
"book a movie ticket", and "you got it wrong, my exam is on tuesday". Deliberately **not** fixed here, because this change
records routing as it is. It is the next change, and this baseline will measure the fix.
