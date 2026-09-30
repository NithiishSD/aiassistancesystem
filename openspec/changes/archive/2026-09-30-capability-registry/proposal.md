# Proposal

Implements **ROADMAP E1**, phase 1 of 2 (one declarative capability registry).

## Why

A capability is defined in five hand-kept tables: router utterances and thresholds in `classifier.py`, the Layer-2 tool list in `classifier_tools.py`, the tier table in `tier_gate.py`, and argument types in `orchestrator._coerce_arg_types`. They desynced three times in one session. Nothing tests that `ROUTER_TOOLS` names are routable, that every native function has a tier, or that coerced argument names exist.

## What Changes

- `capabilities/<name>.yaml`, one per intent (17): `name`, `description` (the only text an LLM sees), `utterances`, `short_examples`, and optional `threshold`, `tier`, `arg_types`. `capabilities/index.yaml` lists the route order and the Layer-2 prompt order.
- `capabilities/__init__.py` loads them with `yaml.safe_load` and **fails closed**: an unknown key, an out-of-range tier or threshold, a name mismatch, or a file/index disagreement raises at import.
- `INTENT_UTTERANCES`, `SHORT_EXAMPLE_UTTERANCES`, `GENERAL_ANCHOR_UTTERANCES`, `STRICT_INTENT_THRESHOLDS`, `ROUTER_TOOLS`/`VALID_INTENT_NAMES`, `FUNCTION_TIERS` and the int-argument table are derived from it.

**Byte-for-byte migration:** all derived tables equal the old ones, the Layer-2 prompt is byte-identical (prompt caching unaffected), and every golden routing decision on dev and test is unchanged.

## Phase 2 (a later change)

Handlers and the `execute()` dispatch move into capabilities; requirement checks (binaries, env vars, MCP servers) and a generated help text. Kept separate because `execute()` carries per-intent approval and watchdog logic.

## Capabilities

### New Capabilities
- `capability-registry`: where capabilities are defined and how they are loaded.

## Impact

- **Code:** new `capabilities/`; `classifier.py`, `classifier_tools.py`, `tier_gate.py`, `orchestrator.py` now derive their tables.
- **Tier gate:** same tiers, now pinned by `tests/test_capabilities.py`; any tier change must edit that test. Unknown functions still fail safe to Tier 3.
- **Dependencies:** none (`pyyaml` already pinned).
