"""Scheduled jobs and the inbox (OpenSpec change: proactive-digest). Temp files only."""

import json
from datetime import datetime

import pytest

import confirmation
import scheduler

DIGEST = {"name": "daily-digest", "schedule": "daily 08:00", "kind": "digest"}
WEDNESDAY = datetime(2026, 9, 30, 9, 30)


@pytest.fixture(autouse=True)
def paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ZEDEK_SCHEDULE_PATH", str(tmp_path / "schedule.json"))
    monkeypatch.setenv("ZEDEK_SCHEDULE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("ZEDEK_INBOX_PATH", str(tmp_path / "inbox.jsonl"))
    return tmp_path


def _jobs(paths, jobs):
    (paths / "schedule.json").write_text(json.dumps(jobs))


class TestSchedule:
    @pytest.mark.parametrize("text,expected", [
        ("daily 08:00", (8, 0, set(range(7)))),
        ("Daily  7:05", (7, 5, set(range(7)))),
        ("daily 18:30 mon,wed, fri", (18, 30, {0, 2, 4})),
    ])
    def test_parse(self, text, expected):
        assert scheduler.parse_schedule(text) == expected

    @pytest.mark.parametrize("text", ["", "hourly", "daily 25:00", "daily 08:61", "daily 08:00 someday", "* * * * *"])
    def test_invalid(self, text):
        assert scheduler.parse_schedule(text) is None

    def test_default_is_the_daily_digest(self):
        assert scheduler.load_jobs() == [DIGEST]

    def test_due_after_its_time(self):
        assert scheduler.due_jobs(WEDNESDAY, [DIGEST], {}) == [DIGEST]

    def test_not_before_its_time(self):
        assert scheduler.due_jobs(WEDNESDAY.replace(hour=7), [DIGEST], {}) == []

    def test_not_twice_a_day(self):
        assert scheduler.due_jobs(WEDNESDAY, [DIGEST], {"daily-digest": {"last_run": "2026-09-30"}}) == []

    def test_missed_days_run_once(self, monkeypatch):
        monkeypatch.setattr("digest.build_digest", lambda now=None: "digest text")
        scheduler._write_json(scheduler.state_path(), {"daily-digest": {"last_run": "2026-09-26"}})
        assert len(scheduler.run_due(WEDNESDAY)) == 1
        assert scheduler.run_due(WEDNESDAY.replace(hour=10)) == []

    def test_weekday_limit(self):
        job = {**DIGEST, "schedule": "daily 08:00 mon,tue"}
        assert scheduler.due_jobs(WEDNESDAY, [job], {}) == []
        assert scheduler.due_jobs(datetime(2026, 9, 28, 9, 0), [job], {}) == [job]

    def test_invalid_job_skipped_others_run(self):
        jobs = [{"name": "bad", "schedule": "whenever", "kind": "digest"},
                {"name": "odd", "schedule": "daily 08:00", "kind": "shell"}, DIGEST]
        assert scheduler.due_jobs(WEDNESDAY, jobs, {}) == [DIGEST]


class TestRunDue:
    def test_prompt_job_runs_under_the_deny_channel(self, paths):
        _jobs(paths, [{"name": "check", "schedule": "daily 08:00", "kind": "prompt", "prompt": "delete my files"}])
        seen = []

        def runner(prompt):
            seen.append(prompt)
            answer = confirmation.ask("Really delete?")
            return "done" if answer else "Cancelled."

        before = confirmation.get_channel()
        added = scheduler.run_due(WEDNESDAY, prompt_runner=runner)
        assert seen == ["delete my files"] and added[0]["text"] == "Cancelled."
        assert confirmation.get_channel() is before

    def test_empty_digest_adds_nothing_but_counts_as_run(self, monkeypatch):
        monkeypatch.setattr("digest.build_digest", lambda now=None: "")
        assert scheduler.run_due(WEDNESDAY) == [] and scheduler.read_inbox() == []
        assert scheduler.due_jobs(WEDNESDAY) == []

    def test_failing_job_is_recorded_and_others_run(self, paths, monkeypatch):
        _jobs(paths, [{"name": "boom", "schedule": "daily 08:00", "kind": "prompt", "prompt": "x"}, DIGEST])
        monkeypatch.setattr("digest.build_digest", lambda now=None: "digest text")

        def runner(prompt):
            raise RuntimeError("provider down")

        added = scheduler.run_due(WEDNESDAY, prompt_runner=runner)
        assert [entry["job"] for entry in added] == ["boom", "daily-digest"]
        assert "failed: provider down" in added[0]["text"]
        assert scheduler.due_jobs(WEDNESDAY) == []

    def test_prompt_job_without_prompt(self, paths):
        _jobs(paths, [{"name": "empty", "schedule": "daily 08:00", "kind": "prompt"}])
        assert "no prompt" in scheduler.run_due(WEDNESDAY, prompt_runner=lambda p: "never")[0]["text"]

    def test_unreadable_schedule_falls_back_to_default(self, paths):
        (paths / "schedule.json").write_text("{not json")
        assert scheduler.load_jobs() == [DIGEST]


class TestInbox:
    def test_unread_then_read(self, monkeypatch):
        monkeypatch.setattr("digest.build_digest", lambda now=None: "digest text")
        scheduler.run_due(WEDNESDAY)
        unread = scheduler.take_unread()
        assert [entry["text"] for entry in unread] == ["digest text"]
        assert scheduler.take_unread() == []
        assert len(scheduler.read_inbox()) == 1 and scheduler.read_inbox()[0]["read"] is True
        assert "daily-digest\ndigest text" in scheduler.format_entries(unread)

    def test_corrupt_line_ignored(self, paths):
        (paths / "inbox.jsonl").write_text('{"text": "ok", "read": false}\nnot json\n[1]\n')
        assert [entry["text"] for entry in scheduler.read_inbox()] == ["ok"]

    def test_notification_has_no_result_text(self, monkeypatch):
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: "/usr/bin/notify-send")
        command = scheduler.notification_command(2)
        assert command[:2] == ["/usr/bin/notify-send", "Zedek"] and "2 new items" in command[2]
        assert scheduler.notification_command(0) is None
        monkeypatch.setattr(scheduler.shutil, "which", lambda name: None)
        assert scheduler.notification_command(2) is None


class TestReplDelivery:
    def test_unread_shown_once(self, monkeypatch):
        import orchestrator

        monkeypatch.setattr("digest.build_digest", lambda now=None: "digest text")
        scheduler.run_due(WEDNESDAY)
        first = orchestrator._unread_notices(run_jobs=False)
        assert "While you were away" in first and "digest text" in first
        assert orchestrator._unread_notices(run_jobs=False) == ""

    def test_broken_scheduler_does_not_stop_startup(self, monkeypatch):
        import orchestrator

        monkeypatch.setattr(scheduler, "run_due", lambda: (_ for _ in ()).throw(OSError("disk")))
        assert orchestrator._unread_notices() == ""

    def test_repl_commands(self, monkeypatch):
        import orchestrator

        monkeypatch.setattr("digest.build_digest", lambda *a, **k: "")
        assert orchestrator._repl_command("digest") == "Nothing to report today."
        assert orchestrator._repl_command("inbox") == "Your inbox is empty."
        assert "'digest'" in orchestrator._repl_command("help")
        assert orchestrator._repl_command("what is a heap") is None
