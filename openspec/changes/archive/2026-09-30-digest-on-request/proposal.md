# Proposal

Implements the open ROADMAP D1 follow-up (a routed intent for the digest).

## Why

The daily digest could be read only from the scheduled job's inbox or by typing the exact REPL
command `digest`. "What should I focus on today" went to general question answering, where a
model that cannot see the practice tracker made something up or said it did not know.

## What Changes

- New capability `daily_digest` (`capabilities/daily_digest.yaml`, handler `_run_daily_digest`):
  the reply is `digest.build_digest()`, the text the scheduled job delivers, or "Nothing to
  report today." No model call.
- 22 golden rows for the new class (11 DEV, 11 held-out TEST), written before any phrase tuning.
- Two news phrases added to `mcp_tool` and three news rows added to the DEV slice, because the new
  route took news requests away from the news tool (see below).
- Baseline regenerated: it gains the new class; no other class's figures change.

## Measured

`evals/routing_eval.py`, Layer 1 only.

| | before | after |
|---|---|---|
| TEST accuracy | 0.610 (n=187) | 0.611 (n=198) |
| TEST escalated to the LLM | 43.3% | 42.9% |
| TEST `daily_digest` precision / recall | – | 1.00 / 0.64 |
| TEST, every other class | | unchanged |
| DEV `daily_digest` precision / recall | – | 1.00 / 0.82 |

- The four TEST digest rows that Layer 1 does not claim escalate to the Layer-2 model, which now
  has `daily_digest` in its tool list.
- **Found by the held-out gate:** with the first phrase set, `mcp_tool` recall on TEST fell
  0.45 → 0.36. "show the latest tech news headlines" no longer resolved locally: the router adds up
  its nearest phrases per route, and the "today / morning / digest" phrases crowded out the news
  tool's. Three news probes of my own showed the same thing (two fell from `mcp_tool` to
  escalation, one was claimed by the digest). Fixed by adding "news headlines for today" and "what
  is in the news this morning" to `mcp_tool`; the probes are now DEV rows. This fix was chosen
  after seeing one TEST row fail, so TEST was looked at twice in this change.
- One DEV row was identical to a router phrase ("what should i focus on today") and was reworded.

## Non-goals

- No change to what the digest contains or to the scheduled job.
- No voice command for the digest (it is not a Tier 0 system function).
- No new intent inside `academic_tracking`; "what should I practice next" still goes there.

## Capabilities

### New Capabilities

### Modified Capabilities
- `proactive-digest`: the digest can be asked for in plain language.

## Impact

- Code: `capabilities/daily_digest.yaml`, `capabilities/index.yaml`, `capabilities/mcp_tool.yaml`,
  `orchestrator.py` (one handler), `evals/routing_golden.csv`, `evals/routing_baseline.json`,
  `tests/test_digest.py`.
- Tier gate: none. The handler reads the practice tracker and stored facts and changes nothing,
  like the existing `digest` command; the capability has no tier and runs no system function.
- Watchdog: none.
- Untrusted-input path: none. The reply is built in code from the owner's own data.
- Prompt caching: the Layer-2 tool list gains one entry, so that prompt's cached prefix changes once.
