# Design

- **Late lookup:** `_capability_handler` resolves the handler from module globals at call time, so `patch("orchestrator._run_x")` works and tests exercise the real dispatch.
- **Import-time check:** `_check_capability_handlers()` at the bottom of `orchestrator.py` raises `CapabilityError` for a missing handler.
- **Order in `execute()`:** `runs_when_unsure` handlers → low-confidence fallback → default intent for `None` → qualified `mcp_*` names (only when not a capability) → registry lookup or block. Same order as the old chain.
