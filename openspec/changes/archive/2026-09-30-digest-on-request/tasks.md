# Tasks

## 1. Golden set

- [x] 1.1 Add 22 `daily_digest` rows (11 DEV, 11 TEST) to `evals/routing_golden.csv` before writing router phrases

## 2. Capability

- [x] 2.1 Add `capabilities/daily_digest.yaml`, list it in `index.yaml`, add `_run_daily_digest`; verify with tests: routed at Layer 1 with no model call, empty digest says so
- [x] 2.2 Measure DEV, then TEST; fix the news-request regression the gate found (two `mcp_tool` phrases, three DEV rows); verify with a test that news requests stay with `mcp_tool`

## 3. Integrate

- [x] 3.1 Regenerate `evals/routing_baseline.json`; confirm only the new class is added
- [x] 3.2 Update `ROADMAP.md` D1; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
