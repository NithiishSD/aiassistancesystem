# Proposal

Implements **ROADMAP B1** (quarantine web-page content from the planner). It also delivers the shared invisible-Unicode sanitizer from **ROADMAP B6**, applied here to the web agent only. B6 remains open for applying it to the research agent and MCP registration.

## Why

`web_agent.WebAgent.decide_next_action()` puts raw scraped page text (`s.observation[:400]`, see `web_agent.py:151`) into the prompt of the LLM that then **chooses the next tool and its arguments**. That is a privileged model reading attacker-controlled input, which is the architecture every documented 2025–26 browser-agent hijack shares. Tier-2 confirmation limits the damage, but the confirmation shows "run `click` on `<url>`?" without saying that the URL or selector was chosen because a web page said so. That makes approval a rubber stamp exactly when it matters. Guardrail classifiers fail more than 90% of the time under adaptive attack, so the fix has to be structural and enforced in deterministic code.

## What Changes

- **Two-model split.** A new *quarantined extraction* step reads each page's raw text. It is an LLM call with no tools and no ability to act, and it returns only a fixed, validated set of typed fields: whether the goal appears satisfied, one proposed next URL, one proposed click selector, and a short excerpt. The *planner* never receives raw page text again. It sees the goal, the URLs and selectors the user supplied, and the typed step results.
- **Deterministic constraints on anything a page can influence:**
  - A page-proposed URL must be http(s) and on a domain the user's original goal named or started from. Otherwise it is discarded and logged.
  - A page-proposed selector must be a single short CSS selector.
  - **Text typed into a page must come from the user's own goal text.** A page can never choose what gets typed.
- **Provenance in confirmations.** The Tier-2 prompt states whether each target (URL or selector) came *from your request* or *from page content*.
- **Invisible-Unicode sanitizer** (a shared helper). It strips zero-width and bidirectional-control characters from page text before the quarantined step or the summary sees it.
- The final summary still reads extracted excerpts. That is safe because its output is text shown to the user and never drives a tool call. Excerpts are sanitized and capped.

## Capabilities

### New Capabilities
- `web-agent`: safety requirements for goal-directed browsing, covering isolation of untrusted page content, the constraints on page-influenced actions, provenance in confirmations, and sanitization.

### Modified Capabilities
<!-- None: openspec/specs/ has no web-agent spec yet. -->

## Impact

- **Code:**
  - `web_agent.py`: new quarantined extraction step and new typed step fields; the planner prompt is rebuilt without observations; the constraint checks go in `execute_action`; `summarize` uses sanitized excerpts.
  - New `text_sanitizer.py`, a shared helper that B6 will reuse.
  - `orchestrator._interactive_confirm` needs no change, because provenance is carried in the message.
- **LLM calls:** +1 small call per successful browser step, for extraction. Steps stay capped at 4, so there are at most 4 extra calls per task. It runs on the `planning` task profile.
- **Tier gate / watchdog:** tiers are unchanged, and every browser action is still Tier 2 with injected confirmation. What changes is the *content* of the confirmation message, which now carries provenance, and that page-derived arguments are rejected *before* the gate.
- **Untrusted-input path:** this change exists to close one. After it, no LLM that selects actions reads raw page text.
- **Behavior change:** the agent will no longer follow links to domains the user did not mention. The user must name the domain in the request. This is intended.
- **Tests:** new tests for the sanitizer, the extraction schema validation, each constraint, provenance text, and a regression test with an injected instruction on the page. The existing 28 web-agent tests are updated wherever they assumed the planner sees observations.

## Non-goals

- Applying the sanitizer to `research_agent.py` and MCP tool descriptions. That is the rest of B6.
- Changing tier levels or the confirmation mechanism itself (`confirm_fn` injection and deny-by-default stay as they are).
- Making the Playwright tools stateful, or supporting multi-page sessions.
- Detecting prompt injection with a classifier model. Deliberately not relied on (see Why).
- Neutering links in research answers (ROADMAP B5).
