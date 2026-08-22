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

from zedek_logger import get_logger

log = get_logger("tier_gate")

# --- Rule-based tier assignment, per function name ---
# Every currently-available function is Tier 0 (read-only, system_agent).
# This map is intentionally explicit (not a default) so a new function added
# later without an entry here fails safe rather than silently running.
FUNCTION_TIERS = {
    "search_files": 0,
    "disk_usage_by_folder": 0,
    "top_memory_processes": 0,
    "free_space_summary": 0,
}

# --- Hard pattern escalation ---
# Regardless of which function/agent proposes an action, if these patterns
# appear anywhere in the action's arguments, force the tier up. Model
# judgment never gets a vote on these — future agents (coding, web) will
# add more entries here as they're built.
FORCE_TIER_3_PATTERNS = [
    "payment", "credit card", "cvv", "bank account", "ssn", "routing number",
]
FORCE_TIER_2_PATTERNS = [
    "delete", "rm -rf", "force push", "--force", "drop table", "credential",
]


def classify(func_name: str, args: dict) -> int:
    """Returns the tier (0-3) for a proposed action."""
    if func_name not in FUNCTION_TIERS:
        log.info("unknown_function_fail_safe", extra={"function": func_name})
        return 3  # unknown function = treat as highest risk, blocks by default

    base_tier = FUNCTION_TIERS[func_name]

    arg_text = " ".join(str(v).lower() for v in args.values())

    for pattern in FORCE_TIER_3_PATTERNS:
        if pattern in arg_text:
            log.info("tier_forced_3", extra={"function": func_name, "pattern": pattern})
            return 3

    for pattern in FORCE_TIER_2_PATTERNS:
        if pattern in arg_text:
            log.info("tier_forced_2", extra={"function": func_name, "pattern": pattern})
            return max(base_tier, 2)

    return base_tier


def gate(func_name: str, args: dict) -> dict:
    """
    Runs classification and returns a decision object telling the caller
    (orchestrator) how to proceed:

        {"tier": int, "action": "auto" | "notify" | "confirm" | "blocked", "message": str}
    """
    tier = classify(func_name, args)
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
