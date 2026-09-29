# Proposal

Implements **ROADMAP C1** (per-turn tracing, plus OpenTelemetry GenAI field names and token accounting). ROADMAP C2 (quota awareness) shipped in the previous change.

## Why

Zedek writes structured JSON logs per module, but nothing ties one user message together. A single turn can touch the classifier, the task planner, one or more LLM providers, MCP tools, and a specialist agent, and each writes to a different `logs/<module>.log`. Answering "what happened for *this* message, which model answered, and how many tokens did it cost?" means guessing by timestamp across files. Token usage isn't recorded at all, although every cloud provider returns it and it is what free-tier token limits are measured in.

The research pass recommended **not** standing up Langfuse or Phoenix (that would mean ClickHouse and Docker on a laptop). Instead, emit the standard OpenTelemetry GenAI field names in the existing JSON logs, so a later tracing backend is a configuration change rather than a migration.

## What Changes

- **A trace ID for every turn.** `orchestrator.handle()` opens a trace context, and **every log line written during that turn, by any module, carries the same `trace_id`** automatically. It is implemented with `contextvars` plus a logging filter, so no function signatures change. A session-level `gen_ai.conversation.id` ties the turns of one run together.
- **A standard record for every LLM call.** A `gen_ai.client.operation` log event records:
  - `gen_ai.operation.name` (`chat`)
  - `gen_ai.provider.name` (`gcp.gemini` and `groq` are well-known values; `nvidia_nim`, `openrouter`, `cerebras`, and `ollama` are custom values, which the spec allows)
  - `gen_ai.request.model`, `gen_ai.response.model`
  - `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`
  - duration, and the Zedek task profile

  Names follow the current OpenTelemetry GenAI registry (`gen_ai.provider.name` replaced the deprecated `gen_ai.system`).
- **Token counts are captured from each provider's response:** the OpenAI-compatible `usage` block (Groq, NVIDIA NIM, OpenRouter, Cerebras), Gemini's `usageMetadata`, and Ollama's `prompt_eval_count` / `eval_count`. A missing usage block records `null`, never an error.
- `generate_chat()` results gain `model` and `usage` keys (additive; `answer` and `source` are unchanged).
- `provider_stats()` gains per-provider session token totals.

## Capabilities

### New Capabilities
- `request-tracing`: one identifier per user turn across all modules, a standard record for every language-model call, and token accounting.

### Modified Capabilities
- `llm-provider-chain`: the usage report adds session input/output token totals per provider.

## Impact

- **Code:**
  - `zedek_logger.py`: trace context and a filter.
  - `orchestrator.py`: `handle()` opens a trace.
  - `llm_provider.py`: provider functions return text plus model and usage, the GenAI event is logged, and token totals are added to stats.
  - Test stubs that return a bare string from a provider function are updated.
- **Logs:** every line inside a turn gains `trace_id` and `gen_ai.conversation.id`. Nothing is removed; existing fields such as `source` stay.
- **Privacy:** no prompt or response text is added to logs. Only counts, model names, and IDs.
- **Tier gate / watchdog:** none.
- **Untrusted-input path:** none. Usage fields are read as integers only.
- **Dependencies:** none. `contextvars` is in the standard library.

## Non-goals

- An OpenTelemetry SDK, an exporter, or a tracing backend (Langfuse, Phoenix). The field names make that a later configuration change.
- Cost in currency. Every provider in use is a free tier, and token counts are what free-tier limits are measured in.
- Spans with parent/child timing. A flat event per LLM call plus a shared `trace_id` covers the "what happened for this message" need at a fraction of the complexity.
- Propagating the trace into MCP server subprocesses. Their logs are separate processes, and the client-side call is traced.
