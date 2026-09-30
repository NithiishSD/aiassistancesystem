# Design

### D1. State
`_PREVIOUS_DECISION` is set at the end of `_handle_single` unless the turn was `correct_fact`; a healed misroute sets it to the redone decision, so a second correction applies to the redo.

### D2. Complaint guard (in `route_request`, after the acknowledgement and ambiguity guards)
Whole-phrase regex; only when `_PREVIOUS_DECISION` exists. A false trigger costs one diagnosis call and then normal routing (`other`).

### D3. Diagnosis
`llm_schemas.correction_verdict_model()`: `kind: Literal["fact","routing","other"]`, `intended_intent: Literal[<capability names>] | None`. Static system prompt lists `name: summary` for every capability (byte-identical per run); the dynamic part holds the previous request, its capability, and the correction. Failure → `fact` (the old behavior).

### D4. Heal (after the user confirms)
`_interactive_confirm` asks first; declined → `_route_normally(correction)`; unclear intent → ask, no changes. Then:
`classifier.remove_utterance_dynamically(prev, wrong)`, `classifier.forget_routing(prev)`, append to misroutes log, then `execute(_decision_for(intended, prev, domain, via_llm=True))`. `via_llm=True` routes the redo through the handlers' existing "learn only after verified success" path. For `general_question`, `add_utterance_dynamically(..., user_confirmed=True)` stores the phrase under the anchor (never allowed without explicit user confirmation); `_build_intent_router` merges learned anchor phrases.

### D5. Test isolation
`DYNAMIC_UTTERANCES_PATH` and the misroutes path read env vars; `tests/conftest.py` sets both to temp files before collection.

### D6. Confirmed questions
`_build_intent_router` also refreshes `classifier._CONFIRMED_QUESTIONS` (lowercased learned `general_question` phrases); `classify_intent` returns `function=None` for an exact match before Layer 1, so the Layer-2 LLM cannot re-misroute it.
