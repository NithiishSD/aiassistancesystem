# Tasks

- [x] 1.1 `RoutingDecision` with validation, legacy `from_dict`, `log_fields`.
- [x] 1.2 `route_request`, `_handle_single`, `execute`, every handler and the correction path use it.
- [x] 1.3 Tests: legacy keys, unknown keys raise (directly and via `execute`), domain/confidence validation, unshared args, logs exclude args, `route_request` and `_handle_single` carry the input.
- [x] 1.4 Full suite and routing gate, 0 failures (1057 → 1064). Update ROADMAP E2; record the correction self-heal finding.
