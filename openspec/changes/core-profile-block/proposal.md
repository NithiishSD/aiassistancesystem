# Proposal

Implements ROADMAP F6 (core memory block).

## Why

Retrieval misses rewordings. On the 50-question eval, "what do people call me", "what am I
majoring in" and "how far along am I in my course" do not retrieve the name, programme or
semester facts, and neither a different embedding model nor a larger reranker fixed that
(`retrieval-eval-model-decision`). The facts that matter most are few, so they can simply be
included.

## What Changes

- New `user_profile.py`: selects the core facts from the facts currently valid (name, college,
  degree/programme/department, semester/year, subjects, goals, target companies, placement
  focus), one per attribute (the newest), in a stable order, at most 12 facts and 1,200
  characters. Built fresh each time, so a correction takes effect at once.
- General Q&A adds the core facts to the long-term facts **only when the question refers to the
  user** (I / my / me). Facts retrieval already returned are not repeated.
- A general-knowledge question still gets no stored facts.
- Sensitive attributes (location, address, contact details, roll number, room, account names)
  are never part of the block. They are still returned when retrieval finds them relevant.
- Measured with `evals/retrieval_eval.py` (new `+profile` column), labelled facts present in the
  answer context at 37 / 187 / 412 facts: **36 / 35 / 34 of 43 before, 40 / 40 / 39 after.**
  Leaks unchanged (the block is not added to general questions). The attribute patterns were
  written while looking at this fixture, so the gain on other facts may be smaller.
- The test suite now runs against a throwaway memory store (`ZEDEK_CHROMA_PATH` set in
  `tests/conftest.py`), so no test can read or write the live one.

## Non-goals

- No separate profile file. ROADMAP suggested git-tracked markdown; this repository is public and
  the profile is personal data, and the fact history (A2) already records every change.
- No profile in the research agent, the web agent or any prompt other than general Q&A.
- No model-written profile summary.

## Capabilities

### New Capabilities

### Modified Capabilities
- `memory-retrieval`: general Q&A may include core profile facts for questions about the user.

## Impact

- Code: new `user_profile.py`; `orchestrator.answer_general_question`; `evals/retrieval_eval.py`;
  `tests/conftest.py`.
- Tier gate, watchdog: none.
- Untrusted-input path: none. The block contains only facts that passed memory hygiene.
- Privacy: questions about the user now send up to 12 core facts to the answering model (cloud
  first) even when retrieval found nothing. General questions send nothing, as before. Sensitive
  attributes are excluded from the block.
- Prompt caching (F5): unaffected; the facts were already in the last, per-turn message.
