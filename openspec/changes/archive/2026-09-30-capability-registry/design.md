# Design

- **Why YAML, not Python:** data only, no code runs at load (`safe_load`), and a capability's router phrases, description and tier sit in one reviewable file. OpenClaw/Open Interpreter use the same shape (ROADMAP E1).
- **Two orders in index.yaml:** semantic-router route order and the Layer-2 prompt order differed historically; both are kept exactly so neither routing nor prompt caching moves in this change.
- **`general_question`:** listed in `llm_tools` only; its utterances are the negative anchor route, never an action.
- **Copies:** every accessor returns fresh containers, so a consumer mutating its table (the classifier extends routes with learned phrases) cannot change the registry.
- **Migration check:** snapshots of every table, the prompt JSON, and `classify_offline` on all golden rows were taken before the change and compared after: all identical.
