# Tasks

## 1. Core

- [x] 1.1 Add `llm_schemas.py` with the models from D1/D5 and `json_schema_for()` from D2. Verify with tests:
  - schemas have `additionalProperties: false`, every property is required, and there are no `$ref`/`title` keys
  - `IntentChoice`'s enum equals `VALID_INTENT_NAMES`
- [x] 1.2 Add the provider adapter from D3: a `schema` keyword on every provider function, the native forms, the schema hint, and the 400 downgrade that records the provider/model in `_SCHEMA_UNSUPPORTED`. Verify with `requests.post` / `ollama.chat` mocked, checking the exact payload per provider, including both Groq model families and the downgrade on a 400.
- [x] 1.3 Add `generate_structured()` and the shared `_dispatch()` (D4). Verify with tests:
  - valid first reply
  - corrected on re-ask
  - still wrong → next provider
  - all invalid → `StructuredOutputError`
  - a re-ask is counted in `provider_stats` and logged
  - the reply text is not in the invalid-output log
  - `<think>` and code-fenced JSON parse
  - `generate_chat` behavior is unchanged (the existing tests pass)

## 2. Call sites

- [x] 2.1 Convert the intent fallback, canonicalization, correction, query planning, and academic intent (D5), and update the tests that mocked `generate_chat` for them. Verify: tests for each site's success and fallback path, and the canonicalization scenarios in the spec.
- [x] 2.2 Constrain MCP argument extraction with the tool's input schema. Verify: a test asserts that `ollama.chat` receives the schema as `format`.

## 3. Finish

- [x] 3.1 Live check: run `generate_structured` against each configured provider and the local model with a small model, and report which mode each used. Update ROADMAP F2 and the `TECH_STACK.md` LLM row. Run the full suite and the routing eval. Verify: 0 failures; report the count before (793) and after.

### Live check result (2026-09-29)
- groq (`openai/gpt-oss-20b`): strict `json_schema`, valid.
- local (`llama3.1:8b`, `format=<schema>`): valid. Canonicalization end to end gave two facts, one fact, and none for a question.
- gemini: unreachable from this machine at the time (connection error, not an API error).
- nvidia_nim and cerebras: 404 on the configured model, including plain requests without a schema.
- openrouter: resolved to an arbitrary free model, and it returns 400 on `json_object`.

All three are pre-existing catalog drift, not F2 regressions. They are the next change (provider model resolution).
