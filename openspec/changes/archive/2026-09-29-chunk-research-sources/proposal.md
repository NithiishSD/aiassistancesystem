# Proposal

Implements **ROADMAP F4** (chunk research pages instead of truncating them).

## Why

`research_agent.gather()` cuts every source to its first 4,000 characters (`MAX_SOURCE_CHARS`). The tools return more than that:
- `fetch_url`: up to 8,000 characters
- Wikipedia: three extracts of up to 2,000 characters each
- arXiv and Semantic Scholar: several abstracts

Whatever sits past character 4,000 never reaches synthesis, however relevant it is. Everything before it is sent anyway, including navigation text and off-topic results, so the prompt is both lossy and padded.

## What Changes

- **Chunk, rank, select.**
  - Each tool-sourced document is split into passages of about 180 words. The split is recursive: paragraphs, then sentences, then words.
  - Each passage is prefixed with `<origin> — <query>`. A free title prefix did well in the 2026 chunking comparison, and plain recursive chunking matches semantic chunking (NAACL 2025).
  - Every passage from every document is scored against the question with the existing cross-encoder (`reranker.score`).
  - The best passages are kept, up to 8 passages and 6,000 characters in total.
- **Citations are unchanged.** Selected passages are regrouped under their document in page order, joined with `…`, so `[S1]` still means one source. A document with no selected passage is dropped from the sources and logged. Labels are assigned after selection, so they stay contiguous.
- **Memory facts are never chunked or dropped.** They are short, already relevance-gated by `memory.retrieve_relevant`, and come first.
- **Degradation.** If the reranker is unavailable, the previous behavior applies (the first 4,000 characters per document), and it is logged.
- **Raw input cap.** Documents are capped at 20,000 characters before chunking, as a safety bound. The tools' own caps are lower.

- **Wikipedia full-text fallback (found in the live check).** The Wikipedia tool uses OpenSearch, which matches article *titles* by prefix. Keyword-style planned queries ("Transformer architecture inventor") therefore found nothing, and a live research run returned zero sources. When OpenSearch returns no titles, the tool now falls back to Wikipedia full-text search (`list=search`). The tool's declared description and schema are unchanged, so its B2 pin stays valid.

## Capabilities

### New Capabilities
- `research-sources`: how gathered research material is reduced to the evidence given to the synthesizing model.

## Impact

- **Code:** `research_agent.py` only.
- **Behavior:** synthesis sees the most relevant passages from anywhere in each document, and prompt size is bounded (6,000 characters of tool text, down from up to 24,000).
- **Latency:** one cross-encoder pass over roughly 10–50 short passages per research run, measured in the tests.
- **Tier gate / watchdog / untrusted input:** none. The text is still invisible-stripped before chunking.
- **Dependencies:** none.

## Non-goals

- Raising the fetch tool's own 8,000-character cap. That would change a pinned tool definition (B2) for a small gain. Revisit it with measurements.
- Semantic or embedding-based chunking.
