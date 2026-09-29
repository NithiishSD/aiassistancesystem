# Proposal

Implements **ROADMAP C2** (provider accounting). ROADMAP C1 (per-turn tracing and OTel GenAI field names, including token counts) is a separate follow-up change.

## Why

Zedek runs on five free-tier providers (Gemini, Groq, NVIDIA NIM, OpenRouter, Cerebras) with a local fallback. After the Layer-1 coverage change, about 48% of messages still reach an LLM for routing, and most handlers make further LLM calls. Quota is the scarce resource, and today the provider chain is blind to it:

- **No memory of rate limits.** When a provider returns 429, `generate_chat()` logs it and moves on, then calls the same exhausted provider again on the very next request. Every message pays a wasted round-trip, and a request that times out can burn up to the 12-second timeout each time.
- **No daily budget.** OpenRouter's `:free` tier allows **50 requests/day** (verified against its docs), yet `TASK_PROVIDERS` lists it *first* for `process_reasoning` and second for `coding`. So the scarcest provider is hit first.
- **No visibility.** There is no way to see how many calls each provider served today, which ones are cooling down, or why a request fell through to the local 8B model. Falling to local is an invisible accuracy drop.

## What Changes

- **Cooldown after rate limiting.** On HTTP 429 the provider is skipped until its retry time passes. The time comes from `Retry-After` or the provider's rate-limit reset header, defaults to 60 s, and is capped at 1 h. On 401/403 (a bad or missing-scope key) it is skipped for 1 h instead of being retried on every call.
- **Daily request budgets** for providers with a known free-tier cap, starting with `openrouter: 50`. Each day's per-provider request counts are persisted locally so the budget survives restarts, and a provider at its budget is skipped until the next day.
- **Chain order by quota headroom.** OpenRouter moves to the end of `process_reasoning` and after Groq in `coding`. The chain stays bounded.
- **`provider_stats()`** reports, per provider, the session's calls, successes, failures, and rate-limit hits, plus today's request count, the budget, and any active cooldown. A one-line summary is logged when a session ends.
- Every skip is logged with its reason (`cooldown`, `daily_budget`), so a fall-through to local is explainable.

## Capabilities

### New Capabilities
- `llm-provider-chain`: how Zedek chooses among LLM providers. This covers order, skipping rate-limited or over-budget providers, daily budgets that survive restarts, the local fallback, and usage reporting.

### Modified Capabilities
<!-- None. -->

## Impact

- **Code:**
  - `llm_provider.py`: health state, cooldown, daily budget, usage persistence, `provider_stats()`, and the chain reorder.
  - `orchestrator.py`: logs the stats summary at session end.
  - New `tests/conftest.py` redirects the usage file to a temporary path so tests never write user data. That fixes the pattern flagged in the acknowledgement change for this new file.
- **New local data file:** `data/provider_usage.json` holds per-day request counts per provider. It contains no prompts or content, and it is gitignored.
- **Behavior:** fewer wasted calls and faster fall-through, since a known-exhausted provider is not contacted. When everything is cooling down, the local fallback is used exactly as today.
- **Tier gate / watchdog:** none.
- **Untrusted-input path:** none. Response headers are parsed as numbers only, with bounds.

## Non-goals

- Per-turn trace IDs, OTel `gen_ai.*` field names, and token/cost accounting (C1).
- LiteLLM or any routing library (rejected in the roadmap).
- Parallel or hedged provider calls (rejected; they double quota burn).
- Budgets for providers whose free-tier caps are not verified. They get cooldowns only, and more budgets can be added when a cap is confirmed.
- A user-facing chat command for stats. `provider_stats()` is the interface for now.
