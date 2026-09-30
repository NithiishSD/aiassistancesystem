# Tasks

- [x] 1.1 Test isolation: env-configurable dynamic-utterances and misroutes paths; conftest sets them (closes A4 follow-up).
- [x] 1.2 classifier: `forget_routing`, cache drop on learn, user-confirmed general-question learning merged into the anchor.
- [x] 1.3 orchestrator: `_PREVIOUS_DECISION`, complaint guard, `_decision_for`, diagnosis, heal, misroute log; remove the old self-heal.
- [x] 1.4 Tests for every spec scenario plus: guard only with a previous turn, `other` routes normally, diagnosis failure falls back to fact correction, prompt excludes the reply, second correction applies to the redo.
- [x] 1.5 Full suite and routing gate (1064 → 1085); ROADMAP A4/E2 updates.
- [x] 1.6 Live diagnosis check on local qwen3:8b; user confirmation before healing; confirmed-question shortcut.
