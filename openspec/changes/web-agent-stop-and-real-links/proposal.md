# Proposal

Implements the two ROADMAP B1 follow-ups found in the live run.

## Why

In the live run the planner ignored "stop when goal_satisfied is true" and spent three steps
(three typed confirmations) reading one page. The extractor also invented a link
(`example.com/learn-more`): it is shown only page *text*, so it has no way to know which
links exist and guesses.

## What Changes

- The browsing loop stops by itself as soon as a successful step reports the goal satisfied.
  The planner is not asked again.
- The browser's navigate and get-text tools append the page's real links (absolute http(s)
  URLs, de-duplicated, capped) after the page text.
- The web agent separates that list from the page text. The extractor is shown the links that
  are on the user's domains and told to pick `next_url` from them or return null.
- A page-proposed URL is offered to the planner only if it is one of those real links. An
  invented URL is dropped and logged, even when it is on the user's domain.

## Non-goals

- No change to the tier gate, confirmation, or the domain allowlist: those checks still run,
  and the new one is in addition.
- No change to click selectors (still proposed from text and shape-checked).
- No change to the MCP tool descriptions, so the tool lock does not need re-accepting.

## Capabilities

### New Capabilities

### Modified Capabilities
- `web-agent`: deterministic stop on goal satisfaction; page-proposed URLs must be real links.

## Impact

- Code: `web_agent.py` (loop, link parsing, extractor prompt, `_admit`),
  `mcp_playwright_server.py` (link list). Tests: `tests/test_web_quarantine.py`.
- Tier gate: none. Watchdog: none.
- Untrusted-input path: tightened. The link list is untrusted page data; it reaches only the
  tool-less extractor, never the planner, and every link still passes the scheme and
  user-domain checks in code. A page that fakes the link list in its text gains nothing: the
  result is still limited to the user's own domains and still needs typed confirmation.
- Fewer confirmations per browsing task; fewer LLM calls.
