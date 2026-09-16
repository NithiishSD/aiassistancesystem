"""Watchdog: independent plan-vs-action observer (Roadmap Item 11).

The tier gate answers "how risky is this action?". The watchdog answers a
different question the gate cannot: "is this the action the agent SAID it
was going to take?" An agent that declares a read-only plan and then tries
to submit a form has not triggered any single high-tier action — it has
deviated from its stated intent, and that deviation is the signal.

Responsibilities:
  1. Record the plan an agent declared before it began acting.
  2. Check every subsequent action against that plan, flagging deviations.
  3. Enforce the two-checkpoint flow for Tier 3 (pre-fill, then pre-submit).
  4. Write an append-only audit trail of everything it saw.

Fail-safe posture throughout: an unknown plan, an unregistered action at
Tier 2+, or a missing approval all resolve to "not allowed". Approval is
always supplied by an injected callback that defaults to deny — the
watchdog never self-approves, exactly like the web agent.

On the "separate process" design note in the roadmap: this module is
deliberately written as pure state + an append-only JSONL audit trail with
no dependency on the orchestrator, so it CAN later be hosted out-of-process
behind an IPC shim. It currently runs in-process; that is stated plainly
rather than implied otherwise.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from zedek_logger import get_logger

log = get_logger("watchdog")

# Mirrors the tier_gate policy: Tier 3 execution is disabled system-wide
# until the user explicitly turns it on. The two-checkpoint flow below is
# fully built and tested so it is ready when that changes.
TIER3_EXECUTION_ENABLED = False

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AUDIT_PATH = os.path.join(_PROJECT_ROOT, "logs", "watchdog_audit.jsonl")

# Checkpoint names for the Tier 3 flow.
CHECKPOINT_PREFILL = "pre_fill"
CHECKPOINT_PRESUBMIT = "pre_submit"


def deny_all(_message: str) -> bool:
    """Default approval callback: deny. The watchdog never self-approves."""
    return False


@dataclass
class Plan:
    """What an agent declared it intended to do, before it did anything."""

    plan_id: str
    goal: str
    allowed_actions: set[str]
    steps: list[str] = field(default_factory=list)
    created_at: str = ""
    checkpoints_passed: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "allowed_actions": sorted(self.allowed_actions),
            "steps": self.steps,
            "created_at": self.created_at,
            "checkpoints_passed": sorted(self.checkpoints_passed),
        }


@dataclass
class Verdict:
    """The watchdog's ruling on one observed action."""

    allowed: bool
    status: str  # "ok" | "deviation" | "blocked" | "unknown_plan" | "awaiting_checkpoint"
    reason: str = ""
    severity: str = "none"  # "none" | "warn" | "critical"

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "reason": self.reason,
            "severity": self.severity,
        }


class Watchdog:
    """Observes declared plans and the actions taken against them."""

    def __init__(self, audit_path: str = DEFAULT_AUDIT_PATH) -> None:
        self.audit_path = audit_path
        self._plans: dict[str, Plan] = {}
        self._lock = threading.Lock()

    # ── Audit trail ──────────────────────────────────────────────────────

    def _audit(self, event: str, payload: dict[str, Any]) -> None:
        """Append one record to the audit trail.

        Audit failures must never break execution — the watchdog is an
        observer, so a full disk degrades observability, not the assistant.
        """
        record = {
            "timestamp": datetime.now().isoformat(),
            "event": event,
            **payload,
        }
        try:
            os.makedirs(os.path.dirname(self.audit_path), exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError as err:
            log.info("watchdog_audit_write_failed", extra={"error": str(err)})

    # ── Plan registration ────────────────────────────────────────────────

    def register_plan(
        self,
        goal: str,
        allowed_actions: list[str] | set[str],
        steps: list[str] | None = None,
    ) -> str:
        """Record a declared plan and return its id."""
        plan_id = uuid.uuid4().hex[:12]
        plan = Plan(
            plan_id=plan_id,
            goal=(goal or "").strip(),
            allowed_actions=set(allowed_actions or []),
            steps=list(steps or []),
            created_at=datetime.now().isoformat(),
        )
        with self._lock:
            self._plans[plan_id] = plan

        log.info("watchdog_plan_registered", extra={
            "plan_id": plan_id, "goal": plan.goal[:200], "action_count": len(plan.allowed_actions),
        })
        self._audit("plan_registered", plan.to_dict())
        return plan_id

    def get_plan(self, plan_id: str) -> Plan | None:
        with self._lock:
            return self._plans.get(plan_id)

    # ── Observation ──────────────────────────────────────────────────────

    def observe(self, plan_id: str, action: str, args: dict[str, Any], tier: int) -> Verdict:
        """Check one proposed action against its declared plan.

        A deviation at Tier 0/1 is a warning — the action is read-only or
        reversible, so flag it and let it through. A deviation at Tier 2+ is
        refused: the agent is trying to do something risky it never declared.
        """
        plan = self.get_plan(plan_id)

        if plan is None:
            verdict = Verdict(
                allowed=False, status="unknown_plan", severity="critical",
                reason=f"No registered plan '{plan_id}' — refusing to vouch for this action.",
            )
        elif action in plan.allowed_actions:
            verdict = Verdict(allowed=True, status="ok", reason="Action matches the declared plan.")
        elif tier >= 2:
            verdict = Verdict(
                allowed=False, status="deviation", severity="critical",
                reason=(f"'{action}' is not in the declared plan and is Tier {tier} "
                        f"(risky). Declared actions: {sorted(plan.allowed_actions)}."),
            )
        else:
            verdict = Verdict(
                allowed=True, status="deviation", severity="warn",
                reason=(f"'{action}' was not in the declared plan, but is Tier {tier} "
                        f"(read-only/reversible) — allowed and logged."),
            )

        log.info("watchdog_observation", extra={
            "plan_id": plan_id, "action": action, "tier": tier,
            "status": verdict.status, "allowed": verdict.allowed,
        })
        self._audit("observation", {
            "plan_id": plan_id, "action": action, "args": _safe_args(args),
            "tier": tier, "verdict": verdict.to_dict(),
        })
        return verdict

    # ── Tier 3 two-checkpoint flow ───────────────────────────────────────

    def checkpoint_prefill(
        self,
        plan_id: str,
        action: str,
        args: dict[str, Any],
        sensitive_fields: list[str] | None = None,
        approve_fn: Callable[[str], bool] | None = None,
    ) -> Verdict:
        """First Tier 3 checkpoint: approve BEFORE sensitive data is entered.

        Passing this checkpoint does not authorize submission — it only
        authorizes filling in the data. The pre-submit checkpoint is separate
        and deliberately cannot be satisfied by this one.
        """
        approve = approve_fn or deny_all
        plan = self.get_plan(plan_id)
        if plan is None:
            return self._refuse(plan_id, CHECKPOINT_PREFILL, action,
                                f"No registered plan '{plan_id}'.")

        fields = ", ".join(sensitive_fields or []) or "sensitive data"
        message = (
            f"[Watchdog checkpoint 1/2 — PRE-FILL]\n"
            f"Goal: {plan.goal}\n"
            f"About to enter {fields} via '{action}'.\n"
            f"This does NOT submit anything yet. Approve entering this data? (y/n)"
        )

        if not approve(message):
            return self._refuse(plan_id, CHECKPOINT_PREFILL, action,
                                "You declined at the pre-fill checkpoint.")

        with self._lock:
            plan.checkpoints_passed.add(CHECKPOINT_PREFILL)

        verdict = Verdict(allowed=True, status="ok", reason="Pre-fill checkpoint approved.")
        log.info("watchdog_checkpoint_passed", extra={"plan_id": plan_id, "checkpoint": CHECKPOINT_PREFILL})
        self._audit("checkpoint", {
            "plan_id": plan_id, "checkpoint": CHECKPOINT_PREFILL, "action": action,
            "args": _safe_args(args), "verdict": verdict.to_dict(),
        })
        return verdict

    def checkpoint_presubmit(
        self,
        plan_id: str,
        action: str,
        args: dict[str, Any],
        approve_fn: Callable[[str], bool] | None = None,
    ) -> Verdict:
        """Second Tier 3 checkpoint: approve the irreversible submit itself.

        Requires that the pre-fill checkpoint already passed for this plan.
        While TIER3_EXECUTION_ENABLED is False this always refuses, even on
        approval — matching the tier gate's system-wide Tier 3 block.
        """
        approve = approve_fn or deny_all
        plan = self.get_plan(plan_id)
        if plan is None:
            return self._refuse(plan_id, CHECKPOINT_PRESUBMIT, action,
                                f"No registered plan '{plan_id}'.")

        if CHECKPOINT_PREFILL not in plan.checkpoints_passed:
            return self._refuse(
                plan_id, CHECKPOINT_PRESUBMIT, action,
                "Pre-submit reached without passing the pre-fill checkpoint — "
                "the two-checkpoint flow was not followed.",
            )

        if not TIER3_EXECUTION_ENABLED:
            return self._refuse(
                plan_id, CHECKPOINT_PRESUBMIT, action,
                "Tier 3 execution is disabled system-wide. The action was fully "
                "prepared and checked, but will not be submitted.",
            )

        message = (
            f"[Watchdog checkpoint 2/2 — PRE-SUBMIT]\n"
            f"Goal: {plan.goal}\n"
            f"About to SUBMIT via '{action}'. This step is irreversible.\n"
            f"Approve submission? (y/n)"
        )
        if not approve(message):
            return self._refuse(plan_id, CHECKPOINT_PRESUBMIT, action,
                                "You declined at the pre-submit checkpoint.")

        with self._lock:
            plan.checkpoints_passed.add(CHECKPOINT_PRESUBMIT)

        verdict = Verdict(allowed=True, status="ok", reason="Pre-submit checkpoint approved.")
        log.info("watchdog_checkpoint_passed", extra={"plan_id": plan_id, "checkpoint": CHECKPOINT_PRESUBMIT})
        self._audit("checkpoint", {
            "plan_id": plan_id, "checkpoint": CHECKPOINT_PRESUBMIT, "action": action,
            "args": _safe_args(args), "verdict": verdict.to_dict(),
        })
        return verdict

    def _refuse(self, plan_id: str, checkpoint: str, action: str, reason: str) -> Verdict:
        verdict = Verdict(allowed=False, status="blocked", reason=reason, severity="critical")
        log.info("watchdog_checkpoint_refused", extra={
            "plan_id": plan_id, "checkpoint": checkpoint, "reason": reason,
        })
        self._audit("checkpoint", {
            "plan_id": plan_id, "checkpoint": checkpoint, "action": action,
            "verdict": verdict.to_dict(),
        })
        return verdict


# Values that must never be written to the audit trail in the clear.
_SENSITIVE_ARG_KEYS = {
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "cvv", "card_number", "cardnumber", "ssn", "pin", "account_number",
}


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values before they reach the audit file.

    The audit trail is a plaintext file on disk; the whole point of the Tier 3
    flow is that it handles payment and identity data, so that data must not
    be persisted here.
    """
    if not isinstance(args, dict):
        return {}
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        if str(key).strip().lower() in _SENSITIVE_ARG_KEYS:
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = value
    return redacted
