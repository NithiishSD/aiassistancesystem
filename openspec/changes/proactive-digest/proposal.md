# Proposal

Implements ROADMAP D1 (proactivity).

## Why

Zedek only speaks when spoken to. The academic tracker and memory already hold what is needed to
say "you haven't touched graphs in 12 days" without being asked, and nothing ever says it.

## What Changes

- New `digest.py`: builds a daily digest from data Zedek already has, with no model call:
  topics not practised for a week or more, the weakest topics with the reason, the practice
  streak, and stored facts about upcoming commitments (exam, deadline, interview, assignment),
  each shown with how long ago it was noted. With nothing to say it produces nothing.
- New `scheduler.py`: a small job runner. Jobs live in `data/schedule.json` (created with one
  job, the daily digest at 08:00). A job is either the built-in digest or a stored prompt that
  runs through the normal `handle()` pipeline. A job missed while the machine was off runs once
  at the next opportunity. Results go to an inbox file.
- Scheduled runs have nobody to ask, so they run under the deny-all confirmation channel: a
  scheduled prompt can never get a risky action approved.
- Delivery: the REPL runs due jobs at startup and shows unread inbox items; `digest` shows
  today's digest on demand and `inbox` re-reads the inbox. `python scheduler.py --run-due` is
  the entry point for a timer, and sends a desktop notification (title only) when it adds items.
- `deploy/zedek-scheduler.service` and `.timer`: a `systemd --user` timer template. Installing
  it is a documented manual step.

## Non-goals

- No new routed intent ("give me my digest" in natural language): that changes routing and
  needs golden-set rows. `digest` is a REPL command for now.
- No APScheduler, no daemon: a timer calling a one-shot command needs no new dependency and
  survives restarts. (ROADMAP suggested APScheduler + SQLite; this is the stdlib equivalent.)
- No LLM-written digest text. The numbers come from the tracker and are reported as they are.
- No installation of the systemd timer by Zedek itself.
- No cron-style expressions: `daily HH:MM`, optionally limited to named weekdays.

## Capabilities

### New Capabilities
- `proactive-digest`: what Zedek tells the user without being asked, when, and under which
  safety limits scheduled work runs.

### Modified Capabilities

## Impact

- Code: new `digest.py`, `scheduler.py`, `deploy/` unit files; `orchestrator.py` REPL (startup
  inbox, `digest` and `inbox` commands). `.gitignore`: `data/schedule.json`,
  `data/schedule_state.json`, `data/inbox.jsonl` (personal data).
- Tier gate: unchanged and still in the path; scheduled prompts go through `handle()`.
  Confirmations are refused by the deny-all channel, so only Tier 0/1 work can run unattended.
- Watchdog: none.
- Untrusted-input path: none new. A stored prompt is the owner's own text from a local file.
- Privacy: the inbox and schedule stay local and out of git. The desktop notification carries
  no digest content.
