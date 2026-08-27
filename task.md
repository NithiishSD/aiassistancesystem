# Task: Read-Only Online MCP Servers

- [x] Verify mcp_servers.json schema (array of objects with `name` field — confirmed)
- [/] Create `mcp_online_server.py` with fetch_url, search_wikipedia, search_arxiv
  - [/] SSRF blocklist guard on fetch_url
  - [/] User-Agent headers on Wikipedia + arXiv
  - [/] httpx explicit timeout (separate from MCP server timeout)
  - [/] Graceful error returns (no exceptions propagating)
- [ ] Update `mcp_servers.json` to register `online_tools`
- [ ] Add tests in `tests/test_mcp_online.py`
  - [ ] Discovery of online_tools server tools
  - [ ] Tier-1 default for online tools (no false escalations)
  - [ ] SSRF blocked URLs return error, not exception
  - [ ] Mock HTTP responses for all three tools
- [ ] Run full test suite (./zedek-env/bin/pytest) — must stay 115+ passing
