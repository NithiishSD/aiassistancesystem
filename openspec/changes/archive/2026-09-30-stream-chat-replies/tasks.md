# Tasks

- [x] 1.1 `_ThinkFilter` and `_StreamRelay` (D2, D3). Tests: split tags, stray closing tag, unfinished partial tag flushed, leading whitespace trimmed, restart only after emission, sink exception swallowed.
- [x] 1.2 Streaming transport (D4) for OpenAI-compatible, Gemini and local. Tests with fake SSE responses: chunk order, `[DONE]`, usage parsing (incl. `x_groq.usage`), in-stream error → HTTPError, Gemini thought parts skipped, JSON/schema calls never stream.
- [x] 1.3 Chain semantics in `_dispatch`: failure before first token (no restart), mid-stream failure (one restart, next provider's text), result/trace/quota unchanged, `stream_first_token` logged.
- [x] 1.4 Orchestrator + REPL (D5). Tests: general QA passes the sink; decomposed requests don't stream; `_TerminalStream` output and restart; REPL reprints only when needed.
- [x] 1.5 Run the routing gate and the full suite (`./zedek-env/bin/python -m pytest -q`), 0 failures; report the count before (928) and after. Live check: time to first token vs. total time on a real cloud provider and on local. Update ROADMAP D2.
