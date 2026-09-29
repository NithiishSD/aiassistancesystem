# Design

## Context

See proposal.md for why. Current state:

- `memory.retrieve(query, domain, user_id, content_type, top_k)` returns dense Chroma top-k as `{"text", "metadata", "id", "distance"}`. Distances are L2 on all-MiniLM-L6-v2 embeddings.
- The three call sites:
  - `research_agent.ResearchAgent.gather()`: top_k=3, drops `distance > MEMORY_RELEVANCE_MAX_DISTANCE (1.0)`.
  - `orchestrator.answer_general_question()`: top_k=3, no gate.
  - `orchestrator._handle_correction()`: top_k=4. The candidates go to an LLM that picks which fact to correct.
- Observed on the live store: relevant ≈ 0.49–1.22, clearly unrelated ≈ 1.40–1.97, and the correct college fact sits at 1.06.
- The store holds ~135 facts after cleanup, so a 20-candidate pool is cheap.
- `sentence-transformers==3.2.1` is installed and ships `CrossEncoder`. Torch CPU is installed.

## Goals / Non-Goals

**Goals:**
- One relevance-ranked retrieval path used by general Q&A and the research agent.
- A relevance gate that does not depend on query length or phrasing the way L2 distance does.
- No new pip dependency. The model is loaded offline from `models/`.

**Non-Goals:**
- Changing `memory.retrieve()` semantics. Its raw candidates are still what fact correction needs.
- Reranking web content (F4) or adding BM25 (F3).

## Decisions

### D1. `sentence_transformers.CrossEncoder` with `cross-encoder/ms-marco-MiniLM-L-6-v2`
- **Why:** already installed (rule: prefer installed packages). It is the smallest standard MS MARCO cross-encoder (22M params) and the same family as the FlashRank model independently benchmarked at +31 ms.
- **Alternatives:**
  - FlashRank `ms-marco-MiniLM-L-12-v2` (ONNX, ~4 MB). This is the benchmarked option, but it is a new pip dependency. It is the fallback if D1 misses the 300 ms budget.
  - `bge-reranker-base` (278M). Better quality, several times slower on CPU. Revisit only if the fixture shows L-6 is insufficient.

### D2. New `memory.retrieve_relevant()`, with `retrieve()` left untouched
```
retrieve_relevant(query, domain="personal", content_type="fact",
                  top_k=3, candidate_k=20, min_score=RELEVANCE_MIN_SCORE) -> list[dict]
```
- It calls `retrieve(..., top_k=candidate_k)`, reranks, filters by `min_score`, and returns ≤ `top_k` items. Each item keeps the existing keys and adds a `"score"` key in 0–1.
- **Why:** additive and backward compatible. Existing tests and `_handle_correction` keep raw semantics. Correction deliberately wants candidates *even when* they are only weakly related, because the LLM makes the final call.
- **Alternative:** add a `rerank=True` flag to `retrieve()`. Rejected because it mixes two behaviors in one function, against the single-responsibility preference.

### D3. Separate `reranker.py` module
- `score(query: str, texts: list[str]) -> list[float] | None` returns sigmoid(logit) scores in 0–1, or `None` if the model is unavailable.
- It lazily loads the model once per process into a module-level cache, and records a failed load so it does not retry on every call.
- **Why:** memory.py stays storage-only, and later F4 (research page chunks) can reuse the same scorer.

### D4. Scores are normalized with a sigmoid, and the threshold is tuned on a fixture
- MS MARCO cross-encoders output unbounded logits. A sigmoid gives a readable 0–1 score, which is easier to set and log.
- `RELEVANCE_MIN_SCORE` is set from the labelled fixture (task group 4). It is the value that keeps every labelled-relevant fact while rejecting the most labelled-irrelevant ones. The initial value is recorded in code with a comment naming the fixture.

### D5. Degraded fallback: embedding ranking with a wider distance cutoff
- If `reranker.score()` returns `None`, return the dense top_k filtered by `FALLBACK_MAX_DISTANCE`, which defaults to **1.3**, and log `memory_retrieval_degraded`.
- **Why 1.3, not 1.0:** 1.0 is known to drop the correct college fact (1.06). The observed clearly unrelated matches start at 1.40, so 1.3 sits in the observed gap. It is also verified on the fixture.

### D6. Model stored in `models/`, loaded offline
- Path: `models/cross-encoder-ms-marco-MiniLM-L-6-v2/`, gitignored like the existing MiniLM.
- `setup.sh` gains a one-time download step. At query time the model loads from the local path only, so no network is needed.
- If the directory is absent, `score()` returns `None` and D5 takes over. It does not download silently at query time.

### D7. Research agent uses `retrieve_relevant()`
- `gather()` replaces its L2 filter with `retrieve_relevant(question, domain=domain, top_k=3)`.
- `MEMORY_RELEVANCE_MAX_DISTANCE` and its tests are removed. The relevance tests move to the new function.

## Risks / Trade-offs

- [First-call load time: torch model load ~1–2 s] → Load lazily once per process. Measure it and report it separately from the per-query budget.
- [Extra memory, ~100 MB resident] → Acceptable on the laptop. Revisit with FlashRank ONNX if memory becomes a constraint.
- [Threshold overfit to a ~20-query fixture] → The fixture is the seed of the A4 eval set and will grow. The threshold is re-checked whenever the fixture changes.
- [Cross-encoder trained on web passages, not one-line personal facts] → This is exactly why the threshold comes from Zedek's own fixture rather than a published value.
- [Degraded mode silently lowers quality] → The degraded state is logged on every call. A later observability change (C1) can surface it.

## Migration Plan

- Purely additive, with no data migration. The Chroma store is unchanged.
- Rollback: point the two call sites back at `memory.retrieve()`. No stored data depends on this change.
