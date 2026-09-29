# Tasks

## 1. Health state, usage persistence, and test isolation

- [x] 1.1 Add `tests/conftest.py` with a session-scoped autouse fixture that points `ZEDEK_PROVIDER_USAGE_PATH` at a temp file (design D6), and add `data/provider_usage.json` to `.gitignore`. Verify: a test asserts the resolved usage path is under the pytest temp dir, and `git check-ignore data/provider_usage.json` succeeds.
- [x] 1.2 Add `_ProviderHealth`, `_health`, the lock, `_reset_health_for_tests()`, and the usage load/increment/save helpers (atomic write, only today's key kept) per design D1. Verify: `tests/test_provider_quota.py` checks that counts persist across a simulated restart (reload from the file), reset on a new date (monkeypatched date), keep only today in the file, and that a corrupt file loads as empty without raising.

## 2. Cooldowns, budgets, and skipping

- [x] 2.1 Header parsing and cooldown rules per design D3. Verify: tests cover `Retry-After: 30` → 30 s; Groq-style `"2m59.56s"` → about 180 s; `"450ms"` → the 1 s floor; a missing or garbage header → 60 s; `99999` → the 3600 s cap; 401/403 → 3600 s; 500 → no cooldown.
- [x] 2.2 Pre-call skip checks and request counting in `generate_chat()` per designs D2 and D4. Verify: tests with `_PROVIDER_FUNCS` patched show that a provider raising 429 with `Retry-After: 30` is not called again within 30 s (monkeypatched time) and is called after. A provider at its daily budget is not called. A "not configured" `RuntimeError` does not increment usage. When every cloud provider is cooling down, local is used and no cloud function is called. Each skip logs `provider_skipped` with its reason.
- [x] 2.3 Reorder `TASK_PROVIDERS` per design D5, and add `DAILY_REQUEST_BUDGETS = {"openrouter": 50}`. Verify: a test asserts openrouter is not first in any chain, every chain ends with `"local"`, and the budget value.

## 3. Reporting

- [x] 3.1 `provider_stats()` and `format_provider_stats()` per design D7, plus the orchestrator session-end log. Verify: a test drives one rate-limited provider and one successful one, then asserts the reported `rate_limited`, `cooldown_remaining_s`, `successes`, and `today` values.

## 4. Finish

- [x] 4.1 Update `TECH_STACK.md` (the cloud chain row) and ROADMAP C2, and run the full suite `./zedek-env/bin/python -m pytest -q`. Verify: 0 failures; report the count before (691) and after. Also confirm `data/provider_usage.json` was **not** created by the test run.
