# Design

## Decisions

### D1. Models live in `llm_schemas.py`, flat and fully required
- Every model sets `model_config = ConfigDict(extra="forbid")`, which makes the schema say `additionalProperties: false`. Strict mode on Cerebras and OpenAI-style providers requires that.
- Every field is required. Optional values are typed `X | None` without a default, because strict modes require every property to appear in `required`.
- Enums are `Literal[...]`.
- `IntentChoice` is built at runtime from `VALID_INTENT_NAMES` with `create_model`, so the enum always matches the router's tool list.

### D2. `json_schema_for(Model)`
- Takes `Model.model_json_schema()`, inlines `$ref`/`$defs` (for the nested `Fact` in `FactList`), and removes `title` keys recursively.
- One schema dict is sent to every provider. Gemini's `responseJsonSchema` accepts standard JSON Schema, including `anyOf` with `null`.

### D3. Per-provider adapter

| Provider | Native request | Fallback on HTTP 400 |
|---|---|---|
| gemini | `generationConfig.responseMimeType=application/json` + `responseJsonSchema` | the same request without `responseJsonSchema` |
| groq, gpt-oss model | `response_format={"type":"json_schema","json_schema":{"name":"response","schema":…,"strict":true}}` | `json_object` |
| groq, other model | `json_object` plus the schema hint | none |
| cerebras | strict `json_schema` (as above) | `json_object` |
| nvidia_nim | `nvext: {"guided_json": schema}` | `json_object` |
| openrouter | `json_object` plus the schema hint | none |
| local (ollama) | `format=schema` | `format="json"` plus the schema hint (on any client/server error) |

- The **schema hint** is a final user message: `Respond with only a JSON object matching this JSON schema: <compact schema>`. It is added only when the request is not natively constrained.
- `_SCHEMA_UNSUPPORTED: set[tuple[source, model]]` is filled on a 400 in native mode, so later calls go straight to the fallback.
- Provider functions gain `schema: dict | None = None`. `generate_structured` passes it as a keyword argument only when it is set, so existing `lambda messages, json_mode:` stubs and `generate_chat` are unchanged.

### D4. One chain loop, two entry points
- The body of `generate_chat()` moves into `_dispatch(messages, json_mode, force_local, task, schema=None, response_model=None)`. `generate_chat()` calls it without a schema, and its behavior is unchanged.
- Coding-task secret redaction, skips, the failure/cooldown bookkeeping, and the GenAI event apply to both entry points, and to every attempt including re-asks.
- With `response_model`, after a successful HTTP call:
  1. Parse `reply.text`: strip `<think>` blocks and a surrounding code fence, then `json.loads`, then `Model.model_validate`.
  2. On failure, log `structured_output_invalid` (source, attempt, the first error location and type; **never the reply text**). Re-ask the same provider once with `messages + [assistant: reply (truncated to 2,000 characters), user: "That reply did not match the required JSON schema: <error summary>. Reply again with only JSON that matches the schema."]`.
  3. If the second reply also fails, move to the next provider.
  4. The re-ask is a normal call: it is counted, traced, and can itself fail over.
- The local model is the last link. If it also fails validation, the call raises `StructuredOutputError`, a subclass of `AllProvidersUnavailableError`, so existing `except` clauses keep working.
- Return value: `_result(...) | {"data": instance}`. `answer` is the raw JSON text.

### D5. Call-site conversions
- **Intent fallback:**
  - `IntentChoice(function_name: Literal[*VALID_INTENT_NAMES])`.
  - The open_application → coding_task guard stays.
  - `llm_args` is returned as `{}`, since the key is kept for callers and was never read.
- **Canonicalization:**
  - Output is `FactList(facts: list[Fact(attribute: str, value: str)])`.
  - Each fact is rendered as `User's {attribute}: {value}`.
  - Facts with an empty or placeholder value (the existing rejected markers) are dropped.
  - An empty list means no fact.
  - On `StructuredOutputError` it returns `[]`, the same as the old NO_FACT answer.
- **Correction:** `FactCorrection(index: int | None, corrected_fact: str | None)`. The existing range check is kept.
- **Query planning:**
  - `ResearchQueries(queries: list[str])` with `max_length=3`. The schema carries `maxItems`.
  - The empty-list fallback to the raw question is kept.
- **Academic intent:**
  - `AcademicIntent(action: Literal["log","review","summary"], topic: str, result: Literal["solved","failed","partial",""], problem: str, difficulty: str, minutes: int)`.
  - The fallback is still `{"action": "review"}`.
  - It returns a `model_dump()` dict, so callers are unchanged.
- **MCP arguments:** `ollama.chat(format=tool_spec.input_schema)` when the schema is a non-empty object schema, otherwise `format="json"`. The existing `jsonschema` validation still runs.
