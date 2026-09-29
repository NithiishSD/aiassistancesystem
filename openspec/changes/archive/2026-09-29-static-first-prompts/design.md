# Design

### D1. Static-first message helper
`orchestrator._static_first(system: str, dynamic: str) -> list[dict]` returns `[{"role": "system", "content": system}, {"role": "user", "content": dynamic}]`. Each prompt's instructions move into a module-level `_*_SYSTEM` constant with no interpolation. The dynamic part is labelled blocks (`Statement:`, `Candidate facts:` …) so the instructions can refer to them by name.

Prompts converted: general Q&A, fact canonicalization, fact correction, fact acknowledgement, academic intent, read-only command generation, MCP tool pick, process reasoning. The classifier's Layer-2 prompt already has this shape (static system + user text) and is left as is.

### D2. General Q&A order
`[system: _GENERAL_QA_SYSTEM] + SESSION_HISTORY + [user: facts block + optional turn note + "User's message: …"]`. The system text says facts arrive with the user's latest message. History is append-only within a session, so the prefix of turn N+1 extends turn N's.

### D3. `llm_cache.py`
- `key(task, response_model, messages) -> str`: `sha256(json.dumps({"v": 1, "task", "schema": name, "json_schema", "messages"}, sort_keys=True))`.
- `get(key) -> dict | None`, `put(key, data_json, source, model)`. Table `entries(key TEXT PRIMARY KEY, data TEXT, source TEXT, model TEXT, created REAL)`. Expired rows (`> TTL_SECONDS`, 30 days) count as a miss and are deleted on read.
- Connection opened per call (single-user, low volume; avoids cross-thread sqlite issues). Every operation wrapped: on `sqlite3.Error`/`OSError` log `llm_cache_error` and return miss / no-op.
- `enabled()` reads `LLM_CACHE` each call, so tests and the owner can toggle it.

### D4. Wiring in `llm_provider.generate_structured`
New keyword `cache: bool = False`. When true and enabled: hit → `{"answer": data_json, "source": "cache", "model": model, "usage": {0, 0}, "data": response_model.model_validate_json(data_json)}` and log `llm_cache_hit`; a stored row that no longer validates (schema changed) is a miss. On miss, dispatch as usual; store only if `result["source"] != "local"`. `StructuredOutputError` propagates and nothing is stored.
