# Proposal

Security fix found while building B6 (`llm-risk-label`).

## Why

`fetch_url` and `search_arxiv` (online_tools MCP server) run an SSRF check on the first URL, then let httpx follow redirects automatically. A public URL that redirects to `http://169.254.169.254/` (cloud metadata) or `http://localhost:…` is fetched without a check. `fetch_url` is reachable from research (a URL in the question), MCP tool calls and the research agent.

The address check also used a hand-written network list that missed `0.0.0.0` (reaches localhost on Linux), `100.64.0.0/10` (CGNAT), `198.18.0.0/15`, multicast, and IPv4-mapped IPv6 (`::ffff:127.0.0.1`). An unparseable resolved address was skipped instead of rejected.

## What Changes

- `_get_checked`: redirects are followed by hand (at most 5), and the SSRF check runs on every hop before it is requested. Used by `fetch_url` and `search_arxiv`.
- `_ip_blocked`: only globally routable unicast addresses pass (`is_global`, not multicast), IPv4-mapped addresses are unwrapped, the explicit list is kept as a second check; unparseable addresses fail closed.

## Known limit

DNS rebinding (the name resolves publicly at check time and privately at connect time) is not addressed; closing it needs connecting to the checked IP, which breaks TLS name checks without more plumbing.

## Capabilities

### Modified Capabilities
- `research-sources`: fetched URLs stay public across redirects.

## Impact

- **Code:** `mcp_online_server.py`. The MCP tool lock is unaffected (tool definitions unchanged).
