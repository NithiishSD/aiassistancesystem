# Tasks

## 1. Digest content

- [x] 1.1 Create `digest.py` (`build_digest(tracker, now, facts_fn)` returning text or ""); verify with new `tests/test_digest.py`: stale and weak topics with figures from the tracker, no practice data, commitment with age, superseded commitment excluded, empty digest

## 2. Scheduler and inbox

- [x] 2.1 Create `scheduler.py`: schedule parsing (`daily HH:MM`, optional weekdays), `due_jobs`, state file, default schedule, paths from `ZEDEK_SCHEDULE_PATH` / `ZEDEK_SCHEDULE_STATE_PATH` / `ZEDEK_INBOX_PATH`; verify with new `tests/test_scheduler.py` for every scheduling scenario in the spec (temp files only)
- [x] 2.2 `run_due`: digest jobs and prompt jobs (prompt runner injected, run under `confirmation.DenyChannel`), inbox append, failing job recorded; verify tests for deny channel active during a prompt job, empty digest adds nothing, failing job does not stop others
- [x] 2.3 Inbox read/mark-read and `--run-due` CLI with a title-only desktop notification; verify tests for unread/mark-read and that the notification command carries no result text

## 3. Delivery

- [x] 3.1 REPL: run due jobs and show unread items at startup; `digest` and `inbox` commands; verify by test of the helper that formats unread items and marks them read
- [x] 3.2 Add `deploy/zedek-scheduler.service` and `.timer` and gitignore the three data files; verify `systemd-analyze verify` accepts the units (or record that it is unavailable) and `git check-ignore` matches the data files

## 4. Integrate

- [x] 4.1 Live check with temp paths: a scheduled prompt asking for a Tier 2 action is refused and recorded; the digest job runs against the real tracker and memory
- [x] 4.2 Update `ROADMAP.md` D1; run `./zedek-env/bin/python -m pytest -q` with 0 failures and report the count before and after
