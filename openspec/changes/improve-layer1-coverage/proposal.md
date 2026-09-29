# Proposal

Raises Layer-1 routing coverage, the follow-up recommended by the first routing baseline (**ROADMAP A4**). It is measured by that baseline.

## Why

Layer 1, the local embedding router, resolves only about a quarter of requests. **75% escalate to the LLM**, so almost every message spends free-tier quota and waits on a network call. Coding, remember/correct-fact, detailed-process, and research requests score F1 = 0.00 locally. The cause is structural. Each intent is represented by 6–9 phrases, the first of which is a 20–40-word description. Real requests are short and embed far away ("write a python function to reverse a linked list" scores 0.42 against `coding_task`). On top of that, a single global threshold of 0.65 is stricter than the data supports.

## Research and experiments behind this change

**External research (2025–26):**
- semantic-router's own guidance is to add short, realistic utterances and study failures.
- Few-shot intent-classification results improve with more examples per class (e.g. BANKING77 goes from 77.9% at 8 examples per class to 82.6% at 16).
- `sum` aggregation structurally favours routes with more utterances.
- A calibrated classifier over embeddings is a known-good pattern, but it replaces the routing mechanism, so it is kept as a later option.
- LLM-generated paraphrases are acceptable only as human-reviewed drafts.

**Experiments on the golden DEV slice only** (the test slice stays held out). "Local precision" means correct / all requests resolved to an intent locally, since a wrong local route runs the wrong action.

| Configuration | Coverage | Local precision | General questions claimed | Escalated |
|---|---|---|---|---|
| Current (static phrases, sum, 0.65) | 28.4% | 96.2% | 9.1% | 72.2% |
| Threshold 0.55 only | 43.2% | 97.4% | 9.1% | 58.3% |
| Logistic regression on the 102 phrases (p ≥ 0.4) | 54.5% | 96.0% | 18.2% | 46.5% |
| **Short phrases + general anchor + sum + 0.55** | **55.7%** | **97.0%** | 9.1% | **46.0%** |
| Same, `max` aggregation | 54.5% | 94.1% | 9.1% | 45.5% |
| Same, threshold 0.50 | 64.2% | 96.6% | 9.1% | 37.4% |

At 0.50, the 16 additional local decisions are only about 87% correct, versus 97% overall, so 0.55 is chosen. It is also the floor the research recommends. The first draft of new phrases pulled general questions into intents (18.2% claimed). Diagnosis showed concept questions sharing vocabulary with actions ("explain what ram is" → `top_memory_processes`). Concept-question anchor phrases fixed that.

## What Changes

- **Short example phrases:** 6–12 per intent, written fresh. None duplicates a golden row, and **none is within 0.90 cosine similarity of any held-out test row** (checked by embedding, not just exact text).
- **General-question anchor route:** about 29 explanation-style and concept questions ("what is ram used for", "how are threads different from processes"). When it wins, `classify_intent` behaves exactly as it does today for `general_question`: it escalates, and never becomes an action.
- **Intent threshold 0.65 → 0.55**, via a new `INTENT_ROUTE_THRESHOLD`. The **domain router keeps 0.65**. It shares `ROUTE_THRESHOLD` today, and changing it was not measured.
- Aggregation stays `sum`.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `intent-routing`: adds a requirement that explanation-style general questions are recognized locally as general, and never turned into an action by the local layer.

## Impact

- **Code:** `classifier.py` only (phrases, anchor, threshold constants, router build). `evals/routing_baseline.json` is regenerated after the gate check.
- **Behavior:** more requests are resolved locally, meaning fewer LLM routing calls and lower latency. The risk is a wrong local route; dev local precision is 97.0%, and most actions are still behind the tier gate and confirmation.
- **Tier gate / watchdog:** none. Routing only decides which handler runs, and handlers still gate their actions.
- **Untrusted-input path:** none.
- **Dynamic learning:** runtime-learned phrases still merge on top of the static ones, unchanged.

## Non-goals

- Answering anchored general questions without the LLM routing call. That would save one more call, but it risks turning a real request into chat. It is a later option once measured.
- Per-intent thresholds or `RouteLayer.fit()` (ROADMAP A5). With about 11 dev rows per class it would overfit.
- Replacing semantic-router with a trained classifier. Kept for later if coverage plateaus.
- The remaining known dev leak ("what's the difference between a process and a thread" → `list_processes_detailed`), a `sum`-aggregation effect. It is left rather than overfitting phrases to one dev row.
- ~~Changing the golden set.~~ **Revised during apply, with the owner's approval.** The new near-duplicate guard found 4 held-out test rows that were near-copies (0.92–0.97) of *original* router phrases, a defect in the previous change's leakage check, which matched exact text only. Those 4 rows are reworded. To keep the before/after comparison fair, the "before" baseline is rebuilt from the old router configuration on the corrected golden set.
