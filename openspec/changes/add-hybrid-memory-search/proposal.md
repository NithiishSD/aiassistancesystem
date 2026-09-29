# Proposal

Implements **ROADMAP F3** (BM25 alongside dense retrieval), measured before adoption as the roadmap requires.

## Why

`memory.retrieve_relevant()` reranks the dense top 20 with a cross-encoder. Whatever the dense embedding ranks below 20 never reaches the reranker. The store is about 30 facts today, but it grows with every conversation, and the facts are near-duplicates of each other ("User's course code for X", "User's lab slot for X", …), which is exactly where a small embedding model crowds out the right answer.

**Measured on 2026-09-29** (the fixture's 30 facts plus exact-token facts plus plausible near-duplicate distractors; the same reranker and gate throughout):

| Store size | Dense top 20 | Dense 20 ∪ BM25 10 | Leaks (general questions) |
|---|---|---|---|
| 187 facts | 23/24 | **24/24** | 0/5 → 0/5 |
| 562 facts | 21–22/24 across runs | **23–24/24** | 0/5 → 0/5 |

- Dense 40 and dense 60 did *not* match the union at 562 facts. More dense candidates are not a substitute for BM25.
- Latency stayed within the existing budget: about 40–60 ms per query for everything, reranking included.
- Dense-only results varied between runs, because Chroma's HNSW index is approximate. The union was never worse than dense-only.

## What Changes

- **A BM25 keyword index beside Chroma** (`bm25s`, in memory):
  - one index per domain/user/content type
  - built lazily from the collection
  - rebuilt after any `store()` or `delete_by_ids()` in that domain
- **`retrieve_relevant()` candidates are the union of dense top 20 and BM25 top 10** (only hits with a positive score), deduplicated. The cross-encoder rescores every candidate and applies the existing relevance gate.
- **Union, not reciprocal rank fusion.** RRF merges two rankings when one of them has to be truncated. Here the cross-encoder rescores the whole union, which makes a fused ranking redundant. It also measured no better.
- **Degradation.** If `bm25s` is unavailable or indexing fails, retrieval continues dense-only and logs `memory_keyword_index_unavailable`. The reranker-unavailable fallback stays dense-only, because BM25 hits carry no embedding distance.
- **The fixture grows** by 7 exact-token facts (course codes, a roll number, a room, a handle, a bus route) and 9 queries.
- **`evals/bench_hybrid_retrieval.py`** reproduces the measurement above.

## Capabilities

### Modified Capabilities
- `memory-retrieval`: candidate selection adds keyword matching, and degrades without it.

## Impact

- **Code:** `memory.py`. New eval script. Fixture data.
- **Dependencies:** `bm25s==0.3.11`. Its only runtime dependency is numpy, which is already pinned.
- **Privacy:** the index is in memory only. Nothing new is written to disk.
- **Tier gate / watchdog / untrusted input:** none.

## Non-goals

- A persisted BM25 index. Rebuilding is cheap at this store size.
- Fixing "where do I live" against "User's Location: …" at scale. There is no word overlap and the embedding ranks it low, so that is an embedding-model question (F7).
- Changing `retrieve()`, which stays the raw dense primitive.
