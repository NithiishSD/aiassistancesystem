# Tasks

## 1. Sanitizer

- [x] 1.1 Add `sanitize_tool_description(text, other_tool_names=()) -> (str, int)` to `text_sanitizer.py` (design D2). Verify: `tests/test_mcp_tool_trust.py` covers each spec scenario:
  - the poisoned `<IMPORTANT>` description
  - shadowing
  - each pattern family
  - a benign description kept unchanged
  - the 300-character cap
  - `None` and empty input

## 2. MCP client

- [x] 2.1 Add `prompt_description` to `MCPToolSpec`, and set `description` to the invisible-stripped raw text at discovery (D1). Point `orchestrator._select_mcp_tool` and `classifier.register_mcp_tools` at `prompt_description`. Verify:
  - A test shows that the selection prompt excludes the poisoned sentence.
  - `tier_gate.classify` still escalates on a verb that appears only in a removed sentence.
  - A zero-width-split verb escalates.
- [x] 2.2 Make `default_tier` mandatory, fail-closed to 2 (D3), and declare it for every server in `mcp_servers.json`. Verify:
  - `_load_config` tests cover the missing, invalid, and valid cases.
  - The bundled config has no server that is missing it.
  - The existing `test_tier_gate_mcp_default_tier_1` still passes.
- [x] 2.3 Add tool pinning (D4): the lock file, drift exclusion, `drifted_tools()`, `accept_tool_changes()`, the corrupt-lock recovery, the `--review`/`--accept` CLI, and the orchestrator startup warning. The conftest redirects `ZEDEK_MCP_LOCK_PATH`, and `.gitignore` covers the lock. Verify with tests using stubbed discovery:
  - first sight pins
  - changed description → excluded, while its sibling stays
  - accept → available
  - a disappeared-then-changed tool is still caught
  - a corrupt lock is moved aside
  - the real `data/` lock is not touched by the suite
- [x] 2.4 Strip MCP results in `_normalize_tool_result`, and research sources in `research_agent` (D5). Verify with tests for zero-width characters in both paths.

## 3. Finish

- [x] 3.1 Update ROADMAP B2/B6, `TECH_STACK.md` (security row), and `COMPLIANCE.md` if it lists MCP controls. Run the full suite. Verify: 0 failures; report the count before (739) and after. Also confirm that the 27 bundled tools all pass through the sanitizer unchanged.
