# Design

### D1. Keyword index
- **Cache key:** `_keyword_indexes[(domain, user_id, content_type)] = (version, ids, docs, metas, retriever)`.
- **Invalidation:** `_index_versions[domain]` is bumped by `store()` and `delete_by_ids()`. A cached index whose version differs from the current one is rebuilt on next use.
- **Build:**
  - `collection.get(where=<same filter as retrieve()>)` returns ids, documents, and metadatas.
  - Documents are tokenized with `bm25s.tokenize(..., stopwords="en")`, then `BM25().index(...)`.
- **Empty collection:** no index is built, and keyword search returns `[]`.
- **`_keyword_candidates(query, domain, user_id, content_type, k)`** returns items shaped like `retrieve()` items (`text`, `metadata`, `id`, and `distance=None`), with a positive BM25 score only, at most `k`.
- **Failure handling:**
  - Any exception (the import, the build, or the query) logs `memory_keyword_index_unavailable` once per process and returns `[]`.
  - The per-process flag stops the warning repeating, and every call still retries, so the index recovers if the failure clears.

### D2. Candidate union
`retrieve_relevant()` works in four steps:
1. `dense = retrieve(..., top_k=max(candidate_k, top_k))`
2. `keyword = _keyword_candidates(query, ..., k=KEYWORD_CANDIDATE_K)`, with `KEYWORD_CANDIDATE_K = 10`
3. The candidates are dense first, then keyword hits whose id is not already present.
4. The candidates go through the cross-encoder and the existing gate, exactly as before. The log gains `keyword_added`.
- **Reranker unavailable:** only dense candidates, which have distances, go through the `FALLBACK_MAX_DISTANCE` path.
- **Query text:** BM25 uses the raw query. English stopwords remove "my/what/do"; "the user's" is not added, because "user" appears in every fact and adds nothing.

### D3. Why union over RRF
RRF exists to merge two rankings when the result has to be cut to N without a common score. The cross-encoder is that common score, applied to every candidate. Measured: RRF top 30 matched the union at 562 facts and did not beat it, and the union needs no fusion parameter.
