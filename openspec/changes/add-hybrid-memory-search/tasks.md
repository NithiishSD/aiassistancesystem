# Tasks

- [x] 1.1 Add `bm25s==0.3.11` to `requirements.txt`. Implement the keyword index with version invalidation (D1). Verify with unit tests on an ephemeral collection:
  - keyword hits are shaped like `retrieve()` items
  - `store` and `delete` invalidate the index
  - per-user and per-content-type filtering is honored
  - an empty collection returns `[]`
  - a forced failure returns `[]` and logs once
- [x] 1.2 Take the union of candidates in `retrieve_relevant()` (D2). Verify with tests:
  - a keyword-only candidate reaches the reranker
  - duplicates are not repeated
  - the reranker-unavailable path ignores keyword-only hits
  - the `keyword_added` log field
- [x] 1.3 Extend `evals/retrieval_fixture.json` with the exact-token facts and queries. Add `evals/bench_hybrid_retrieval.py`. Verify: `tests/test_retrieval_fixture.py` passes (recall 1.0, 0 leaks), and the benchmark reports union ≥ dense at both store sizes.
- [x] 1.4 Update ROADMAP F3 and the TECH_STACK keyword-search row. Run the full suite. Verify: 0 failures; report the count before (870) and after.

### Results (2026-09-29)
- Fixture (with f31–f37): recall 24/24, general-question leaks 0/5.
- `evals/bench_hybrid_retrieval.py` (production path):
  - 187 facts: dense 22/24 → hybrid 24/24
  - 412 facts: dense 22/24 → hybrid 23/24
  - 0 leaks at both sizes, +3–7 ms per query
- Suite: 870 → 885.
