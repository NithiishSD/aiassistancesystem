# Proposal

Implements **ROADMAP D2** (streaming and progress feedback), text path.

## Why

Every reply is fully blocking. A general question routed to the cloud takes 5–20 s (the F5 live check measured 8.1 s on Gemini and 20 s on Groq after a Gemini timeout), and the terminal shows nothing until the whole answer arrives, so a slow provider is indistinguishable from a hang. Streaming costs no accuracy and no extra quota: the same single request, delivered as it is generated.

## What Changes

- **Streaming transport** in `llm_provider`, used only when a caller passes a stream sink to `generate_chat(..., stream=sink)` and the call is plain text (not JSON, not schema-constrained):
  - OpenAI-compatible providers (Groq, NVIDIA NIM, OpenRouter, Cerebras): `"stream": true`, server-sent events, `choices[0].delta.content`. Usage is read from any chunk that carries it (`usage`, or Groq's `x_groq.usage`); an in-stream `error` object is raised like the non-streaming embedded error.
  - Gemini: `:streamGenerateContent?alt=sse`; thought parts are skipped.
  - Local Ollama: `ollama.chat(..., stream=True)`; counts from the final chunk.
- **Fallback stays correct.** A provider that fails before its first visible token falls through the chain exactly as today. One that fails mid-stream calls `sink.restart()` so the UI can discard the partial text, and the next provider answers. The returned result, tracing, quota accounting, and 404/429 handling are unchanged; the `ProviderReply` carries the full text.
- **`<think>` filtering across chunk boundaries**, matching `strip_thinking_tags` on the final text (needed before D3's Qwen3, which emits think blocks).
- **A failing sink never fails the request**: sink errors are logged and streaming stops for that call; the answer is still returned.
- **Orchestrator:** `handle(user_input, stream=None)` puts the sink in a context variable; only `answer_general_question` streams. Decomposed multi-step requests never stream (their answers are reformatted into a step list).
- **Terminal REPL:** prints a `(thinking…)` line at once, streams general answers as they arrive, and prints the returned answer in full whenever it differs from what was streamed, so output is never lost. Logs `stream_first_token` with time-to-first-token.

## Capabilities

### New Capabilities
- `reply-streaming`: incremental delivery of chat replies and its failure semantics.

## Impact

- **Code:** `llm_provider.py`, `orchestrator.py`.
- **Behavior:** first visible text after roughly one provider round-trip instead of the full generation time. Non-streaming callers are unaffected.
- **Tier gate / watchdog / untrusted input:** none. Streaming is display only; the gate decisions happen before any general answer is generated, and no streamed text is fed back into a model.
- **Dependencies:** none (`requests` streaming, `ollama` streaming, already installed).

## Non-goals

- **Sentence-to-TTS in the voice path.** Zedek has no TTS yet (`wake_word.py` prints replies). The sink interface is what a TTS consumer will plug into later.
- Streaming the specialist agents (research, coding, web) or structured calls.
- Hedged/backup requests (ROADMAP: voice turns only, and only once calls are non-blocking).
- A spinner thread: it would interleave with Tier-2 `input()` confirmations.
