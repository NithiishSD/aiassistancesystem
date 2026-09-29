# Proposal

Implements **ROADMAP A4** (routing evaluation harness). It covers the golden set, the per-class metrics, the regression gate, and the "must stay local" list. The retrieval half of A4 is already seeded (`evals/retrieval_fixture.json`, added by F1) and is not extended here.

## Why

Routing is Zedek's historical failure point. "I study at PSG" once routed to `search_files`, and a 0.403 score was labelled "high confidence". Yet it is the one thing the project does not measure. 596 unit tests check that code runs; none check whether a request lands on the right intent. There are now 16 intents plus 27 MCP tools competing for phrasings, and every new capability can quietly steal another's utterances. Published data shows routing accuracy collapsing as tool count grows. Without a measured baseline, Phase F changes (e.g. an embedding swap, F7) and new intents cannot be checked for routing regressions.

While inspecting the classifier for this change, one more problem surfaced. **Layer 1 reports a fake confidence score.** `classify_intent()` sets `top_score = ROUTE_THRESHOLD if result.name else 0.0`, so every Layer-1 match reports exactly 0.65 and is labelled "high". The real score is never available from the router's public result. In the installed semantic-router (0.0.72), `RouteChoice.similarity_score` is always `None` (verified). The actual decision is made internally: the winning intent has the highest *sum* of phrase similarities, and it is accepted only if its best single phrase similarity is **> 0.65**. The evaluation needs that real value, and so does ROADMAP A5 (confidence calibration).

## What Changes

- **Golden set:** `evals/routing_golden.csv`, labelled utterances with at least 20 per class across 17 classes (16 intents + `general_question`). Each row is tagged `dev` or `test`. The test slice must never be used for tuning. Utterances must not duplicate the router's own training phrases.
- **Layer-1 evaluation harness:** runs every golden utterance through `classify_intent()` against a **static-only router** (the router's built-in phrases, excluding runtime-learned and MCP-registered phrases, which differ per machine). The LLM fallback is disabled, and escalation is recorded as its own outcome, `ESCALATE`. It produces a per-class precision/recall/F1 report and a confusion matrix.
- **Baseline and regression gate:** the first run is committed as `evals/routing_baseline.json`. A pytest test fails if any class's precision or recall on the test slice drops more than 2 points below the baseline. Improvements are accepted by deliberately re-generating the baseline.
- **"Must stay local" list:** utterances that must be resolved by Layer 1 to a specific intent, without escalating to the LLM. A test enforces it, which guards against the Home Assistant–style regression where local commands started going to the LLM.
- **Real Layer-1 score:** `classify_intent()` reports the best-phrase similarity of the winning intent, the exact value the router compares against 0.65, instead of the constant. **No routing decision changes.** A guard test checks, on every golden utterance, that the new path chooses exactly what the router's own `__call__` chooses. That also catches any future semantic-router upgrade that changes these internals.
- **Opt-in end-to-end run:** a script that includes the LLM fallback, reporting how many escalations it rescues. It is never part of pytest, because it spends free-tier quota.

## Capabilities

### New Capabilities
- `intent-routing`: observable guarantees of intent classification. This covers the reported confidence score and decision stability. It is the first spec for the classifier; only the requirements touched here are specified.
- `routing-evaluation`: how routing quality is measured and protected. This covers the golden set, its dev/test split, the metrics produced, the regression gate, and the must-stay-local check.

### Modified Capabilities
<!-- None: openspec/specs/ has memory-retrieval and web-agent only. -->

## Impact

- **Code:**
  - `classifier.py`: real score in `classify_intent()`, and a static-only option for `_build_intent_router()`.
  - New `evals/routing_eval.py` (the harness), `evals/routing_golden.csv`, `evals/routing_baseline.json`, and `evals/must_stay_local.txt`.
  - New `tests/test_routing_eval.py` (the gate) and `tests/test_classifier_score.py`.
  - New `evals/routing_eval_e2e.py` (opt-in, uses quota).
- **Dependencies:** none new. `scikit-learn` 1.9.0 is already installed transitively, and it will be pinned in `requirements.txt` because the eval now imports it directly.
- **Runtime behavior:** unchanged. The only observable difference is that `decision["score"]` carries the real similarity.
- **Tier gate / watchdog:** none.
- **Untrusted-input path:** none. The harness reads only committed evaluation files. It deliberately excludes MCP-registered utterances, which are derived from third-party tool descriptions (the concern in ROADMAP B2), so a server's description text cannot shift the gated baseline.
- **Test time:** about 340 Layer-1 classifications. The model loads once, and the run is expected to take seconds.

## Non-goals

- Extending the retrieval eval set, which remains a separate item under A4.
- Confidence calibration and per-intent thresholds (A5). This change only makes the score real, so A5 becomes possible.
- Changing any routing decision, adding utterances, or retuning thresholds to improve scores. The first baseline records the router as it is today, including its weaknesses.
- Gating on the LLM fallback or on the production router (static + learned + MCP). Those are reported by the opt-in script only.
- Absolute accuracy floors (e.g. "≥ 0.85 per class"). The gate is relative to the committed baseline. Absolute floors are reported but not enforced until a baseline exists to judge them against.
