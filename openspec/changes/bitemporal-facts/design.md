# Design

## Context

See proposal.md for motivation. Observed in the code:

- `memory.store()` writes `user_id`, `content_type`, `timestamp`. There is no update path
  other than `memory_hygiene.clean_store()` rewriting documents in place.
- `orchestrator._handle_correction()` calls `memory.delete_by_ids()` **before**
  `memory.store()`. `store()` returns `""` when hygiene rejects the text, and that return
  value is not checked, so a rejected replacement loses the old fact.
- Three read paths build their own Chroma filter: `retrieve()` (inline), `_where()` used by
  the keyword index, and `clean_store()` (unfiltered `collection.get()`).
- The keyword index signature is `(version, collection.count())`. An in-place metadata
  update changes neither unless the version is bumped.
- ChromaDB is 0.5.20. Probed on an ephemeral collection: `{"key": {"$ne": v}}` and `$nin`
  **match rows where the key is absent**; `$eq` does not.

## Goals / Non-Goals

**Goals:**
- One filter, used by every read path, that hides invalidated facts.
- Zero migration: rows without the new fields are current.
- A correction can never lose a fact.

**Non-Goals:**
- Point-in-time queries, automatic supersession on `remember_fact`, any new store.

## Decisions

**1. Four metadata fields, one of them a constant flag.**
`valid_at` (float, set at store time for facts), and on invalidation `invalid_at` (float),
`superseded_by` (id, only when replaced) and `invalidated: True`.
The flag exists because of the probe above: `invalid_at` varies per row, so there is no
single `$ne` value to filter on, while `{"invalidated": {"$ne": True}}` matches both rows
without the key (every existing row) and rows never invalidated.
*Alternative — filter in Python after the query:* rejected; history rows would eat slots in
the dense top-20, which is exactly the crowding F3 measured.
*Alternative — a sentinel `invalid_at: 0` on every row:* rejected; needs a migration of the
live store, and a missed row would silently vanish from `$eq` filters.

**2. `_where()` becomes the only filter builder.**
`_where(user_id, content_type, include_invalidated=False)` adds the flag clause.
`retrieve()` drops its inline copy and takes `include_invalidated: bool = False`.
The keyword index is built through `_where()`, so it excludes history by construction.
Chroma requires `$and` to have at least two clauses; `user_id` plus the flag clause always
gives two.

**3. `memory.invalidate(ids, domain, user_id, superseded_by=None) -> list[str]`.**
Reads the owned, not-yet-invalidated rows, merges the three fields into their existing
metadata, `collection.update(ids, metadatas)`, bumps the keyword index version, logs
`memory_invalidated`. Returns the ids actually changed. Same ownership check as
`delete_by_ids`. Already-invalidated rows are left alone so the first `invalid_at` stands.

**4. Correction order: store, then invalidate.**
`new_id = memory.store(...)`; only if `new_id` is non-empty, invalidate the old row with
`superseded_by=new_id`. An empty `new_id` returns a "nothing was changed" reply.
The same check applies on the delete path: store first, delete only on success.

**5. Keep history unless the old row is not useful (owner decision).**
`_worth_keeping(old_text, new_text)` is False when `memory_hygiene.normalize_fact(old_text)`
rejects the row, or when `new_text` is given and `memory_hygiene._dedup_key` of both match.
False → `delete_by_ids` (today's behaviour). True → `invalidate`.
The user's wording ("delete", "forget") does not change this: a real fact is kept as
history and the reply says so. Permanent removal is the purge command.
*Alternative — hard-delete on erase words:* was the first draft; the owner chose history.
*Alternative — let the model judge usefulness:* rejected; it puts a deletion decision in a
model's hands and changes a structured-output contract that F2's tests pin.

**6. `clean_store()` skips invalidated rows entirely.**
They are not normalized, not counted as duplicates, not repaired. `purge_invalidated(domain,
dry_run=True)` collects rows with the flag and removes them through `delete_by_ids`.
CLI: `--purge-invalidated`, applied only together with the existing `--apply`.

**7. History view.**
`memory.history(query, domain, top_k=10)` = `retrieve(..., include_invalidated=True)`.
`python memory.py --history "<query>" [--domain academic]` prints each fact as
current / superseded / retracted with ISO dates. The existing self-test under `__main__`
writes three rows to the live store on every run; it moves behind `--self-test` so that
asking for history does not write anything.

## Risks / Trade-offs

- [`$ne` on a missing key is observed behaviour of Chroma 0.5.20, not a documented
  guarantee] → a test stores a row with no validity fields and asserts it is retrieved, so
  a Chroma upgrade that changes this fails the suite instead of silently hiding every
  pre-existing fact.
- [History rows are personal data that used to be deleted, and "delete that" no longer
  erases] → never retrieved, the reply states it was kept and names the purge command,
  which removes it permanently. `COMPLIANCE.md` is updated.
- [History grows without bound] → one user, a handful of corrections a month; the purge
  command is the control.

## Migration Plan

None. Existing rows are valid as they are. Rollback is reverting the commit: invalidated
rows would then be retrieved again, so run `--purge-invalidated --apply` first if rolling
back after corrections have been made.
