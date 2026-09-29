# Design

## Context

See proposal.md for why.

- `zedek_logger.get_logger(name)` builds a `logging.Logger` with a `pythonjsonlogger.JsonFormatter`, writing to `logs/<name>.log` and the console. The formatter serializes a record's non-reserved attributes, so an attribute a filter adds appears in the JSON.
- `orchestrator.handle(user_input)` is the single entry point for a user turn. The ambiguity guard, the decomposition path, and the single path all run inside it.
- Provider functions (`_gemini`, `_groq`, `_nvidia_nim`, `_openrouter`, `_cerebras` via `_openai_compatible`, and `_local`) return a plain `str`. The raw JSON, where usage lives, is discarded.
  - OpenAI-compatible responses carry `usage.prompt_tokens`, `usage.completion_tokens`, and `model`.
  - Gemini carries `usageMetadata.promptTokenCount` and `usageMetadata.candidatesTokenCount`.
  - Ollama chat carries `prompt_eval_count`, `eval_count`, and `model`.

## Decisions

### D1. Trace context via `contextvars` + a logging filter
- `zedek_logger` gets `_trace_id: ContextVar[str | None]` (default `None`) and a module-level `SESSION_ID` (a uuid4 hex, created once per process).
- `trace_context()` is a context manager that sets a fresh `uuid4().hex[:16]` trace ID and resets it on exit, via the ContextVar token, so nesting is safe.
- A `_TraceFilter` attached to every handler sets `record.trace_id` and `record.__dict__["gen_ai.conversation.id"]` only while a trace is active. Outside a turn it adds nothing, which satisfies "no turn identifier outside a turn".
- **Why contextvars:** no signature changes across dozens of functions, and the context carries into `asyncio.run` (the MCP client), which copies it. **Alternative rejected:** passing `trace_id` explicitly, which would touch every call chain and still miss third-party callbacks.
- Loggers already configured before this change (the `if logger.handlers` early return) still get the filter, because the filter is added in `get_logger` at creation time and every module logger is created through it at import.

### D2. `handle()` is the trace boundary
- The body of `orchestrator.handle()` runs inside `with trace_context():`, so one user message equals one trace, including every sub-task of a decomposed request. The ID is logged once at the start as `turn_started`.

### D3. Provider functions return a `ProviderReply`
- `ProviderReply(text: str, model: str | None, input_tokens: int | None, output_tokens: int | None)`, a frozen dataclass.
- `_openai_compatible`, `_gemini`, and `_local` read the usage fields defensively. Only `int` values are kept (`bool` is excluded, since it is a subclass of int), and anything else becomes `None`.
- `generate_chat()` uses `reply.text` exactly where it used the string before, so the returned `answer` and `source` are unchanged. It adds `model` and `usage` (`{"input_tokens", "output_tokens"}`).
- **Test stubs** that return a bare `str` from a `_PROVIDER_FUNCS` entry are updated to return `ProviderReply`. There is deliberately no str-compatibility shim.

### D4. One `gen_ai.client.operation` event per successful call
- The event has these fields: `gen_ai.operation.name="chat"`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `zedek.duration_ms`, `zedek.task`, and `source`.
- Provider names: `gemini→gcp.gemini`, `groq→groq`, `nvidia_nim→nvidia_nim`, `openrouter→openrouter`, `cerebras→cerebras`, `local→ollama`.
- `gen_ai.request.model` is the model Zedek asked for (resolved before the call). `gen_ai.response.model` is what the provider reported, falling back to the request model.
- **No prompt or response text is ever put in the event.**

### D5. Token totals in `provider_stats()`
- `_ProviderHealth` gains `input_tokens` and `output_tokens`, summed on success when the values are known. `provider_stats()` reports them.
- Local calls are tracked under the key `local` in the event only. `provider_stats()` keeps its cloud-provider scope from C2.

## Risks / Trade-offs

- [Some providers omit usage on streaming or on certain models] → The fields are `null` and the call still succeeds (spec scenario).
- [GenAI semconv is still "Development"] → Names may change upstream. They live in one mapping in `llm_provider.py` and are cheap to rename.
- [A thread started inside a turn doesn't inherit the ContextVar] → Zedek's turn path is synchronous or asyncio-based. A future thread pool would need `contextvars.copy_context().run`, which is noted in the logger docstring.

## Migration Plan

- Additive. Log consumers see new fields only. Rollback: revert the commit.
