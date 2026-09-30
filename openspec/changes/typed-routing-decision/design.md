# Design

- A plain dataclass, not pydantic: it is internal state, never parsed from model output (LangGraph's typed-state idea as a pattern, not a dependency, per ROADMAP).
- `from_dict` maps `_original_input` → `user_input` and rejects anything else unknown, so old-shape callers keep working while typos fail loudly.
- `unsupported` replies keep their text; no router ever set `reason`, so it is now a constant.
