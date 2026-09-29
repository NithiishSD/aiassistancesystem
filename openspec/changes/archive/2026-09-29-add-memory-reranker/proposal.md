# Proposal

Implements **ROADMAP F1** (cross-encoder reranker) and supersedes **ROADMAP A3** (relevance threshold everywhere).

## Why

Memory retrieval asks for 3 dense matches and gates them on a hand-tuned L2 distance cutoff of 1.0. On the cleaned live store (2026-09-29), the correct answer to *"what college do I study at"* (`User's college: PSG College of Technology`) comes back at distance **1.06**. So the existing cutoff in the research agent drops the right fact, and applying the same cutoff to general Q&A as A3 planned would spread that failure. Meanwhile general Q&A uses no gate at all and injects the top 3 whatever they are. A fixed distance threshold is the wrong tool, because distances shift with query length and phrasing. A reranker scores query–fact relevance directly. It measured +31 ms on CPU in an independent benchmark, and it is the standard fix.

## What Changes

- Add relevance-ranked memory retrieval: fetch a wider candidate pool (~20), rescore each candidate against the query with a small local cross-encoder, keep the best `top_k`, and drop candidates below a relevance-score threshold.
- **General Q&A** (`answer_general_question`) switches from ungated top-3 to relevance-ranked retrieval. This covers A3's intent.
- **Research agent** memory gathering switches from the L2 ≤ 1.0 filter to relevance-ranked retrieval. `MEMORY_RELEVANCE_MAX_DISTANCE` is removed as the primary gate.
- The existing `memory.retrieve()` keeps its current signature and behavior (raw dense top-k with distances). Callers that need raw candidates, such as fact correction, keep using it.
- If the reranker model cannot load, retrieval degrades to embedding-distance ranking and logs that it is degraded. It never raises.
- The reranker model is stored in `models/` like the existing embedding model and loaded offline. It needs no new pip dependency, because `sentence-transformers` already provides the cross-encoder.
- A small labelled retrieval fixture (~20 queries over a fixed fact set) is added to tune the threshold. It seeds the ROADMAP A4 retrieval eval set.

## Capabilities

### New Capabilities
- `memory-retrieval`: how stored personal facts are selected for a question. This covers ranking by relevance, the relevance threshold, the result-count limit, degraded behavior, offline operation, and the latency budget.

### Modified Capabilities
<!-- None: openspec/specs/ is empty; this is the first spec. -->

## Impact

- **Code:** `memory.py` (new relevance-ranked retrieval function), new `reranker.py`, `orchestrator.py` (`answer_general_question`), `research_agent.py` (`gather`, removes the L2 gate). Fact correction (`_handle_correction`) is deliberately unchanged.
- **Dependencies:** none new. One model download (`cross-encoder/ms-marco-MiniLM-L-6-v2`, about 90 MB) into gitignored `models/`. `setup.sh` gains a bootstrap step.
- **Latency:** one cross-encoder pass over ≤20 short pairs per memory lookup. The budget is ≤300 ms on the laptop CPU, measured in this change.
- **Tier gate / watchdog:** none. Retrieval is read-only and triggers no actions.
- **Untrusted-input path:** none. Stored facts are the user's own data, and fetched web content is out of scope (see F4).
- **Tests:** new unit tests for the reranker (model mocked) and the retrieval function, plus the fixture-based threshold test. The existing 488 tests must still pass.

## Non-goals

- BM25/keyword hybrid search (ROADMAP F3). This change adds reranking only.
- Swapping the embedding model (F7).
- Chunking or reranking fetched research pages (F4). Only stored memory facts are reranked here.
- Changing fact correction's candidate selection.
- Larger rerankers (bge-reranker-v2-m3, mxbai-v2). Start small; the fixture decides whether that is enough.
