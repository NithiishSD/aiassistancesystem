# Proposal

Owner request: when corrected, Zedek should tell whether it **stored a fact wrongly** or **misclassified the request**, fix memory in the first case, and in the second fix the routing and adapt, so the same mistake does not repeat.

## Why

- Corrections only ever touched memory. A misrouted request ("I didn't ask you to open an app, I asked what apps I have") was treated as a fact correction and answered with "I don't have a stored fact that matches".
- The old routing self-heal read the *current* turn's decision (found in E2), so it never un-learned the phrase that caused the mistake, and it never learned the right one.
- Complaints about *what Zedek did* rarely reach `correct_fact`: "that's not what i asked" (0.38), "you misunderstood me" (0.48) escalate to the LLM, and "i wanted the disk usage not the memory" routes to `free_space_summary`.
- Tests write the owner's real `data/dynamic_utterances.json` (open ROADMAP A4 follow-up); this change adds more learning, so that is fixed first.

## What Changes

- **Previous turn tracked:** `_PREVIOUS_DECISION` is the last completed non-correction turn's decision.
- **Complaint guard:** with a previous turn, messages that complain about the action ("not what I asked", "you misunderstood", "I didn't ask", "I meant/wanted …", "wrong app/file/…") go to the correction flow without routing.
- **Diagnosis:** one schema-constrained call (`CorrectionVerdict`: `kind` = `fact` | `routing` | `other`, `intended_intent` = a capability name or null). The model sees only the user's previous request, the capability it was routed to, and the correction, **never Zedek's previous reply** (which can carry web content; untrusted text must not reach a model that picks actions).
- **`fact`:** the existing memory correction (update or retract).
- **`routing`:** the diagnosis only *proposes*. Zedek asks "Did I misunderstand "<request>"? Redo it as: <capability>? (y/n)" before anything changes (agents never self-approve). On no, the message is handled as a new request and nothing is un-learned. On yes:
  1. un-learn the previous request's phrase from the wrong capability and drop its routing-cache entry;
  2. log the misroute to `data/misroutes.jsonl` (gitignored) for promotion into the golden set (ROADMAP A4);
  3. re-run the previous request as the intended capability through `execute()`, so the tier gate and every confirmation apply;
  4. learn the phrase under the intended capability through the existing execution-verified learning (only after success). "It was just a question" teaches the general-question anchor, which today can never be learned.
  If the intended capability is unclear, Zedek asks what the user wanted and changes nothing.
- **Confirmed questions stick:** a request the user confirmed was "just a question" is answered directly on an exact repeat; neither the router nor the Layer-2 LLM can turn it into an action again (the anchor route alone still escalates to the LLM).
- **`other`:** the message is routed normally.
- `ZEDEK_DYNAMIC_UTTERANCES_PATH` and `ZEDEK_MISROUTES_PATH`; tests point both at temp files.

## Live check (local qwen3:8b, cloud off)

Diagnosis on hand-written cases: 6/8 at first. Misses: "which apps are installed" diagnosed as process listing, and "thanks, now find my notes folder" taken as a complaint. After one prompt rule ("a NEW request is other") and a clearer `system_inspect` summary: 8/8 on those cases, 3/4 on four fresh cases. The remaining miss ("i wanted to also open the file manager" read as a complaint) is why healing needs the user's confirmation instead of more prompt tuning.

## Capabilities

### Modified Capabilities
- `intent-routing`: corrections diagnose and heal misroutes.

## Impact

- **Code:** `orchestrator.py`, `classifier.py`, `llm_schemas.py`, `tests/conftest.py`, `.gitignore`.
- **Tier gate:** a redone request goes through `execute()` like any other; the model's choice of intent cannot skip a gate or confirmation.
- **Routing gate:** static routes unchanged (the eval builds without dynamic phrases).
