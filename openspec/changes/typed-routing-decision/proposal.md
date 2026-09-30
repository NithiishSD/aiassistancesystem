# Proposal

Implements **ROADMAP E2** (typed state between pipeline steps).

## Why

`route_request → execute` passed a free-form dict. Its keys were read in ~25 places with defaults (`decision.get("_original_input", "")`), so a misspelled key silently became the default instead of failing, and the input text was patched in after routing (`decision["_original_input"] = user_input`).

## What Changes

- `routing_decision.RoutingDecision` (dataclass): `function`, `user_input`, `domain` (normalized to personal/academic), `confidence` (`high`/`low`, validated), `score`, `via_llm`, `args`, `clarify`. `log_fields()` is what routing logs record (no args or input text).
- `route_request` builds it with the input text; `_handle_single` no longer mutates it; every handler reads attributes.
- `execute()` still accepts the legacy dict (tests and any caller) but converts it at once with `from_dict`, which **raises on unknown keys**.

## Found, not changed (recorded in ROADMAP)

`_handle_correction` is meant to un-learn the router phrase from a *previous* misrouted turn, but `LAST_ROUTING_DECISION` is overwritten with the *current* (correction) turn before `execute()`, so the self-heal never sees the earlier turn. Fixing it would start deleting learned phrases whenever a fact is corrected, even when the earlier routing was right ("i live in X" → "that's outdated"). Behavior is preserved exactly; the owner decides.

## Capabilities

### Modified Capabilities
- `intent-routing`: the routing decision is typed.

## Impact

- **Code:** new `routing_decision.py`; `orchestrator.py`.
- **Tier gate / behavior:** unchanged.
