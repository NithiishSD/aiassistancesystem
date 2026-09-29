# Tasks

## 1. Trace context

- [x] 1.1 Add `SESSION_ID`, the `_trace_id` ContextVar, `trace_context()`, `current_trace_id()`, and `_TraceFilter` to `zedek_logger.py`, attaching the filter to every handler `get_logger` creates (design D1). Verify: `tests/test_tracing.py` shows that records written inside `trace_context()` carry `trace_id` and `gen_ai.conversation.id`, records outside carry neither, two sequential contexts get different IDs and the same conversation ID, and a nested context restores the outer ID on exit.
- [x] 1.2 Wrap `orchestrator.handle()` in `trace_context()` and log `turn_started` (design D2). Verify: a test runs `handle()` with routing and LLM stubbed, captures records from the `orchestrator` and `llm_provider` loggers, and asserts that they share one `trace_id` and that a second `handle()` call gets a different one.

## 2. Standard LLM call records

- [x] 2.1 Add `ProviderReply` and make `_openai_compatible`, `_gemini`, and `_local` return it with the defensively parsed model and token counts (design D3). Update the existing test stubs that return bare strings. Verify: tests with `requests.post` and `ollama.chat` mocked cover each response shape (OpenAI-compatible `usage`, Gemini `usageMetadata`, Ollama counts), a missing usage block (None, no error), and non-int junk (None).
- [x] 2.2 `generate_chat()` returns `model` and `usage`, and logs one `gen_ai.client.operation` event per successful call with the D4 fields, including for the local fallback. Verify: tests check the event fields and the provider-name mapping (`gemini→gcp.gemini`, `local→ollama`), and that neither the prompt nor the answer text appears anywhere in the event. The existing `tests/test_provider_quota.py` still passes.

## 3. Token totals

- [x] 3.1 Add `input_tokens`/`output_tokens` to `_ProviderHealth`, sum them on success, and report them in `provider_stats()` (design D5). Verify: a test for two calls (100/20 and 50/10) reports 150/30, and a call with unknown usage adds nothing.

## 4. Finish

- [x] 4.1 Update `TECH_STACK.md` (the logging row) and ROADMAP C1, and run the full suite `./zedek-env/bin/python -m pytest -q`. Verify: 0 failures; report the count before (720) and after.
