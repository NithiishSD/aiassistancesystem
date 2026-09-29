# Proposal

Fixes the production bug found by the first routing baseline (**ROADMAP A4**, recorded in `openspec/changes/archive/2026-09-29-add-routing-eval/design.md` → Measurements).

## Why

Zedek short-circuits "thanks/okay" replies so they don't trigger an action. The check matches words as raw **substrings**, so real requests are classified as acknowledgements. They then skip **both** the local router **and** the LLM fallback, and get a chat reply instead of being served:

| Request | What matched | Should route to |
|---|---|---|
| "type hello world into the textbox on example.com" | "ty" in *type* | web_task |
| "disk capacity and free space please" | "ty" in *capacity* | free_space_summary |
| "port this javascript to typescript" | "ty" in *typescript* | coding_task |
| "open facebook" / "book a movie ticket" | "ok" in *facebook*, *book* | open_application / unsupported |
| "you got it wrong, my exam is on tuesday" | "got it" | correct_fact |
| "thanks, now find my resume file" | "thanks" (any length) | search_files |

The same bug exists **twice**. `classifier._is_acknowledgement_or_confirmation()` has it, and `orchestrator.route_request()` has its own copy that runs *first* (for inputs of ≤ 6 words). The routing eval only exercises the classifier's copy, so the orchestrator's copy was invisible to it.

## What Changes

- Replace substring matching with **whole-message, word-level** matching. A message is an acknowledgement only if **every** word in it is acknowledgement or filler vocabulary ("okay thank you", "thanks a lot bro", "ok got it", "cool, thanks zedek"). If any other word is present ("thanks, now find my resume file"), it is a request and routes normally.
- **One implementation.** `orchestrator.route_request()` calls the classifier's guard instead of keeping its own copy, so the routing eval now measures the check production actually runs.
- Measure the fix against the committed routing baseline. The gate must pass, since improvements are allowed. Then regenerate the baseline deliberately to lock in the gain, and record the before/after per-class numbers.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `intent-routing`: adds a requirement defining what counts as an acknowledgement (whole-message, word-level), and that a request containing acknowledgement words alongside other content is routed normally. The existing three requirements are unchanged.

## Impact

- **Code:**
  - `classifier.py`: replaces `_is_acknowledgement_or_confirmation()` with a public `is_acknowledgement()`, now shared by two modules, and updates its callers (design D2).
  - `orchestrator.py`: `route_request()` drops its inline guard and calls the shared one.
- **Behavior:** a message is short-circuited only if it contains nothing but acknowledgement and filler words, so it cannot also contain a request. Many fewer messages are short-circuited overall. A few pure acknowledgements the old check missed ("noted", "cheers", "kk", "sounds good bro") are now recognized as well, which is correct, since none of them asks for anything. Messages that contain a request alongside "thanks" now reach the router. A long, purely polite message that is not all ack/filler words (e.g. "thank you for helping me today") now goes through normal routing instead of being short-circuited. It still ends up as a general reply, at the cost of one Layer-1 lookup and possibly one LLM call.
- **Tier gate / watchdog:** none directly. Some requests that were silently dropped (e.g. "type … into the textbox") will now reach agents that still gate every action.
- **Untrusted-input path:** none.
- **Tests:** new unit tests for true acknowledgements, and for every misfire listed above. The existing acknowledgement tests (`"okay thank you"` → None) must keep passing. The routing gate runs against the committed baseline, and the baseline is then regenerated.

## Non-goals

- Raising Layer-1 coverage (the 74% escalation rate). That is a separate, larger change.
- Changing the golden set. It stays byte-identical so the before/after comparison is clean.
- Changing the unsupported-action guard, which already uses word boundaries.
