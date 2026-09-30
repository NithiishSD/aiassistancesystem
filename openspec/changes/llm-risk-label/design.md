# Design

- The label is computed after every other lane and combined with `max`; early returns (unknown function, force-3) are unaffected, so it cannot change a blocked result.
- Decoding format: `{"type":"object","properties":{"args": <tool schema or {"type":"object"}>, "risk": {"enum": [...]}},"required":["args","risk"]}`; `_validate_mcp_args` still validates `args` against the tool schema before any call.
