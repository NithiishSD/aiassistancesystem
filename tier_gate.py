"""
Phase 4: Tier & confirmation gate.

Every function call chosen by the orchestrator passes through here BEFORE
execution. Classification is rule-based first (hardcoded, cannot be
overridden by model judgment); only unmatched/ambiguous cases would fall
back to model judgment (not needed yet — no agent currently produces
ambiguous actions).

Tiers:
  0 - Read-only. Auto-executes silently.
  1 - Reversible. Auto-executes, but shows a quick heads-up first.
  2 - Risky. Requires the user to see the plan and explicitly confirm.
  3 - High-risk. BLOCKED at execution. Detection stays active; dispatch does not proceed.
      (Per current design: Tier 3 execution is disabled system-wide until explicitly
      re-enabled per-task by the user.)
"""

from __future__ import annotations

import re

import capabilities

from zedek_logger import get_logger

log = get_logger("tier_gate")

# --- Rule-based tier assignment, per function name ---
# From `tier:` in capabilities/*.yaml (ROADMAP E1); pinned by
# tests/test_capabilities.py so no tier changes without a reviewed test edit.
# Explicit, not a default: a function with no tier fails safe (Tier 3) rather
# than silently running. A malformed capability file stops Zedek at import.
FUNCTION_TIERS = capabilities.function_tiers()

# --- Hard pattern escalation (args + user input) ---
# Regardless of which function/agent proposes an action, if these patterns
# appear anywhere in the action's arguments or raw user input, force the tier
# up. Model judgment never gets a vote on these.
FORCE_TIER_3_PATTERNS = [
    "payment", "credit card", "cvv", "bank account", "ssn", "routing number",
]
FORCE_TIER_2_PATTERNS = [
    "delete", "rm -rf", "force push", "--force", "drop table", "credential",
]

# --- MCP effect-verb patterns (tool description scoped) ---
# These verbs indicate a tool mutates world state. They are matched only
# against the *effect-description portion* of a tool's registered description
# (everything before the Args/Parameters block), NOT against runtime args or
# user input — that distinction is the collision-avoidance boundary.
#
# Multi-word phrases ("write to", "type into") are boundary-safe by virtue of
# containing a space. Single-word entries use \b word-boundary anchors to
# prevent substring false positives:
#   "patch"  would match inside "dispatch"
#   "press"  would match inside "compress", "progress", "express"
#   "commit" would match inside "committee"
#   "push"   would match inside "pushes" (still action-adjacent, kept)
MCP_ACTION_VERB_PATTERNS: list[str] = [
    "click", "submit", "type into", "press", "send", "purchase",
    "confirm", "write to", "commit", "push", "patch",
]

# Precompile for efficiency. re.IGNORECASE ensures capitalised forms match.
# The \b...\b word-boundary suffix prevents substring false positives
# (e.g. "patch" inside "dispatch"), and the optional inflection suffix
# (?:s|ed|ing|es)? allows 3rd-person ("clicks"), past ("clicked"),
# and progressive ("clicking") forms to match alongside the bare infinitive.
# Multi-word phrases ("write to", "type into") don't need the suffix — their
# trailing word naturally provides the boundary.
_MCP_ACTION_VERB_RES: list[re.Pattern[str]] = [
    re.compile(r"\b" + re.escape(v) + r"(?:s|es|ed|ing)?\b", re.IGNORECASE)
    for v in MCP_ACTION_VERB_PATTERNS
]

# Regex to strip Args/Parameters docstring sections before verb matching.
_PARAM_SECTION_RE = re.compile(
    r"(Args?|Arguments?|Parameters?)\s*:\s*.*",
    re.IGNORECASE | re.DOTALL,
)


def _extract_effect_description(tool_description: str) -> str:
    """Return only the effect-describing portion of a tool description.

    Strips everything from the first Args/Parameters section onward, leaving
    only the top-level summary sentence(s) that describe what the tool does
    to the world. This prevents verb matches on parameter *names* or
    *descriptions* (e.g. a param called "text to write a summary of").
    """
    return _PARAM_SECTION_RE.sub("", tool_description).strip().lower()


def _server_default_tier(func_name: str) -> int:
    """Return the server-level default_tier for the server that owns func_name.

    Derives the server name from the qualified_name prefix (mcp_{server}_...).
    Returns 1 (MCP baseline) if the server is not found or has no default_tier.
    Imports mcp_client lazily to avoid circular imports at module load time.
    """
    if not func_name.startswith("mcp_"):
        return 1  # non-MCP function; server default does not apply

    try:
        import mcp_client
        registry = mcp_client.get_server_registry()
    except Exception:
        return 1  # import failure or no discovery yet — fail safe

    # Qualified name format: mcp_{server_name}_{tool_name}
    # server_name itself may contain underscores, so we match greedily against
    # known server names rather than splitting on the first underscore.
    for server_name, cfg in registry.items():
        prefix = f"mcp_{server_name}_"
        if func_name.startswith(prefix):
            return cfg.default_tier

    return 1  # unknown server — return MCP default, pattern matching still applies


# Lane 4: the model's own risk label (ROADMAP B6, OpenHands-style). The model
# that chose the action states LOW/MEDIUM/HIGH in the same structured reply, at
# no extra inference cost. It can only RAISE a tier: max(rule tier, label).
# HIGH means "ask first", never "block": blocking stays a rule decision.
LLM_RISK_TIERS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def classify(func_name: str, args: dict, user_input: str = "",
             tool_description: str = "", llm_risk: str | None = None) -> int:
    """Returns the tier (0-3) for a proposed action.

    Checks in order:
      1. FUNCTION_TIERS explicit map (non-MCP tools).
      2. MCP default (Tier 1) for any mcp_* function.
      3. FORCE_TIER_3_PATTERNS in resolved args + raw user input.
      4. FORCE_TIER_2_PATTERNS in resolved args + raw user input.
      5. MCP_ACTION_VERB_PATTERNS in tool's effect description (word-boundary matched).
      6. Server-level default_tier (monotonic ceiling-raiser).

    Precedence: effective_tier = max of all applicable lanes — each lane can
    only raise the tier, never lower it.
    """
    if func_name in FUNCTION_TIERS:
        base_tier = FUNCTION_TIERS[func_name]
    elif func_name.startswith("mcp_") or func_name == "mcp_tool":
        # MCP tools: default Tier 1 (notify) — visible/transparent execution.
        # Force-patterns (Tier 2/3) and effect-verb patterns still apply on top.
        base_tier = 1

        # --- Lane 2: effect-verb inspection (description-scoped, collision-safe) ---
        if tool_description:
            effect_text = _extract_effect_description(tool_description)
            for compiled_re, verb in zip(_MCP_ACTION_VERB_RES, MCP_ACTION_VERB_PATTERNS):
                if compiled_re.search(effect_text):
                    log.info("tier_forced_2_effect_verb",
                             extra={"function": func_name, "verb": verb})
                    base_tier = max(base_tier, 2)
                    break  # one match is enough

        # --- Lane 3: server-level default_tier (monotonic ceiling-raiser) ---
        # Only applies to MCP tools — non-MCP functions use FUNCTION_TIERS exclusively.
        server_default = _server_default_tier(func_name)
        base_tier = max(base_tier, server_default)
    else:
        log.info("unknown_function_fail_safe", extra={"function": func_name})
        return 3  # unknown function = treat as highest risk, blocks by default

    # --- Lane 1: args + user-input hard patterns ---
    # These apply to ALL function types (MCP and non-MCP alike).
    arg_text = " ".join(str(v).lower() for v in args.values())
    combined_text = f"{arg_text} {user_input.lower()}"

    for pattern in FORCE_TIER_3_PATTERNS:
        if pattern in combined_text:
            log.info("tier_forced_3", extra={"function": func_name, "pattern": pattern})
            return 3

    for pattern in FORCE_TIER_2_PATTERNS:
        if pattern in combined_text:
            log.info("tier_forced_2", extra={"function": func_name, "pattern": pattern})
            base_tier = max(base_tier, 2)

    risk_tier = LLM_RISK_TIERS.get(llm_risk, 0) if isinstance(llm_risk, str) else 0
    if risk_tier > base_tier:
        log.info("tier_raised_by_llm_risk", extra={"function": func_name, "risk": llm_risk,
                                                    "from": base_tier, "to": risk_tier})
    return max(base_tier, risk_tier)


def gate(func_name: str, args: dict, user_input: str = "",
         tool_description: str = "", llm_risk: str | None = None) -> dict:
    """
    Runs classification and returns a decision object telling the caller
    (orchestrator) how to proceed:

        {"tier": int, "action": "auto" | "notify" | "confirm" | "blocked", "message": str}

    The optional `tool_description` parameter accepts the MCPToolSpec.description
    string, enabling effect-verb matching against the tool's declared behaviour.
    Callers that do not supply it (e.g. legacy call sites) continue to work —
    only args/user-input patterns are checked in that case.

    `llm_risk` ("LOW" | "MEDIUM" | "HIGH") is the choosing model's own label; it
    can raise the tier (HIGH -> confirm) but never lower it.
    """
    tier = classify(func_name, args, user_input, tool_description, llm_risk)
    log.info("gate_decision", extra={"function": func_name, "tier": tier})

    if tier == 0:
        return {"tier": 0, "action": "auto", "message": None}

    if tier == 1:
        msg = f"Heads up: running '{func_name}' with {args} (reversible action)."
        return {"tier": 1, "action": "notify", "message": msg}

    if tier == 2:
        msg = f"This action is Tier 2 (risky): '{func_name}' with {args}. Confirm to proceed? (y/n)"
        return {"tier": 2, "action": "confirm", "message": msg}

    # tier == 3
    msg = (f"This is a high-risk task (Tier 3): '{func_name}' with {args}. "
           f"Execution is currently disabled — you'll need to do this yourself, "
           f"or explicitly enable Tier 3 for this task.")
    return {"tier": 3, "action": "blocked", "message": msg}