# Proposal

Implements **ROADMAP C3** (provider model resolution), which the F2 live check found on 2026-09-29.

## Why

Model names are resolved against each provider's live catalog, and the fallback was "the catalog's first entry". Every candidate list had rotted:
- **Groq.** None of the four llama candidates exist any more, so Zedek sent requests to whatever came first in the catalog. On two consecutive runs that was `gpt-oss-safeguard-20b` (a safety classifier) and then `gpt-oss-20b`. The catalog also includes Whisper, prompt-guard, and text-to-speech models.
- **OpenRouter.** It picked an arbitrary `:free` model, which returned 400 for JSON mode.
- **NVIDIA NIM.** Its catalog lists `llama-3.1-nemotron-70b-instruct`, but chat requests to it return 404, as they do for two other listed models. A listed model is not proof that it is served.
- **Cerebras.** Its model is hard-coded (`llama-3.3-70b`), which returns 404. Every model now returns **402 Payment Required**. That status had no cooldown, so Cerebras was retried on every request.

The effect is that answers come from an unintended model, or a provider silently costs a failed round-trip on every request.

## What Changes

- **Refreshed candidate lists.** Every model here was probed live on 2026-09-29, except Gemini's, which is unreachable from this machine; those come from Google's current free-tier lineup.
  - Groq: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`
  - NVIDIA: `nvidia/nemotron-3-super-120b-a12b` first, then `openai/gpt-oss-20b`
  - OpenRouter: `nvidia/nemotron-3-super-120b-a12b:free` first
  - Cerebras: `gpt-oss-120b`, `qwen-3.8-27b`
  - Gemini: the 3.x Flash line, then 2.5/2.0
- **No arbitrary fallback.**
  - If the catalog is readable and no candidate is in it, the provider is **skipped** as "no usable chat model". This is treated like a missing key: it is not counted against the quota, and a warning names the provider, so the list can be updated.
  - If the catalog can't be read, the candidates are tried in order.
- **A 404 marks the model dead** for the session (`model_unavailable`), and the same request is retried once with the next resolvable model. The resolver never returns a dead model again.
- **Cerebras resolves from its live catalog** like the others. `CEREBRAS_MODEL` is still an override.
- **402 Payment Required gets the 1-hour auth cooldown**, the same as 401/403.
- An env override (`GROQ_MODEL`, etc.) is honored unless it has been marked dead.

## Capabilities

### Modified Capabilities
- `llm-provider-chain`:
  - Payment-required counts as an authorization failure.
  - New requirements: providers only receive requests for a known chat model, and unavailable models are retired for the session.

## Impact

- **Code:** `llm_provider.py` only (the resolvers, the provider functions, and `_cooldown_for`).
- **Tier gate / watchdog / untrusted input:** none.
- **Dependencies:** none.
- **Quota:** fewer wasted requests. Dead models and payment-required providers stop being retried.

## Non-goals

- A background health checker or live probing at startup. Resolution stays lazy.
- Choosing models by benchmark quality. The candidate order is a manual, documented choice.
