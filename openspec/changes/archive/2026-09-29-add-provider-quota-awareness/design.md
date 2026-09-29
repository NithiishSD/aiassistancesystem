# Design

## Context

See proposal.md for why. Current `llm_provider.generate_chat(messages, json_mode, force_local, task)`:

- Resolves the chain from `TASK_PROVIDERS[task]` or `DEFAULT_CHAIN`. For each cloud source it calls `_PROVIDER_FUNCS[source](messages, json_mode)`. On any exception it logs `provider_failed` and continues. Finally it calls `_run_local_or_raise`.
- The provider functions raise `RuntimeError("... API key is not configured")` **before any network I/O** when a key is missing. HTTP errors surface as `requests.exceptions.HTTPError` (from `raise_for_status()`), with `.response.status_code` and `.response.headers`.
- `DEFAULT_CHAIN` and several task chains include `openrouter`. Its `:free` tier allows 50 requests/day and 20 RPM (verified against its docs on 2026-09-29).

## Goals / Non-Goals

**Goals:** never contact a provider known to be exhausted or mis-keyed, respect verified daily caps across restarts, and make every fall-through explainable.

**Non-Goals:** see proposal.md.

## Decisions

### D1. In-memory health state plus a persisted daily counter
- `_health: dict[str, _ProviderHealth]`, a dataclass with `calls`, `successes`, `failures`, `rate_limited`, `cooldown_until` (epoch seconds), and `last_error`. It is session-scoped and guarded by a `threading.Lock`.
- The daily counts live in `data/provider_usage.json` as `{"YYYY-MM-DD": {"provider": n}}`. Only today's key is kept when writing, so the file never grows. Writes are atomic (temp file + `os.replace`), like `_save_dynamic_utterances`.
- The path is resolved at call time: `os.getenv("ZEDEK_PROVIDER_USAGE_PATH")` or the default. **Why:** tests can then redirect it (D6) without monkeypatching module constants before import.

### D2. What counts as a request
- A request counts toward today's usage when the provider function was called and did **not** raise the "not configured" `RuntimeError`. That covers success, HTTP errors, and timeouts, because the request left the machine and may count against the provider's quota.
- **Why conservative:** undercounting would let the 50/day cap be exceeded. Overcounting only skips OpenRouter slightly early, and it is last in the chain anyway.

### D3. Cooldown sources and bounds
- **429:** `Retry-After` in integer seconds. Otherwise `x-ratelimit-reset-requests` or `x-ratelimit-reset-tokens` in Groq/OpenAI style ("2m59.56s", "12s", "450ms"), parsed by a small regex. Otherwise 60 s. The value is clamped to [1 s, 3600 s].
- **401/403:** 3600 s.
- **Anything else** (5xx, timeouts, connection errors): no cooldown. The existing try-next behavior stays, so a transient blip doesn't sideline a provider.
- Header values are parsed as numbers only. An unparseable value falls back to the default and is never an error.

### D4. Skip checks happen before calling
- Before each cloud source in the chain: if `now < cooldown_until`, skip with reason `cooldown`. If `DAILY_REQUEST_BUDGETS.get(source)` exists and today's count is at or above it, skip with reason `daily_budget`.
- Skips log `provider_skipped` with `{source, reason, seconds_remaining | used/budget}`.
- The local fallback is unchanged. It is always the last resort, and `force_local` and `ALLOW_CLOUD=false` behave as before.

### D5. Chain reorder
- `process_reasoning`: `["openrouter", "gemini", "cerebras", "local"]` → `["gemini", "groq", "cerebras", "openrouter", "local"]`.
- `coding`: `["nvidia_nim", "openrouter", "groq", "local"]` → `["nvidia_nim", "groq", "openrouter", "local"]`.
- `DEFAULT_CHAIN` already has openrouter fourth, so it is unchanged. All chains stay bounded.

### D6. Tests never touch user data
- A new `tests/conftest.py` with a session-scoped autouse fixture sets `ZEDEK_PROVIDER_USAGE_PATH` to a temporary file, so no test run writes `data/provider_usage.json`.
- Unit tests reset `_health` through a small `_reset_health_for_tests()` helper.

### D7. Reporting
- `provider_stats() -> dict[str, dict]` returns `calls`, `successes`, `failures`, `rate_limited`, `today`, `budget`, and `cooldown_remaining_s` for each provider in `_PROVIDER_FUNCS`.
- `format_provider_stats()` renders a one-line-per-provider summary. The orchestrator logs `provider_stats_session_end` from its `quit` path.

## Risks / Trade-offs

- [A provider's Retry-After is huge or bogus] → Clamped to 1 h.
- [A provider starts counting quota differently] → Budgets exist only for verified caps. Others rely on 429 cooldowns, which adapt automatically.
- [Two Zedek processes on the same day race on the usage file] → Last writer wins, so a count may be off by a few. That is acceptable for a single-user assistant, and the 429 cooldown is the backstop.
- [The clock changes or the machine suspends] → Cooldowns use wall time. The worst case is one extra attempt, which just re-triggers the cooldown.

## Migration Plan

- Additive. No existing data is changed, and `data/provider_usage.json` is created on the first counted request.
- Rollback: revert the commit. The usage file can simply be deleted.
