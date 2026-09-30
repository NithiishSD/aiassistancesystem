# Proposal

Implements the open ROADMAP A1 item "Reject hedged or speculative facts".

## Why

The store still accepts LLM guesses as facts, such as "User's Current Studies: Data Structures
(presumably a course at …)" and "Operating System: likely a Linux-based system". One such guess
outranked the real college fact on the live store. A guess is not something the user said.

## What Changes

- The hygiene gate rejects a fact whose value is hedged: presumably, likely, probably, possibly,
  maybe, perhaps, apparently, "not explicitly stated", "implied", "inferred", "assumed".
- The existing sweep (`python memory_hygiene.py`) therefore reports such rows; applying it is
  still an explicit owner action (`--apply`).
- Because a hedged row is now "not a usable fact", correcting or retracting one deletes it
  instead of keeping it as history (the `fact-history` rule already says so).

## Non-goals

- No sweep of the live store in this change; only the dry-run count is reported.
- No model-based judgement of whether a fact is speculative.
- Hedge words in the attribute name (left of the colon) are not checked.

## Capabilities

### New Capabilities
- `memory-hygiene`: which candidate facts are refused on the way into long-term memory.

### Modified Capabilities

## Impact

- Code: `memory_hygiene.normalize_fact()`; tests in `tests/test_memory_hygiene.py`.
- Tier gate: none. Watchdog: none. Untrusted-input paths: none (the check is a regex on text
  about to be stored; it only ever refuses).
- Risk: a fact the user really phrased with "probably" is refused. Accepted: a fact the user is
  unsure of should not be replayed to the model as ground truth.
