"""
Scheduled jobs and the inbox (ROADMAP D1).

No daemon: `python scheduler.py --run-due` is a one-shot command for a
systemd user timer (deploy/), and the REPL calls run_due() at startup, so a
job missed while the machine was off runs once at the next opportunity.

A job is {"name", "schedule", "kind"}: kind "digest" builds the daily digest;
kind "prompt" sends its "prompt" through the normal request pipeline. Nobody
is there to approve anything, so prompts run under confirmation.DenyChannel:
the tier gate still decides, and every request for approval is refused.

Schedules: "daily HH:MM", optionally "daily HH:MM mon,wed,fri".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Callable

import confirmation
from zedek_logger import get_logger

log = get_logger("scheduler")

_ROOT = os.path.dirname(os.path.abspath(__file__))
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_SCHEDULE_RE = re.compile(r"^daily\s+(\d{1,2}):(\d{2})(?:\s+([a-z,\s]+))?$")
DEFAULT_JOBS = [{"name": "daily-digest", "schedule": "daily 08:00", "kind": "digest"}]
MAX_RESULT_CHARS = 4000


def _path(env: str, filename: str) -> str:
    return os.environ.get(env) or os.path.join(_ROOT, "data", filename)


def schedule_path() -> str:
    return _path("ZEDEK_SCHEDULE_PATH", "schedule.json")


def state_path() -> str:
    return _path("ZEDEK_SCHEDULE_STATE_PATH", "schedule_state.json")


def inbox_path() -> str:
    return _path("ZEDEK_INBOX_PATH", "inbox.jsonl")


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as error:
        log.info("scheduler_file_unreadable", extra={"path": os.path.basename(path), "error": str(error)[:200]})
        return default


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(tmp, path)


def load_jobs() -> list[dict]:
    """The owner's jobs, or the default (daily digest) when none are configured."""
    jobs = _read_json(schedule_path(), None)
    if not isinstance(jobs, list):
        return [dict(job) for job in DEFAULT_JOBS]
    return [job for job in jobs if isinstance(job, dict)]


def parse_schedule(schedule: str) -> tuple[int, int, set[int]] | None:
    """(hour, minute, weekdays 0=Mon) or None if the schedule is not understood."""
    match = _SCHEDULE_RE.match(" ".join(str(schedule or "").lower().split()))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    days = set(range(7))
    if match.group(3):
        names = [name for name in re.split(r"[,\s]+", match.group(3)) if name]
        if not names or any(name not in _WEEKDAYS for name in names):
            return None
        days = {_WEEKDAYS.index(name) for name in names}
    return hour, minute, days


def due_jobs(now: datetime, jobs: list[dict] | None = None, state: dict | None = None) -> list[dict]:
    """Jobs whose time has come today and that have not run today."""
    jobs = load_jobs() if jobs is None else jobs
    state = _read_json(state_path(), {}) if state is None else state
    today = now.date().isoformat()
    due: list[dict] = []
    for job in jobs:
        name = job.get("name")
        parsed = parse_schedule(job.get("schedule", ""))
        if not name or parsed is None or job.get("kind") not in ("digest", "prompt"):
            log.info("scheduler_job_skipped", extra={"job": str(name)[:80], "reason": "invalid job or schedule"})
            continue
        hour, minute, days = parsed
        if now.weekday() not in days or (now.hour, now.minute) < (hour, minute):
            continue
        if (state.get(name) or {}).get("last_run") == today:
            continue
        due.append(job)
    return due


def _run_job(job: dict, now: datetime, prompt_runner: Callable[[str], str] | None) -> str:
    if job["kind"] == "digest":
        import digest
        return digest.build_digest(now=now)
    prompt = str(job.get("prompt") or "").strip()
    if not prompt:
        return "This scheduled job has no prompt."
    if prompt_runner is None:
        import orchestrator
        prompt_runner = orchestrator.handle
    with confirmation.use_channel(confirmation.DenyChannel()):
        return str(prompt_runner(prompt) or "")


def run_due(now: datetime | None = None, prompt_runner: Callable[[str], str] | None = None) -> list[dict]:
    """Run every due job once. Returns the inbox entries that were added."""
    now = now or datetime.now()
    state = _read_json(state_path(), {})
    if not isinstance(state, dict):
        state = {}
    added: list[dict] = []
    for job in due_jobs(now, state=state):
        try:
            text = _run_job(job, now, prompt_runner)
        except Exception as error:
            log.info("scheduler_job_failed", extra={"job": job["name"], "error": str(error)[:200]})
            text = f"Scheduled job '{job['name']}' failed: {error}"
        # Marked as run even on failure or an empty result: once a day, not a retry loop.
        state[job["name"]] = {"last_run": now.date().isoformat()}
        _write_json(state_path(), state)
        if text.strip():
            entry = {"time": now.isoformat(timespec="seconds"), "job": job["name"],
                     "text": text.strip()[:MAX_RESULT_CHARS], "read": False}
            _append_inbox(entry)
            added.append(entry)
        log.info("scheduler_job_ran", extra={"job": job["name"], "delivered": bool(text.strip())})
    return added


def _append_inbox(entry: dict) -> None:
    path = inbox_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_inbox() -> list[dict]:
    entries: list[dict] = []
    try:
        with open(inbox_path(), encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and "text" in entry:
                    entries.append(entry)
    except FileNotFoundError:
        pass
    return entries


def take_unread() -> list[dict]:
    """Unread inbox entries, marking them read."""
    entries = read_inbox()
    unread = [entry for entry in entries if not entry.get("read")]
    if unread:
        for entry in unread:
            entry["read"] = True
        tmp = inbox_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(tmp, inbox_path())
    return unread


def format_entries(entries: list[dict]) -> str:
    return "\n\n".join(f"[{entry.get('time', '?')}] {entry.get('job', 'job')}\n{entry['text']}" for entry in entries)


def notification_command(count: int) -> list[str] | None:
    """The desktop notification for new inbox items. Title only: the result
    text never leaves the inbox file."""
    binary = shutil.which("notify-send")
    if not binary or count < 1:
        return None
    return [binary, "Zedek", f"{count} new item{'s' if count != 1 else ''} in your inbox. Open Zedek to read."]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Zedek scheduled jobs")
    parser.add_argument("--run-due", action="store_true", help="run every job that is due, then exit")
    parser.add_argument("--list", action="store_true", help="show the configured jobs")
    args = parser.parse_args()

    if args.run_due:
        new_entries = run_due()
        command = notification_command(len(new_entries))
        if command:
            subprocess.run(command, check=False, timeout=10)
        print(f"{len(new_entries)} job result(s) added to the inbox.")
    elif args.list:
        for configured in load_jobs():
            print(f"{configured.get('name')}: {configured.get('schedule')} ({configured.get('kind')})")
    else:
        parser.print_help()
