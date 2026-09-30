# Proposal

Implements **ROADMAP E1**, phase 2 of 2 (dispatch and help from the capability registry).

## Why

Phase 1 derived every routing and tier table from `capabilities/*.yaml`, but `orchestrator.execute()` was still a 200-line if-chain naming each intent. Adding a capability still meant editing it, and nothing tested that every routable intent had a branch.

## What Changes

- Each capability file names its `handler` (a function in `orchestrator.py` taking `(decision, domain)`) and a one-line user-facing `summary`.
- `execute()` looks the capability up and calls its handler. Unknown names are blocked as before; qualified `mcp_*` names still go to the MCP gate.
- The branch bodies moved verbatim into `_run_<intent>` handlers (checked mechanically: coding, remember-fact and native-function code is identical after dedent). The seven system_agent functions share `_run_native_function`: allowlist, argument coercion, **tier gate**, run.
- `runs_when_unsure: true` (coding_task only) makes explicit the old ordering in which coding ran before the low-confidence fallback, because its own gate, plan approval and apply approval stand in for router confidence.
- A handler name that does not exist stops Zedek at import.
- REPL: `help` (or `/help`, `?`) prints what Zedek can do, generated from the summaries.

## Deferred (recorded in ROADMAP)

Requirement checks (binaries, env vars, MCP servers). Declared wrongly they would hide working capabilities, and the agents that depend on MCP servers already report a missing server.

## Capabilities

### Modified Capabilities
- `capability-registry`: dispatch and help are derived too.

## Impact

- **Code:** `orchestrator.py`, `capabilities/`.
- **Tier gate:** unchanged calls in unchanged places; a test proves the native path still gates and that low confidence still never acts (except coding, as before).
- **Behavior:** unchanged, except a literal `general_question` function name (never produced by the classifier) is now answered instead of blocked.
