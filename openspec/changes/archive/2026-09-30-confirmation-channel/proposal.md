# Proposal

Implements ROADMAP D5 (abstract the confirmation channel) and the open B6 item
"reject-with-feedback".

## Why

Six places in `orchestrator.py` call `input()` directly to confirm a risky action. That blocks,
cannot work from a UI, a voice turn or a scheduled job, and throws away anything the user says
other than "y". A refusal ends the task with no explanation reaching the agent, so the user has
to start again to say "not that one".

## What Changes

- New `confirmation.py`: one way to ask for approval and to show a notice. The terminal is the
  default channel. A deny-all channel exists for non-interactive runs. The active channel can be
  swapped for the duration of a task.
- An answer carries `approved` and an optional `reason`. Only "y" or "yes" approves, exactly as
  strict as today plus the word "yes". Anything else is a refusal; text other than "n"/"no" is
  kept as the user's reason.
- Every confirmation in the orchestrator (Tier 2 system action, MCP tool, coding-task gate,
  coding plan, apply patch, browser action, routing correction) goes through the channel.
  The coding plan is sent as one message instead of a series of prints.
- Reject-with-feedback:
  - Browser: a refusal **with a reason** is shown to the planner as a refused step with the
    user's words, and browsing continues within the step limit. A bare refusal still ends the
    run. The planner proposing the same refused action again ends the run.
  - Coding plan: a refusal with a reason produces a revised plan from the request plus the
    reason, shown for approval again, at most twice. A bare refusal ends as today.
  - Other refusals log the reason and cancel.

## Non-goals

- No UI or voice channel yet (D4, F9). This change only makes them possible.
- No change to what needs confirmation: the tier gate decides that, unchanged.
- No auto-approval, remembered approvals, or "always allow".
- Tier 3 stays disabled.

## Capabilities

### New Capabilities
- `confirmation`: how approval for a risky action is requested, what counts as approval, and
  what happens to the reason given with a refusal.

### Modified Capabilities
- `web-agent`: a refusal with a reason no longer ends the browsing run.

## Impact

- Code: new `confirmation.py`; `orchestrator.py` (seven call sites); `web_agent.py` (loop and
  planner history). Tests: new `tests/test_confirmation.py`, additions to web agent tests.
- Tier gate: unchanged; every action still passes `gate()` first, and each new proposal after a
  refusal passes the gate and needs its own confirmation.
- Watchdog: none.
- Untrusted-input path: the refusal reason is the user's own typed text and is the only new text
  the browser planner sees. Page content still never reaches it.
- Safety default: with no channel able to ask, the answer is a refusal.
