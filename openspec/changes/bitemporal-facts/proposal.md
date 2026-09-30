# Proposal

Implements ROADMAP A2 (bi-temporal facts).

## Why

Memory has no way to say "this fact stopped being true". A correction deletes the old row
and stores a new one, so the history is gone, and if the second step fails or is rejected
by memory hygiene the old fact is lost with nothing in its place. The live store has
already held contradictory pairs (`...exam next week` beside `...exam next week: false`)
because a retraction had nowhere to go except another row of text.

## What Changes

- Every stored fact records when it became valid. A fact can be marked no longer valid,
  with the time and, when it was replaced, the fact that replaced it.
- Correcting a fact stores the replacement first and then marks the old fact superseded.
  The old fact is no longer deleted. If the replacement is rejected, the old fact stays
  current and the user is told nothing changed.
- Saying a fact is no longer true (without a new value) marks it invalid and keeps it as
  history.
- History is kept only when it is worth keeping (owner decision, 2026-09-30). The old row
  is hard-deleted instead when it is not a usable fact (memory hygiene rejects it: LLM
  preamble, null value, placeholder, …) or when the replacement says the same thing
  (a history row would be a duplicate). Everything else is kept, including when the user
  says "delete that": the fact stops being used at once, the reply says it was kept as
  history, and the purge command removes it permanently.
- Facts marked invalid are excluded from every retrieval path: embedding candidates,
  keyword candidates, the relevance-ranked results used by general Q&A and the research
  agent, and the candidate list offered when correcting a fact.
- Rows written before this change have none of the new fields and are treated as valid.
  No migration and no re-embedding.
- The hygiene sweep ignores invalidated rows when looking for duplicates, so a current
  fact is never deleted as a "duplicate" of its own history. A new
  `python memory_hygiene.py --purge-invalidated` permanently removes history rows
  (dry run by default, like the existing sweep).
- `python memory.py --history "<query>"` lists matching facts including superseded ones,
  with their dates.
- The correction reply says what actually happened: updated (old kept as history),
  marked no longer true (kept as history), or permanently deleted.

## Non-goals

- No automatic supersession when a *new* fact is remembered for an attribute that already
  has a value. Attributes can be multi-valued (two hobbies, five courses), so that needs
  its own measured change.
- No "as of date" queries ("where did I live last year"). The data to answer them is now
  kept; the question path is not built.
- No graph store, no Zep/Graphiti/Neo4j dependency. Schema only, as ROADMAP A2 says.
- No change to conversation rows, the academic tracker's JSON store, or the F6 profile block.
- No sweep of the live store. Existing rows stay as they are.

## Capabilities

### New Capabilities
- `fact-history`: how a stored fact stops being current (superseded, retracted, erased),
  what is kept as history, and how history is inspected and purged.

### Modified Capabilities
- `memory-retrieval`: facts that are no longer valid are excluded from candidates and results.

## Impact

- Code: `memory.py` (metadata on store, `invalidate()`, retrieval filter, keyword index
  scope, `--history`), `orchestrator.py` (`_handle_correction`), `memory_hygiene.py`
  (duplicate scan, `--purge-invalidated`).
- Docs: `ROADMAP.md` A2, the "Correction and erasure" row in `COMPLIANCE.md`.
- Dependencies: none new.
- Tier gate: none. Correcting a fact keeps its current tier and confirmation behaviour.
- Watchdog: none.
- Untrusted-input paths: none added. The delete/keep decision is the existing
  deterministic hygiene check on the stored text, not a model judgement. The model still
  only picks which candidate fact is meant and the corrected value, as today.
- Privacy: history rows are personal data that used to be deleted. They stay local in the
  same store, are never sent to a model by retrieval, and can be purged with one command.
  A spoken "delete that" no longer erases by itself; the reply says so and names the purge.
