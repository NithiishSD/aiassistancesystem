# Design

### D1. Adoption gate
Measured on the **dev** slice only (the test slice stays held out), cloud disabled, both models warm:
- Layer-2 intent accuracy on the rows Layer 1 escalates (same path as `routing_eval_e2e.py`).
- Structured-output validity: share of those Layer-2 calls (a schema-constrained intent choice) that return a valid object. The golden set has no argument labels, so argument quality is not scored.
- Median latency per call.

Adopt `qwen3:8b` as the default only if intent accuracy and structured validity are each ≥ llama3.1's and median latency is ≤ 1.5× llama3.1's. Otherwise the default stays `llama3.1:8b` and only the plumbing (D2, D3) ships, with the numbers recorded in ROADMAP D3.

### D2. `local_chat`
`local_chat(messages, *, format=None, stream=False)` calls `ollama.chat(model=LOCAL_MODEL, messages=..., think=False, ...)`. Replies are also passed through `strip_thinking_tags` by existing callers, so a model that ignores `think` still never shows a trace. `_local`, `_local_stream` and the orchestrator's direct calls use it.

### D3. One model setting
`LOCAL_MODEL = os.getenv("ZEDEK_LOCAL_MODEL", <default>)`, read at import like the other model settings. `orchestrator.ROUTING_MODEL = llm_provider.LOCAL_MODEL`.
