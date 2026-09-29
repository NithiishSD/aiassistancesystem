# Tasks

- [x] 1.1 Add `llm_cache.py` (D3). Tests: round trip, key sensitivity (task / schema / messages), TTL expiry, `LLM_CACHE=off`, corrupt file → miss.
- [x] 1.2 `generate_structured(cache=...)` (D4). Tests: hit skips providers and reports `source="cache"`; local result not stored; StructuredOutputError not stored; stale row that fails validation is a miss.
- [x] 1.3 Static-first orchestrator prompts (D1, D2); `cache=True` on canonicalization and academic intent. Tests: byte-identical system message across inputs for each converted prompt; general-QA message order; dynamic text only in the last message.
- [x] 1.4 Run the routing eval gate and the full suite (`./zedek-env/bin/python -m pytest -q`). Verify 0 failures; report count before and after. Live check: canonicalize one statement twice and confirm the second is a cache hit. Update ROADMAP F5.

### Results (2026-09-29)
- Live (cloud enabled per process; the owner's `.env` keeps `ALLOW_CLOUD=False`):
  - canonicalize "i mostly code in rust these days and my editor is helix": Gemini `gemini-3-flash-preview`, 243 in / 22 out tokens, 8.1 s
  - the identical call again: `source=cache`, 0 tokens, 1 ms
  - academic intent "i solved two graph problems on dijkstra in 40 minutes" under the new layout: `log / graphs / solved / 40 min` (Groq, after a Gemini read timeout)
  - with cloud off, the local 8B answered both calls and nothing was cached, as specified
- Routing eval gate: 17 passed.
- Suite: 904 → 928. Three existing tests read the prompt from `messages[0]` and were updated to read the new layout (the MCP poison test now checks every message, which is stricter).
