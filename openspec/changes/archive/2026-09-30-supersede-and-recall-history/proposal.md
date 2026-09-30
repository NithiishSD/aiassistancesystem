# Proposal

Implements the two open ROADMAP A2 follow-ups.

## Why

Bi-temporal facts (A2) keep history when the user *corrects* a fact, but a fact that arrives
through "remember" was simply added next to the old one. The live store shows the result: four
single-valued attributes currently hold more than one value (where the user lives: 4 values; field
of study: 3; operating system: 2; semester: 2), and semantic search returns whichever is closest.
The history that A2 keeps was also never read: "where did I live before" could not be answered.

## What Changes

- New `fact_attributes.py`: a fixed table of attributes that hold one value at a time (name,
  college, degree, field of study, semester, year, location, address, hometown, age, date of
  birth, email, phone, roll number, CGPA, operating system, code editor, browser, tone), matched
  against the whole attribute name. Anything else, including an explicit "location (alternate)",
  holds many values.
- `memory.remember()`: for a single-valued attribute, stores the new fact first, then marks every
  current fact with a different value as superseded by it; the same value is not stored twice
  ("5th semester" and "5th" are the same). The remember-fact handler uses it and the reply lists
  what was replaced.
- `memory.conflicts()` and `python memory.py --conflicts` (read-only): the conflicts already in
  the store.
- `memory.past_facts()`: matching facts that are no longer valid, with their dates. General Q&A
  adds them, labelled as no longer true, only when the question is about the user and refers to
  the past ("before", "used to", "last year", "was my", "did I", a year, …).

## Measured

- Live store, read-only: 19 of 99 current facts are on a single-valued attribute; 4 attributes
  conflict (11 facts). Two of those groups were the same value written two ways or a hedged guess,
  which is why values are compared without the attribute's own words.
- End to end on a scratch store with the real models and fictional facts: "I live in Lakeview…"
  then "I moved to Hillcrest last month" superseded the residence and said so; two projects both
  stayed current; "where do I live?" answered Hillcrest and "where did I live before?" answered
  Lakeview.

## Non-goals

- The existing conflicts in the live store are not resolved here: which value is right is the
  owner's call. `--conflicts` lists them.
- Facts extracted when the session buffer is flushed are still stored as before. They are a
  model's summary of the conversation, not the user's statement, and should not replace one.
- No model decides whether two facts conflict, and no attribute outside the table supersedes.
- No undo command for a superseded fact; stating the earlier value again supersedes back.
- Found and left: relative dates are stored as written ("User's move date: last month"), and
  "what am I working on?" did not retrieve the two project facts (the known rewording gap, F7).

## Capabilities

### New Capabilities

### Modified Capabilities
- `fact-history`: superseding on remember, listing conflicts, history for questions about the past.

## Impact

- Code: new `fact_attributes.py`; `memory.py` (`remember`, `conflicts`, `past_facts`, CLI flag);
  `orchestrator.py` (remember-fact handler, general Q&A); new `tests/test_fact_superseding.py`.
- Tier gate: none. Remembering a fact was already ungated; nothing is deleted, and a superseded
  fact stays in the store.
- Watchdog: none.
- Untrusted-input path: none new. The fact text still passes the hygiene gate before storage;
  history lines enter the Q&A prompt the same way current facts do.
- Prompt caching: the history block is in the per-request part of the prompt, after the static
  system message.
