# Proposal

Implements ROADMAP F8 (condenser and stuck detection), scaled to what Zedek actually has.

## Why

The session buffer holds 10 messages. When it fills, six are dropped after their facts are
extracted, and everything else said in them is gone: "which one was faster?" two minutes later
has nothing to refer to. Separately, the browser planner can propose the same action on the same
page again; the tools are stateless, so it returns the same thing and costs the user another
typed confirmation.

## What Changes

- The flush that already runs when the buffer fills (one local-model call) now also returns a
  one-or-two-sentence summary of the turns being dropped. It is kept for the session, carried
  into the next flush, bounded to 600 characters, and given to general Q&A with the per-turn
  content. It is cleared when the session ends. No extra model call.
- The flush reviews only the turns that are leaving. It used to review all ten and keep four, so
  those four were reviewed again at the next flush and their facts stored twice.
- Browser: if the next proposed action is identical to one that already ran (succeeded or
  failed) in this run, the run stops with a note instead of executing it again.

## Non-goals

- No token-count trigger ("70% of the context window"): the buffer is capped at 10 messages, far
  below any provider's window, so a size trigger would never fire.
- No separate "save anything durable" turn: the existing flush is that turn.
- No change to the coding agent's retry loop: it makes at most two attempts, so there is nothing
  to cut short.
- The summary is not stored in long-term memory.

## Capabilities

### New Capabilities
- `session-context`: what is kept of a conversation once turns leave the short-term buffer.

### Modified Capabilities
- `web-agent`: a repeated identical action ends the run.

## Impact

- Code: `orchestrator.summarize_and_flush_session`, `answer_general_question`; `web_agent.browse`.
- Tier gate, watchdog: none. The repeat check runs before the gate and can only stop a run.
- Untrusted-input path: none. The summary is made from the conversation by the local model and
  is read only by the answering model, which chooses no actions.
- Prompt caching (F5): unaffected; the summary sits with the per-turn content in the last message.
- Privacy: the summary is sent to the answering model (cloud first) like the turns it replaces.
