# Proposal

Implements **ROADMAP B6**, first bullet (OpenHands-style LLM-declared risk).

## Why

The tier gate's rules see the tool name, its description and pattern-matched arguments, but not what a specific call *means*. The model that fills in a call's arguments has that context. OpenHands has the model state a risk level inside the tool call at no extra inference cost; the effective tier is the higher of rule and label, so the model can raise a tier but never lower it (the gate's existing "lanes only raise" design).

## What Changes

- `tier_gate`: lane 4, `LLM_RISK_TIERS = {LOW: 0, MEDIUM: 1, HIGH: 2}`; `classify`/`gate` take `llm_risk`; effective tier = `max(rule tier, label)`. HIGH means confirm, never block (blocking stays a rule decision). Invalid or missing labels are ignored. A raise is logged.
- MCP argument extraction (the one place a model's choice reaches the tier gate) now asks for `{"args": <tool schema>, "risk": LOW|MEDIUM|HIGH}` in the same schema-constrained local call; `_execute_mcp_tool` passes the label to `gate()`, including on its fallback re-extraction.

## Not covered, and why

- Browser actions: already Tier 2 (confirm) for every step, so HIGH cannot raise them.
- Native functions: fixed, mostly read-only operations with explicit tiers.
- `system_inspect`: gated by `command_verifier`'s allowlist and dry run, not the tier gate.

## Live check (local qwen3:8b)

Argument extraction on 8 real tool schemas: 8/8 before and after wrapping (latency unchanged, ~4 s). Labels: LOW for every public read; `fetch http://localhost:8080/admin` → MEDIUM (asked for HIGH). A label is a useful extra signal, not a substitute for deterministic guards; the fetch server's own SSRF guard is what blocks internal addresses.

## Capabilities

### Modified Capabilities
- `tier-gate`: model-declared risk lane.

## Impact

- **Code:** `tier_gate.py`, `orchestrator.py`.
- **Safety:** raise-only by construction; pinned by tests for unknown functions (still 3), force patterns (still 2/3) and Tier-1 functions (never below 1).
