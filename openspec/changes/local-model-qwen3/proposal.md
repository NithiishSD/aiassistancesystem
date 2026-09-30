# Proposal

Implements **ROADMAP D3** (local model refresh).

## Why

The local model is the last link of every provider chain and does structured extraction (intent choice, function and MCP arguments, session fact extraction). With `ALLOW_CLOUD` off it is the *only* model. `llama3.1:8b` is from 2024; Qwen3 8B has the same footprint and is reported to follow instructions and emit JSON more reliably. The swap is only worth making if it measures better on Zedek's own evals, so this change is gated on a head-to-head comparison.

Two things make a bare rename unsafe:
- **Qwen3 thinks by default.** On a laptop CPU a reasoning trace costs tens of seconds per call and can break JSON-constrained output. Every local call must disable thinking. The pinned `ollama` client (0.3.3) cannot send `think`, so it is bumped to 0.6.3 (the only package that changes).
- **The model name lives in two places.** `llm_provider.LOCAL_MODEL` and `orchestrator.ROUTING_MODEL` (used by five direct `ollama.chat` calls). They are unified so a swap or rollback is one setting.

## What Changes

- `LOCAL_MODEL` reads `ZEDEK_LOCAL_MODEL` (default `qwen3:8b`: it passed the D1 gate); `orchestrator.ROUTING_MODEL` becomes an alias of it.
- One helper, `llm_provider.local_chat(...)`, wraps `ollama.chat` with `think=False`; every local call (provider chain, streaming, the orchestrator's direct calls) goes through it.
- `ollama` client pinned to 0.6.3 in `requirements.txt`.
- An opt-in eval, `evals/local_model_eval.py`, comparing candidate local models on the dev slice: Layer-2 intent accuracy with cloud off, structured-output validity, and latency.

## Capabilities

### Modified Capabilities
- `llm-provider-chain`: the local model is configurable and never emits a reasoning trace.

## Impact

- **Code:** `llm_provider.py`, `orchestrator.py`, `requirements.txt`, new `evals/local_model_eval.py`.
- **Tier gate / untrusted input:** none. The gate runs on the routed intent regardless of which model chose it; no new content reaches a model.
- **Rollback:** `ZEDEK_LOCAL_MODEL=llama3.1:8b`.

## Non-goals

- Changing cloud chains or `qwen2.5-coder` (the coding agent's local model).
- Enabling Qwen3's thinking mode for any task (a later change could, per task, with measurements).
