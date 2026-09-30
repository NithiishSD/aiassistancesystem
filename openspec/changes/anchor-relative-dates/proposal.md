# Proposal

Implements the ROADMAP A2 follow-up found during `supersede-and-recall-history` (relative dates
go stale).

## Why

Facts are stored in the user's words. "User's exam: next week" and "User's move date: last month"
are true on the day they are said and wrong or meaningless a month later, and they are then given
to the model as ground truth. The live check of the previous change stored exactly such a fact,
and the digest already has to add "check the dates still hold" to every commitment it shows.

## What Changes

- New `fact_dates.py`: `anchor_relative_dates(text, now)` keeps the user's words and adds what
  they meant on that day, e.g. "next week (week of 5 October 2026)", "last month (August 2026)",
  "tomorrow (1 October 2026)", "on Friday (2 October 2026)", "in 3 days (3 October 2026)".
  Expressions with no single reading ("next Friday", "next semester") get "(said on <day>)"
  instead of a guess. First expression only; already anchored text is left alone. Rules, no model.
- `memory.store()` applies it to facts, using the fact's own timestamp. Conversation rows are
  not changed.

## Non-goals

- Facts already in the store are not rewritten: their timestamps would let a sweep do it, but
  editing stored facts is the owner's decision, like the other cleanups.
- No expiry: a fact about last week's exam is not retired when the date passes.
- No parsing of absolute dates, times of day, or recurring schedules ("every Monday").
- Only English expressions.

## Capabilities

### New Capabilities

### Modified Capabilities
- `memory-hygiene`: relative dates in a stored fact are anchored to the day it was stored.

## Impact

- Code: new `fact_dates.py`; `memory.py` (one call in `store()`); new `tests/test_fact_dates.py`.
- Tier gate, watchdog: none.
- Untrusted-input path: none. The text added is a date computed in code.
- Retrieval: the stored text of such facts gains a short date suffix. The retrieval fixture holds
  no relative dates, so its figures do not move.
