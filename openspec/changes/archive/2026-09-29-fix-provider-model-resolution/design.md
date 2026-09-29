# Design

### D1. `_choose_model(source, candidates, live) -> str | None`
- `usable(m)` is `(source, m) not in _DEAD_MODELS`.
- If `live` is non-empty, return the first usable candidate that appears in `live`. Otherwise return `None`, and log `model_candidates_stale` (a warning) with the provider name.
- If `live` is empty (the catalog is unreachable or has no key), return the first usable candidate.
- Resolvers (`resolve_gemini_model`, `resolve_groq_model`, `resolve_nvidia_model`, `resolve_openrouter_model`, and the new `resolve_cerebras_model`) raise `RuntimeError("<source>: no usable chat model (not configured)")` on `None`. The chain already treats "not configured" as a skip that is not counted against the quota.
- **Alternative rejected:** a regex that filters out non-chat model names and then picks the first remaining entry. It would keep providers available when candidates rot, but it recreates the original bug in a subtler form (an arbitrary, untested model), and it would need upkeep as vendors add model types.

### D2. `_model_for(source, env_var, resolver)`
Returns the env override when it is set and not dead, and otherwise the resolver's choice.

### D3. `_with_model_fallback(source, pick, send)`
- `model = pick()`, then `send(model)`.
- On an `HTTPError` with status 404:
  - add `(source, model)` to `_DEAD_MODELS`
  - log `model_unavailable`
  - call `pick()` again
  - if the result is the same model (which can't normally happen) or the pick raises, re-raise
  - otherwise `send(new_model)` once
- Other errors propagate unchanged, so the quota and cooldown logic still applies.
- Used by `_gemini`, `_groq`, `_nvidia_nim`, `_openrouter`, and `_cerebras`.

### D4. 402 cooldown
`_cooldown_for` treats 401, 402, and 403 the same way, with `AUTH_FAILURE_COOLDOWN` (1 h).

### Candidate order rationale
- Groq `gpt-oss-120b` first: it is the strongest free model there and supports strict schema mode (F2).
- NVIDIA `nemotron-3-super-120b` first: it answered in 1.3 s, whereas `gpt-oss-20b` took 10.4 s.
- OpenRouter's popular free models were returning 429 at probe time. The one that answered goes first, and the others follow.

### D5. Error bodies (added during apply)
`_post_openai_compatible` raises `requests.HTTPError` with a stand-in response carrying the body's `error.code` when `choices` is missing. OpenRouter returns this shape with HTTP 200 when an upstream model fails. Before this, it surfaced as a `KeyError: 'choices'` with no cooldown.
