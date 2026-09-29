# Tasks

- [x] 1.1 Add `_choose_model`, `_DEAD_MODELS`, and resolvers that raise on no usable model, plus `resolve_cerebras_model` with a catalog fetch; refresh all candidate lists (D1, D2). Verify with tests:
  - first live candidate wins
  - a rotted catalog → the provider is skipped and not counted
  - an unreachable catalog → the first candidate is used
  - a dead model is skipped
  - an env override is honored, but not once it is dead
- [x] 1.2 Add `_with_model_fallback` to every cloud provider function (D3). Verify with tests: a 404 retires the model and the request succeeds on the next candidate; the next request goes straight to the second; a non-404 error is not retried.
- [x] 1.3 Give 402 the 1-hour auth cooldown (D4). Verify: the existing cooldown tests, plus a 402 case.
- [x] 1.4 Live check: resolve and call each provider with a one-word prompt and report the model used. Update ROADMAP C3. Run the full suite. Verify: 0 failures; report the count before (846) and after.
- [x] 1.5 (found in the live check) An HTTP 200 whose body is `{"error": {"code": N}}` (OpenRouter, when an upstream fails) raises an HTTP error carrying status N, so 429 cools the provider down and 404 retires the model. Verify: tests for embedded 429, embedded 404, and malformed bodies.

### Live check result (2026-09-29)
- Plain requests:
  - gemini → `gemini-3-flash-preview` (resolved from the live catalog)
  - groq → `openai/gpt-oss-120b`
  - nvidia_nim → `nvidia/nemotron-3-super-120b-a12b`
  - openrouter → `nvidia/nemotron-3-super-120b-a12b:free`
  - cerebras → 402 Payment Required, which now triggers the 1 h cooldown
- F2 structured re-check: gemini (native), groq (strict), openrouter (hint), and local (native) all produced valid output. NIM gave a transient 503.
