# Design

### D1. Sink contract (`llm_provider.StreamSink`, a Protocol)
`delta(text: str)` receives visible text in order; `restart()` means "discard what you've shown, a new answer follows". A sink need not be thread-safe: every call is on the caller's thread.

### D2. `_StreamRelay` (internal, one per `generate_chat` call)
- Wraps the sink with a `_ThinkFilter` and leading-whitespace trimming of the first visible chunk.
- `begin_attempt()`: before each provider; if the previous attempt emitted text, calls `sink.restart()`; resets the filter.
- `feed(chunk)` is the provider's `on_delta`; `finish()` flushes a held partial tag.
- Sink exceptions: logged once (`stream_sink_failed`), relay goes silent, provider keeps going.
- Records time to first visible chunk; `_dispatch` logs `stream_first_token {source, ms}`.

### D3. `_ThinkFilter`
Streaming equivalent of `strip_thinking_tags`: drops `<think>…</think>` (case-insensitive) and stray `</think>`, holding back a suffix that could be the start of a tag until the next chunk decides it.

### D4. Transport
- Providers gain an optional `on_delta` keyword; `_call_provider` passes it only when set, so two-argument stubs keep working.
- `_openai_compatible(..., on_delta)`: streams only when `on_delta` is set and neither `json_mode` nor `schema`. `_stream_openai_compatible` posts with `stream=True`, raises for status before reading, parses `data:` lines until `[DONE]`.
- `_gemini_request(..., on_delta)`: streaming URL + `alt=sse`; the existing one-shot 5xx retry still applies (it fires before any chunk is read).
- `_local(..., on_delta)`: `ollama.chat(stream=True)`.
- `_with_model_fallback` is unchanged: a 404 happens before any chunk.

### D5. Orchestrator
- `_REPLY_STREAM: ContextVar[StreamSink | None]`, set by `handle(user_input, stream=None)` and reset afterwards.
- `answer_general_question` passes `_REPLY_STREAM.get()`; `_handle_decomposed` runs its sub-requests with it set to `None`.
- `_TerminalStream` sink: prefixes `Zedek: ` on the first delta, prints deltas flushed, and on `restart()` prints a one-line notice and starts over. The REPL prints the returned answer when nothing was streamed or when it differs (whitespace-normalized) from what was.
