# Design

## Context

See proposal.md for why. There are two implementations today.

**`classifier._is_acknowledgement_or_confirmation(text)`**, used by `classify_intent()` and `evals/routing_eval.py`. It strips punctuation, then returns True if any of these holds:
1. The whole message is in a small exact-match set.
2. Any of `thank you, thanks, thank u, thx, ty, got it, understood, appreciate it` is a **substring**, at any length.
3. The message is ≤ 4 words and any of `okay, ok, thanks, thank, got, understood` is a **substring**.

**Inline in `orchestrator.route_request()`**, which runs before the classifier. It returns True if the whole message is in an exact-match set, OR if (a substring from the list above is present AND the message is ≤ 6 words). It has the same substring flaw.

Probed today: "thanks, now find my resume file", "got it. what's my battery percentage", "book a movie ticket", "how empty is my disk", and "you got it wrong, my exam is on tuesday" are all classified as acknowledgements.

## Goals / Non-Goals

**Goals:** whole-word, whole-message recognition with one implementation. The invariant is that a message is short-circuited only if every word in it is acknowledgement or filler vocabulary, so a short-circuited message can never contain a request.

**Non-Goals:** recognizing conversational replies such as "yes", "cool", or "sure" as acknowledgements. They were never short-circuited, and routing them normally (with session context) is correct.

## Decisions

### D1. Rule: every word is ack/filler, and at least one core ack is present
- Tokenize: lowercase, drop apostrophes, and replace every other non-alphanumeric character with a space.
- `is_acknowledgement(text)` is True iff all of these hold:
  - there is at least one token;
  - every token ∈ `ACK_CORE ∪ ACK_FILLER`;
  - at least one **core** signal is present: a token in `ACK_CORE`, or an adjacent pair `got it` or `sounds good`;
  - there are ≤ 8 tokens.
- `ACK_CORE` = `ok, okay, okie, k, kk, alright, thanks, thank, thanku, thankyou, thx, ty, tysm, understood, noted, appreciate, appreciated, cheers`.
- `ACK_FILLER` = `you, u, so, much, a, lot, very, really, for, the, that, thats, this, it, all, again, bro, man, dude, buddy, zedek, cool, great, nice, perfect, awesome, fine, good, sounds, got, now, then, oh, ah`.
- **Why the core requirement:** without it, "cool", "great", or "fine" alone (filler only) would short-circuit. Those are often replies to a question Zedek just asked, and they must reach routing with session context. The old code never short-circuited them either.
- **Newly recognized:** a few pure acknowledgements the old code missed ("noted", "cheers", "kk", "alright cool", "sounds good bro"). By the invariant above they contain no request, so short-circuiting them is correct. This is a deliberate, small widening, not a regression.
- **Why ≤ 8 tokens:** a very long message made only of polite words is almost certainly more than a thank-you. It is cheap to route normally.
- **Alternatives rejected:**
  - Regex word boundaries around the old substrings. That fixes *type* and *capacity*, but still swallows "thanks, now find my resume file" (rule 2 has no length cap).
  - An LLM check. It costs quota on every message for something a word list decides.

### D2. One public implementation
- Rename to `classifier.is_acknowledgement(text)`, a public name because two modules use it, and update its callers: `classify_intent()` and `evals/routing_eval.py`.
- `orchestrator.route_request()` replaces its inline condition with `classifier.is_acknowledgement(user_input)`.
- The routing eval therefore measures exactly the check production uses. There is no alias for the old private name, since no other caller exists (grep-verified during apply).

### D3. Measure against the committed baseline, then lock it in
- Run `tests/test_routing_eval.py` against the **unchanged** baseline. It must pass, because only improvements are allowed.
- Print the per-class test-slice numbers, before (the committed baseline) and after.
- Regenerate the baseline with `--write-baseline` so the gain becomes the new floor. The golden set is untouched, so its hash is unchanged.

## Risks / Trade-offs

- [A pure politeness message that is longer or unusual (e.g. "thank you for helping me today") now goes through routing] → It lands on general Q&A as before, at the cost of one Layer-1 lookup and sometimes one LLM call.
- [Vocabulary gaps: a real acknowledgement using a word not in the lists is routed normally] → The failure mode is benign: a conversational reply instead of a short-circuit. Words can be added when observed.
- [Moved rows might still escalate rather than land on the right intent] → Expected. The fix removes a hard misroute. Whether Layer 1 then resolves them is the separate coverage problem (74% escalation).

## Migration Plan

- Code-only. Rollback: revert the commit and restore the previous baseline file from git.

## Measurements (2026-09-29)

**Offline gate (Layer 1 only, held-out test slice, 187 rows).** The committed baseline passed unchanged before regeneration (task 2.1).

| | before | after |
|---|---|---|
| `general_question` precision | 0.917 | **1.000** |
| `general_question` recall | 1.000 | 1.000 |
| accuracy | 0.2995 | 0.2995 |
| escalated to LLM | 74.3% | 75.4% |

No other class changed on the test slice. No row in all 374 is now misrouted to `general_question` by the guard (before: 9).

**Why the offline number barely moves.** Of the 9 formerly swallowed rows, 2 now resolve correctly at Layer 1 ("disk capacity and free space please", "how empty is my disk"), and both are in the dev slice. The other 7 now **escalate to the LLM instead of being dropped as small talk**. The gated eval never calls the LLM by design, so it scores "escalated" and "dropped" identically, as misses. The fix's real effect only shows end to end.

**End to end (real `classify_intent`, LLM fallback on, 7 calls).** 5/7 now route correctly (unsupported, open_application, research_task, web_task ×2). Before the fix, all 7 were answered as small talk. Together with the 2 now resolved locally: **7/9 formerly swallowed requests are served correctly, up from 0/9.** The 2 remaining misses are LLM misroutes, not the guard: "you got it wrong, my exam is on tuesday" → academic_tracking, and "port this javascript to typescript" → general_question.

**Found while testing (pre-existing, out of scope, recorded as follow-ups):**
- "port this javascript to typescript" also trips the *ambiguity* check. "port" is in `AMBIGUOUS_TERMS`, so the orchestrator asks which "port" is meant before routing.
- Importing `orchestrator` runs `register_mcp_tools()`, which rewrites the real `data/dynamic_utterances.json` on every test run. The contents are idempotent (verified by hash), but tests should not write user data. It is also how third-party MCP descriptions become router phrases (ROADMAP B2).
