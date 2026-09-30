# Proposal

Implements the ROADMAP A4 retrieval eval set item and decides ROADMAP F7 (embedding model).

## Why

F7 says to pick the embedding model "with the eval set", and the rejected list says to try a
larger reranker only "if the eval set shows a gap". Neither had been measured. The fixture had
29 questions, all phrased close to the stored facts, so it could not show a gap.

## What Changes

- `evals/retrieval_fixture.json` gains 21 harder `paraphrases` (18 rewordings such as "what do
  people call me" for the name fact, 3 more general-knowledge questions): 50 questions in all.
  The strict test keeps asserting on the original `queries`.
- New `evals/retrieval_eval.py`: runs the production retrieval at store sizes 37 / 187 / 412 and
  reports, per embedding model, how many labelled facts reach the candidate pool, final recall,
  relevant-first, leaks and time; with `--reranker`, recall and leaks at each relevance threshold.
- Measured (2026-09-30, laptop CPU), no production change:
  - Embeddings, candidate pool at 187 / 412 facts: MiniLM-L6 36/43 and 36/43; bge-small-en-v1.5
    34/43 and 35/43; snowflake-arctic-embed-s 7/43 and 3/43. **Keep MiniLM-L6.**
  - Rerankers at a zero-leak threshold, recall at 37 / 187 / 412 facts: ms-marco-MiniLM-L-6
    (installed) 35 / 35 / 35; ms-marco-MiniLM-L-12 35 / 33 / 34; bge-reranker-base 34 / 33 / 30,
    at 5x the time and 1.1 GB. **Keep the installed reranker.**
  - The gap that remains is rewording: 36/43 on the 50 questions at the current threshold, with
    one leak ("who wrote the Python language" returns the favourite-language fact). Neither
    candidate closes it. That is evidence for F6 (keep core facts always in the prompt).
- ROADMAP F7 and the rejected list are updated with these numbers.

## Non-goals

- No change to the embedding model, the reranker, or `RELEVANCE_MIN_SCORE`.
- The router embedding swap (`model2vec`) is not measured: Layer 1 already takes about 5 ms.
- No downloaded candidate model is added to the repository or `setup.sh`.

## Capabilities

### New Capabilities

### Modified Capabilities

No spec changes: this adds measurement tooling and records a decision (`skip_specs: true`).

## Impact

- Code: `evals/retrieval_eval.py`, `evals/retrieval_fixture.json`, a test of the harness.
- Tier gate, watchdog, untrusted-input paths: none.
