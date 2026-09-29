# Tasks

- [x] 1.1 Add `_chunk()` (D1). Verify with tests:
  - paragraph packing
  - an oversize paragraph splits into sentences, and an oversize sentence into word windows
  - no chunk exceeds `max_words`
  - text is preserved, apart from whitespace
  - empty input
- [x] 1.2 Raw documents in `gather()` (D2), plus `_select_passages()` and its use in `research()` (D3, D4). Verify with tests, using `reranker.score` stubbed:
  - an answer past character 4,000 is selected
  - the character budget and passage cap hold across six 8,000-character documents
  - regrouping by page order
  - dropped documents are unlabelled, and labels are contiguous
  - memory sources come first and are untouched
  - a `None` score gives the old truncation, plus a log
  - the existing research tests still pass
- [x] 1.3 Latency and real-model check: run the real cross-encoder over six 8,000-character documents and report the time. Update ROADMAP F4. Run the full suite. Verify: 0 failures; report the count before (885) and after.
- [x] 1.4 (found in the live check) Add a Wikipedia full-text fallback in `mcp_online_server.search_wikipedia`, for when OpenSearch finds no title. The declared description is unchanged, so there is no pin drift. Verify: tests for the fallback hit and for fallback failure; the existing "no results" test still passes.

### Results (2026-09-29)
- Real cross-encoder over six 8,000-character documents: 665 ms, and the answer located past character 4,000 was selected.
- Live research ("who introduced the transformer architecture and what problem did attention solve"):
  - before the fallback: 0 sources, and an ungrounded refusal
  - after: 2 Wikipedia sources, and an answer citing "Attention Is All You Need" (2017) with [S1][S2], in 8.5 s
- Suite: 885 → 904.
