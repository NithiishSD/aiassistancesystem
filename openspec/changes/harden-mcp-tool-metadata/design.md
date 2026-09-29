# Design

## Context

- `mcp_client._async_discover_server()` builds `MCPToolSpec(server_name, tool_name, description, input_schema, qualified_name)` from `list_tools()`.
- `discover_all_tools()` replaces `_TOOL_REGISTRY` and `_SERVER_REGISTRY` atomically.
- Descriptions are consumed in three places:
  - the tier gate: `tool_spec.description` at three call sites (orchestrator, web agent, research agent)
  - `orchestrator._select_mcp_tool()`: an LLM prompt
  - `classifier.register_mcp_tools()`: routing phrases
- `_load_config()` defaults a missing or invalid `default_tier` to 1.
- `text_sanitizer.strip_invisible()` exists and the web agent uses it.

## Decisions

### D1. Two description fields, split by consumer
- `description = strip_invisible(raw)`. This is what the **tier gate** sees.
  - The gate's lanes only raise the tier, so removing text before the gate could only lower risk. Keeping the full text is the safe side.
  - Stripping invisibles closes a verb-splitting evasion.
  - The existing call sites and test fakes keep working unchanged.
- `prompt_description = sanitize_tool_description(raw, other_tool_names)`. This is what **models and the router** see.
- **Alternative rejected:** sanitizing `description` in place. That would silently weaken the gate whenever a removed sentence contained an effect verb.

### D2. `sanitize_tool_description(text, other_tool_names=()) -> tuple[str, int]`
It returns the cleaned text and the count of removed pieces. Steps:
1. Apply `strip_invisible`.
2. Remove paired tag blocks `<tag>…</tag>` (DOTALL, non-greedy), which is the documented poisoning carrier. Then remove any stray `<…>` tags.
3. Split into sentences on `(?<=[.!?])\s+` and newlines. Drop a sentence if it matches any of:
   - **override:** `ignore|disregard|forget|override` together with `previous|prior|above|earlier|other|all|system` together with `instruction|rule|prompt|message|direction`
   - **obligation to the model:** `\byou (must|should|need to|have to|are required to|will)\b`, and `\b(assistant|AI|model|LLM)\s+(must|should)\b`
   - **concealment:** `(do not|don't|never|without)` together with `(tell|mention|inform|reveal|show|notify|alert)` together with `user`
   - **sequencing directives:** `\b(before|after|instead of|prior to)\s+(using|calling|running|invoking|executing)\b`, and `\b(always|first)\s+(call|run|use|invoke|execute|read|send)\b`
   - **secrets/paths:** `~/|/etc/|\.ssh\b|id_rsa|\.env\b|private key|mcp\.json|mcp_servers\.json`
   - **role markers:** `^\s*(system|assistant|user)\s*:`, and `system prompt`
   - **shadowing:** a whole-word match of any name in `other_tool_names` (bare tool names of other registered tools)
4. Collapse whitespace and cap at 300 characters on a word boundary.

The patterns target instruction-to-the-model shapes, not tool behavior. All 27 bundled descriptions pass through unchanged, and a test asserts this against the bundled servers.

### D3. Mandatory `default_tier`, fail-closed to 2
- A missing, non-int, or out-of-range value → `default_tier = 2`, logged as `mcp_config_default_tier_missing` or `mcp_config_invalid_default_tier`.
- The gate already applies it as a floor (`max`), so no tier_gate change is needed.
- `mcp_servers.json` gets explicit values that preserve today's effective tiers.

### D4. Tool pinning (trust on first use)
- The lock path comes from env `ZEDEK_MCP_LOCK_PATH`, defaulting to `data/mcp_tool_lock.json`, resolved at call time like the provider usage file.
- The file holds `{"version": 1, "tools": {qname: {"sha256", "description", "input_schema"}}}`, and is written atomically (temp file + `os.replace`).
- The hash is `sha256` of `json.dumps({"description": raw, "input_schema": schema}, sort_keys=True)`. It uses the **raw** description, so an invisible-only change is still drift.
- During discovery:
  - An unknown qname is pinned (`mcp_tool_pinned`).
  - A matching hash is kept.
  - A mismatch is **excluded from the registry** and recorded in `_DRIFTED` (`mcp_tool_drift_blocked`, warning).
  - Pins for tools that disappeared are kept, so a changed tool that comes back is still caught.
- A corrupt or unreadable lock is moved aside to `*.corrupt-<timestamp>` and rebuilt (warning). Refusing every tool forever would leave the user with no way forward except deleting files.
- `drifted_tools()` returns `{qname: {"old": ..., "new": ...}}` descriptions for review.
- `accept_tool_changes(qnames=None)` re-pins the current definitions of the drifted tools, and the caller rediscovers.
- **Why exclude and not force Tier 2:** a changed description can poison the *selection prompt* before any gate runs. Excluding the tool removes it from every model-visible surface. The cost is that the tool is unavailable until the user reviews it.
- **CLI:** `python mcp_client.py --review` lists the drift with the old and new text. `--accept` re-pins all drifted tools and reports them.
- **Startup:** `orchestrator._init_mcp()` logs one warning naming the disabled tools and the review command.

### D5. Hostile output stripping
- `_normalize_tool_result()` applies `strip_invisible` to result and error text. This covers every MCP consumer.
- `research_agent` strips each source's content in `gather()`'s `add()`, as defense in depth for non-MCP sources.

## Risks

- **False-positive sentence removal** on a legitimate third-party description only affects model-facing text, never the gate. The removal is logged with a count.
- **A legitimate server update disables its changed tools** until `--accept` is run. That is intended, and the startup warning says how to recover.
