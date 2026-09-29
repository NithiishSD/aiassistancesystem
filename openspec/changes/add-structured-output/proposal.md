# Proposal

Implements **ROADMAP F2** (schema-constrained JSON on every provider).

## Why

Every structured LLM call in Zedek asks for "some JSON":
- `response_format: json_object`
- Gemini `responseMimeType`
- Ollama `format: "json"`

That guarantees valid syntax, not the right shape. Each call site then hand-parses with `json.loads` plus `.get()` and silently degrades on a wrong shape. Examples:
- The intent fallback reads an unknown intent name as "general question".
- A fact correction with a string index is dropped.
- Fact canonicalization parses free text line by line, with a `NO_FACT` sentinel.

Independent evidence: small models (0.6B–4B) go from 7–21% schema-invalid output to 0% under constrained decoding. JSONSchemaBench found that constrained decoding also *raised* task accuracy by up to ~4 points. The local fallback (llama3.1:8b) is the model that most needs it.

## What Changes

- **`llm_provider.generate_structured(messages, Model, task=...)`** takes a Pydantic model and returns the usual result plus `data`, a validated instance of `Model`. It uses the same provider chain, cooldowns, budgets, and tracing as `generate_chat()`.
- **Native schema mode per provider**, via one hand-written adapter:

  | Provider | Mode |
  |---|---|
  | Gemini | `responseJsonSchema` |
  | Groq | strict `json_schema` on the gpt-oss models (the only ones where strict is honored); `json_object` on the others |
  | Cerebras | strict `json_schema` |
  | NVIDIA NIM | `nvext.guided_json` |
  | Ollama | `format=<schema>` |
  | OpenRouter | `json_object`, because free-model support varies |

  When a mode is `json_object`, the schema is appended to the prompt as text.
- **Downgrade on rejection.** If a provider answers a native-schema request with HTTP 400, the same request is retried once in `json_object` mode, and that provider/model is remembered as unsupported for the session. A hosted endpoint that lacks a feature therefore costs one extra call, not a lost provider.
- **Validate, re-ask once, then move on.** A reply that fails validation is sent back once to the same provider along with the validation error. If the second reply also fails, the chain moves to the next provider. If no provider produces a valid reply, the call raises `StructuredOutputError`, and callers keep their existing safe fallbacks.
- **Call sites converted.** These are the ones ROADMAP F2 names, plus the academic tracker, which has the same shape:
  - Intent fallback (`classifier.query_llm_with_tools`): a **single-field enum** schema over the valid intent names. The unused `arguments` object is dropped.
  - Fact canonicalization (`orchestrator.canonicalize_fact`): `{facts: [{attribute, value}]}` replaces line parsing and the `NO_FACT` sentinel.
  - Fact correction (`orchestrator._handle_correction`): `{index: int|null, corrected_fact: str|null}`.
  - Research query planning (`research_agent.plan_queries`): `{queries: [str]}`, 1–3 items.
  - Academic intent (`orchestrator._extract_academic_intent`): `action` and `result` are enums.
  - MCP argument extraction (`orchestrator`): the local Ollama call is constrained by **the tool's own input schema**. It is still validated by `jsonschema` before the call.

## Capabilities

### New Capabilities
- `structured-output`: schema-constrained, validated structured replies from any provider in the chain.

## Impact

- **Code:**
  - `llm_provider.py`: the adapter, the validation loop, and `generate_structured`. The provider functions gain an optional `schema` keyword, and existing two-argument stubs keep working.
  - New module `llm_schemas.py`: the Pydantic models.
  - The call sites above, and the tests that mocked `generate_chat` for those sites.
- **Behavior:**
  - A wrong-shaped reply now costs at most one re-ask per provider, instead of silently producing a wrong result.
  - Canonicalized facts keep the `User's <attribute>: <value>` storage format, so stored memory is unchanged.
- **Tier gate / watchdog:** none.
- **Untrusted-input path:** none new. Validated data is strictly typed.
- **Dependencies:** none. `pydantic` is already pinned.
- **Quota:** a re-ask is one extra request on the same provider, and it counts toward that provider's budget.

## Non-goals

- Instructor, Outlines, or similar libraries (rejected in the roadmap: extra dependencies for something one adapter covers).
- Converting the free-form planners (web agent, task planner, evaluator, coding agent). They can adopt `generate_structured` later, one at a time.
- Tool calling (function-calling APIs). The intent fallback only needs one enum value.
