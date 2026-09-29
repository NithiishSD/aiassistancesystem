# Tasks

## 1. One whole-word acknowledgement check

- [x] 1.1 Replace `classifier._is_acknowledgement_or_confirmation` with `classifier.is_acknowledgement(text)` per design D1 (tokenize, every token in core ∪ filler, at least one core signal, ≤ 8 tokens), and update its callers `classify_intent()` and `evals/routing_eval.py`. Verify: grep shows no remaining reference to the old name. `tests/test_acknowledgement.py` shows that pure acknowledgements ("okay thank you", "thanks a lot bro", "ok got it", "cool, thanks zedek", "ty", "sounds good") are True, and so are the deliberately newly recognized "noted", "cheers", and "sounds good bro". It also shows that every misfire from the proposal table, plus "got it. what's my battery percentage", "cool", "yes", and "i forgot my password file location", is False.
- [x] 1.2 `orchestrator.route_request()` calls `classifier.is_acknowledgement()` instead of its inline condition. Verify: tests show `route_request("port this javascript to typescript")` reaches `classifier.classify_intent` (patched to observe), `route_request("okay thank you")` short-circuits without calling it, and both entry points agree on every phrase in the 1.1 lists. The existing `"okay thank you"` tests in `tests/test_ambiguity.py` and `tests/test_hybrid_router.py` pass unchanged.

## 2. Measure

- [x] 2.1 Run `tests/test_routing_eval.py` against the **committed** baseline, before regenerating it. Verify: it passes (no class regressed).
- [x] 2.2 Record the before/after test-slice per-class precision and recall for every class that changed, plus accuracy and escalation rate, in `design.md` under Measurements. Then run `python evals/routing_eval.py --write-baseline`. Verify: the golden hash in the new baseline is unchanged, the must-stay-local test passes, and the recorded numbers match the new baseline.

## 3. Finish

- [x] 3.1 Add a line to ROADMAP A4 with the before/after headline, and run the full suite `./zedek-env/bin/python -m pytest -q`. Verify: 0 failures; report the test count before (622) and after.
