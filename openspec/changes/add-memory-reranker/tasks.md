# Tasks

## 1. Reranker model and scorer

- [ ] 1.1 Add a download step to `setup.sh` that fetches `cross-encoder/ms-marco-MiniLM-L-6-v2` into `models/cross-encoder-ms-marco-MiniLM-L-6-v2/`, then run it once. Verify: the directory contains `config.json` and the model weights.
- [ ] 1.2 Create `reranker.py` with `score(query, texts) -> list[float] | None`. It lazy-loads the model once from the local path, returns sigmoid(logit) scores in [0, 1], and returns `None` (logging `reranker_unavailable` once) when the model is missing or fails to load. Verify: `tests/test_reranker.py` covers ordering (a relevant text outscores an irrelevant one, with the real model skipped if absent), the empty list, the missing-model path returning `None` with no exception, the single load across calls, and scores within [0, 1].
- [ ] 1.3 Latency benchmark: a script under `evals/` that times ranking 20 facts for 10 queries after warm-up and reports the median and p95. Verify: the median is ≤ 300 ms, recorded in the change's notes. If it is over budget, stop and raise the FlashRank alternative (design D1) before continuing.

## 2. Relevance-ranked retrieval in memory

- [ ] 2.1 Add `retrieve_relevant(query, domain, content_type, top_k, candidate_k, min_score)` to `memory.py` per design D2: dense `retrieve(top_k=candidate_k)` → `reranker.score()` → sort → filter by `min_score` → at most `top_k`, adding a `"score"` key. Verify: unit tests with a stubbed collection and a stubbed scorer check order, top_k bound, threshold exclusion, and the empty result when nothing passes.
- [ ] 2.2 Implement the degraded fallback per design D5: when the scorer returns `None`, rank by distance with `FALLBACK_MAX_DISTANCE = 1.3` and log `memory_retrieval_degraded`. Verify: a test with the scorer stubbed to `None` returns distance-ordered results and never raises.
- [ ] 2.3 Confirm `memory.retrieve()` is unchanged. Verify: the existing memory, correction, and hygiene tests pass untouched.

## 3. Wire callers

- [ ] 3.1 `orchestrator.answer_general_question()` uses `memory.retrieve_relevant(user_input, domain=domain, top_k=3)`. Verify: a test with the retrieval stubbed to return `[]` shows the prompt contains "(no relevant long-term facts found)".
- [ ] 3.2 `research_agent.ResearchAgent.gather()` uses `retrieve_relevant()` and drops the L2 gate. Remove `MEMORY_RELEVANCE_MAX_DISTANCE` and replace its tests in `tests/test_research_agent.py` with equivalents against the new function. Verify: `pytest tests/test_research_agent.py -q` passes.
- [ ] 3.3 Leave `_handle_correction()` on `memory.retrieve()`. Verify by grep: it is the only remaining caller of raw `retrieve()` outside memory.py and the tests.

## 4. Threshold fixture (seed of ROADMAP A4)

- [ ] 4.1 Create `evals/retrieval_fixture.json`: a fixed set of ~30 facts written to resemble the cleaned live store, plus ~20 queries, each labelled with the facts that should be returned (including queries with no relevant fact, and the "what college do I study at" phrasing). Verify: the file loads and every labelled fact ID exists in it.
- [ ] 4.2 Add `tests/test_retrieval_fixture.py`. It indexes the fixture into a temporary Chroma collection, runs `retrieve_relevant()`, and reports recall of labelled facts and the count of irrelevant facts returned. It is skipped when the reranker model is absent. Verify: it runs and prints both metrics.
- [ ] 4.3 Set `RELEVANCE_MIN_SCORE` (and check `FALLBACK_MAX_DISTANCE = 1.3`) from the fixture results: every labelled-relevant fact kept and irrelevant returns minimized. Record the chosen values and their metrics in a code comment. Verify: the fixture test asserts recall = 1.0 for labelled facts and that the no-relevant-fact queries return nothing.

## 5. Finish

- [ ] 5.1 Update `TECH_STACK.md`: move the reranker from "Planned additions" into the core architecture table, noting the D1 model choice and the measured latency.
- [ ] 5.2 Run the full suite `./zedek-env/bin/python -m pytest -q`. Verify: 0 failures, and report the test count before (488) and after.
- [ ] 5.3 Run the same live check used in the cleanup (the "what college do I study at" query against the real store). Verify: the college fact is returned first, and "what is a binary search tree" returns no personal facts.
