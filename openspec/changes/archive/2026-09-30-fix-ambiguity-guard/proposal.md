# Proposal

Closes the ROADMAP A4 follow-up: *"port" in `AMBIGUOUS_TERMS` makes "port this javascript to typescript" stop for a clarifying question.*

## Why

Measured on the golden set, the problem is wider than "port": **37 of 384 clear requests (9.6%) were answered with a clarifying question instead of being handled**, including "write a python function to reverse a linked list", "is docker installed", "memory usage by process please" and "what is a binary tree". The guard runs before routing, so these requests never reached their handler.

Causes: the guard asked whenever a listed term appeared without one of ~30 fixed context words, and the correction check matched substrings ("no" inside "know").

## What Changes

- The guard asks only when the ambiguous term is a **bare topic**: after removing question/request scaffolding ("what is", "tell me about", "explain", …), the term is the only word left. Any other content word already says which meaning is meant.
- Correction signals match whole words.
- Existing checks (context in this or the previous turn) are unchanged.

## Result

Golden false positives: dev 0/197, test 0/187 (was 37/384 overall). Bare questions ("tell me about python", "what is rust", "explain astro", "spark") still ask.

## Capabilities

### Modified Capabilities
- `intent-routing`: when a clarifying question may pre-empt routing.

## Impact

- **Code:** `orchestrator.py` only. Routing and the tier gate are unchanged; more requests now reach them.
