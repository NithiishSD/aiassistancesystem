# Design

### D1. `_chunk(text, max_words=180) -> list[str]`
- Split on blank lines (paragraphs). Any paragraph over `max_words` is split into sentences on `(?<=[.!?])\s+`. Any sentence still over the limit is cut into word windows.
- Consecutive small pieces are packed greedily up to `max_words`.
- There is no overlap, because the selection works per passage.
- Empty and whitespace-only pieces are dropped.

### D2. Gathering keeps raw documents
- `add()` stores `strip_invisible(content)[:RAW_DOC_CHAR_CAP]` instead of `_truncate(...)`.
- The document count limit (`max_sources`, 6) is unchanged.
- Labels are provisional until selection.

### D3. `_select_passages(question, sources) -> list[Source]`
- Memory sources pass through untouched and first.
- The other sources become passages: `(doc_index, position, f"{origin} — {query}: {chunk}")`.
- `reranker.score(question, passage_texts)`:
  - `None` → fallback: `_truncate(content)` per document, and log `research_passage_selection_degraded`.
  - Otherwise, passages are sorted by score in descending order. They are taken while `count < MAX_PASSAGES (8)` and `chars + len(chunk) <= PASSAGE_CHAR_BUDGET (6000)`. A passage that would exceed the budget is skipped, and smaller ones may still fit.
  - The title prefix is used for scoring only. The prompt shows the chunk text, because the source header already carries the origin.
- Regrouping:
  - Each document's selected chunks are sorted by position and joined with `"\n…\n"`.
  - Documents with nothing selected are dropped.
  - Labels `S1..Sn` are reassigned in the order memory, then documents in gather order.
- Log `research_passages_selected` with the counts: documents in, passages, selected, documents dropped, and prompt characters.

### D4. Where it runs
`research()` runs `gather()`, then `_select_passages()`, then `synthesize()`. `gather()` itself stays usable on its own, and returns raw documents.
