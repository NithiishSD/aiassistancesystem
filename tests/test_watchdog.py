"""
Unit tests for the watchdog plan-vs-action observer (Roadmap Item 11).

The safety properties under test:
- An unknown plan is never vouched for (fail-safe).
- A risky (Tier 2+) action that was never declared is refused; a read-only
  deviation is allowed but flagged.
- The Tier 3 two-checkpoint flow cannot be short-circuited: pre-submit
  requires pre-fill, and while Tier 3 is disabled it refuses even on approval.
- Sensitive argument values never reach the plaintext audit trail.
"""

import json
import os

import pytest

import watchdog
from watchdog import Watchdog, Verdict, CHECKPOINT_PREFILL, CHECKPOINT_PRESUBMIT


@pytest.fixture
def dog(tmp_path):
    return Watchdog(audit_path=str(tmp_path / "audit.jsonl"))


def _approve(_message):
    return True


class TestPlanRegistration:
    def test_register_returns_id_and_stores_plan(self, dog):
        plan_id = dog.register_plan("read some files", ["search_files"], ["step one"])
        plan = dog.get_plan(plan_id)
        assert plan is not None
        assert plan.goal == "read some files"
        assert "search_files" in plan.allowed_actions

    def test_registration_is_audited(self, dog):
        dog.register_plan("goal", ["a"])
        with open(dog.audit_path) as handle:
            records = [json.loads(line) for line in handle]
        assert any(r["event"] == "plan_registered" for r in records)

    def test_plan_ids_are_unique(self, dog):
        first = dog.register_plan("g", ["a"])
        second = dog.register_plan("g", ["a"])
        assert first != second


class TestObserve:
    def test_declared_action_is_ok(self, dog):
        plan_id = dog.register_plan("g", ["search_files"])
        verdict = dog.observe(plan_id, "search_files", {}, tier=0)
        assert verdict.allowed is True
        assert verdict.status == "ok"

    def test_unknown_plan_is_refused(self, dog):
        verdict = dog.observe("does-not-exist", "search_files", {}, tier=0)
        assert verdict.allowed is False
        assert verdict.status == "unknown_plan"
        assert verdict.severity == "critical"

    def test_undeclared_risky_action_is_refused(self, dog):
        plan_id = dog.register_plan("read only please", ["search_files"])
        verdict = dog.observe(plan_id, "browser_submit", {}, tier=2)
        assert verdict.allowed is False
        assert verdict.status == "deviation"
        assert verdict.severity == "critical"

    def test_undeclared_readonly_action_is_warned_not_blocked(self, dog):
        plan_id = dog.register_plan("g", ["search_files"])
        verdict = dog.observe(plan_id, "free_space_summary", {}, tier=0)
        assert verdict.allowed is True
        assert verdict.status == "deviation"
        assert verdict.severity == "warn"

    def test_tier3_undeclared_action_is_refused(self, dog):
        plan_id = dog.register_plan("g", ["search_files"])
        verdict = dog.observe(plan_id, "make_payment", {}, tier=3)
        assert verdict.allowed is False

    def test_observation_is_audited(self, dog):
        plan_id = dog.register_plan("g", ["a"])
        dog.observe(plan_id, "a", {"x": 1}, tier=0)
        with open(dog.audit_path) as handle:
            records = [json.loads(line) for line in handle]
        assert any(r["event"] == "observation" for r in records)


class TestTwoCheckpointFlow:
    def test_prefill_denied_by_default_callback(self, dog):
        plan_id = dog.register_plan("pay something", ["fill_form"])
        verdict = dog.checkpoint_prefill(plan_id, "fill_form", {"card_number": "4111"})
        assert verdict.allowed is False

    def test_prefill_approved_records_checkpoint(self, dog):
        plan_id = dog.register_plan("pay something", ["fill_form"])
        verdict = dog.checkpoint_prefill(plan_id, "fill_form", {}, ["card number"], _approve)
        assert verdict.allowed is True
        assert CHECKPOINT_PREFILL in dog.get_plan(plan_id).checkpoints_passed

    def test_prefill_on_unknown_plan_refused(self, dog):
        verdict = dog.checkpoint_prefill("nope", "fill_form", {}, [], _approve)
        assert verdict.allowed is False

    def test_presubmit_requires_prefill_first(self, dog):
        plan_id = dog.register_plan("pay something", ["submit_form"])
        verdict = dog.checkpoint_presubmit(plan_id, "submit_form", {}, _approve)
        assert verdict.allowed is False
        assert "pre-fill" in verdict.reason.lower()

    def test_presubmit_blocked_while_tier3_disabled(self, dog):
        """Even with both checkpoints approved, a disabled Tier 3 must not submit."""
        assert watchdog.TIER3_EXECUTION_ENABLED is False
        plan_id = dog.register_plan("pay something", ["submit_form"])
        dog.checkpoint_prefill(plan_id, "fill_form", {}, [], _approve)
        verdict = dog.checkpoint_presubmit(plan_id, "submit_form", {}, _approve)
        assert verdict.allowed is False
        assert "disabled" in verdict.reason.lower()

    def test_presubmit_succeeds_when_tier3_enabled(self, dog, monkeypatch):
        monkeypatch.setattr(watchdog, "TIER3_EXECUTION_ENABLED", True)
        plan_id = dog.register_plan("pay something", ["submit_form"])
        dog.checkpoint_prefill(plan_id, "fill_form", {}, [], _approve)
        verdict = dog.checkpoint_presubmit(plan_id, "submit_form", {}, _approve)
        assert verdict.allowed is True
        assert CHECKPOINT_PRESUBMIT in dog.get_plan(plan_id).checkpoints_passed

    def test_presubmit_denied_when_user_declines(self, dog, monkeypatch):
        monkeypatch.setattr(watchdog, "TIER3_EXECUTION_ENABLED", True)
        plan_id = dog.register_plan("pay something", ["submit_form"])
        dog.checkpoint_prefill(plan_id, "fill_form", {}, [], _approve)
        verdict = dog.checkpoint_presubmit(plan_id, "submit_form", {}, lambda _m: False)
        assert verdict.allowed is False

    def test_prefill_does_not_grant_submit_authority(self, dog, monkeypatch):
        """Approving pre-fill must not itself satisfy the pre-submit checkpoint."""
        monkeypatch.setattr(watchdog, "TIER3_EXECUTION_ENABLED", True)
        plan_id = dog.register_plan("pay", ["submit_form"])
        dog.checkpoint_prefill(plan_id, "fill_form", {}, [], _approve)
        verdict = dog.checkpoint_presubmit(plan_id, "submit_form", {}, lambda _m: False)
        assert verdict.allowed is False


class TestAuditRedaction:
    def test_sensitive_values_are_redacted(self):
        out = watchdog._safe_args({"card_number": "4111111111111111", "url": "https://a.com"})
        assert out["card_number"] == "[REDACTED]"
        assert out["url"] == "https://a.com"

    def test_redaction_is_case_insensitive(self):
        out = watchdog._safe_args({"CVV": "123", "Password": "hunter2"})
        assert out["CVV"] == "[REDACTED]"
        assert out["Password"] == "[REDACTED]"

    def test_non_dict_args_handled(self):
        assert watchdog._safe_args("not a dict") == {}

    def test_secrets_never_written_to_audit_file(self, dog):
        plan_id = dog.register_plan("pay", ["fill_form"])
        # Not "999": the audit line's microsecond timestamp or hex plan id can
        # contain three nines by chance, which failed this test about 1 run in 100.
        dog.observe(plan_id, "fill_form", {"cvv": "cvv-value-K7", "ssn": "111-22-3333"}, tier=3)
        contents = open(dog.audit_path).read()
        assert "cvv-value-K7" not in contents
        assert "111-22-3333" not in contents
        assert "[REDACTED]" in contents


class TestAuditResilience:
    def test_audit_failure_does_not_break_observation(self, tmp_path):
        # Point the audit at a path that cannot be created.
        dog = Watchdog(audit_path="/proc/definitely/not/writable/audit.jsonl")
        plan_id = dog.register_plan("g", ["a"])
        verdict = dog.observe(plan_id, "a", {}, tier=0)
        assert verdict.allowed is True


class TestOrchestratorIntegration:
    def test_declared_write_targets_includes_plan_files(self):
        import orchestrator

        targets = orchestrator._declared_write_targets({
            "files_to_create": ["/home/a/new.py"],
            "files_to_modify": ["/home/a/old.py"],
        })
        assert "write:/home/a/new.py" in targets
        assert "write:/home/a/old.py" in targets

    def test_declared_write_targets_always_allows_default_solution_file(self):
        import orchestrator
        import coding_agent

        targets = orchestrator._declared_write_targets({})
        expected = f"write:{os.path.join(coding_agent.CODING_WRITE_ROOT, 'solution.py')}"
        assert expected in targets

    def test_undeclared_patch_target_is_blocked_by_watchdog(self):
        """A patch aimed at a file the approved plan never named must be refused."""
        import orchestrator

        plan_id = orchestrator.WATCHDOG.register_plan(
            goal="write a helper",
            allowed_actions=orchestrator._declared_write_targets({"files_to_create": ["/home/a/declared.py"]}),
        )
        verdict = orchestrator.WATCHDOG.observe(
            plan_id, "write:/home/a/SNEAKY.py", {"target_file": "/home/a/SNEAKY.py"}, tier=2,
        )
        assert verdict.allowed is False

    def test_declared_patch_target_is_allowed(self):
        import orchestrator

        plan_id = orchestrator.WATCHDOG.register_plan(
            goal="write a helper",
            allowed_actions=orchestrator._declared_write_targets({"files_to_create": ["/home/a/declared.py"]}),
        )
        verdict = orchestrator.WATCHDOG.observe(
            plan_id, "write:/home/a/declared.py", {"target_file": "/home/a/declared.py"}, tier=2,
        )
        assert verdict.allowed is True
