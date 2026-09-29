# Design

## Context

See proposal.md for why. The relevant state of `web_agent.py` today:

- `Step` holds `observation`, the raw page text truncated to `MAX_OBSERVATION_CHARS = 2500`.
- `decide_next_action(goal, history, start_url)` builds `history_block` including `s.observation[:400]` and asks an LLM (`task="planning"`, JSON mode) for `{tool, args, done, reason}`.
- `execute_action(alias, args, goal, index, confirm_fn)` checks the tool allowlist and `is_safe_url()`, calls `tier_gate.gate()`, calls `confirm_fn(message)` on `confirm`, then `mcp_client.call_mcp_tool()`.
- `browse()` runs the loop, with at most 4 steps. `summarize()` feeds the observations of successful steps to an LLM that produces user-facing text only.
- The Playwright tools are stateless: every call carries a full URL.

## Goals / Non-Goals

**Goals:**
- Structural isolation: the model that picks actions never sees page text.
- Every page-influenced argument (URL, selector, typed text) is checked by deterministic code before `gate()`.
- The user can see where each target came from at the moment they confirm.

**Non-Goals:**
- Perfect semantic safety of the excerpt shown in the summary. The summary performs no actions, so its risk is residual (ROADMAP B5).
- A general untrusted-text framework. Only the sanitizer helper is shared now.

## Decisions

### D1. Dual-LLM split, with a quarantined extractor per successful step
- After a step succeeds, `extract(goal, sanitized_page_text) -> Extraction | None`. This is one LLM call with no tools, on `task="planning"` in JSON mode.
- `Extraction` is a dataclass: `goal_satisfied: bool`, `next_url: str | None`, `click_selector: str | None`, `excerpt: str`, where excerpt is ≤ 500 characters.
- Validation is plain Python (no new dependency): required keys, types, and length caps. Any failure returns `None`, which is treated as "nothing extracted".
- **Why:** this is the dual-LLM pattern (Willison, 2025). Injected text can at most shape *which* typed field values come back, and each of those fields is constrained by D3–D5.
- **Alternatives:**
  - A detector model. Rejected, because detectors fail more than 90% of the time under adaptive attack.
  - Hiding all text including the excerpt from every model. Rejected: the summary still needs content to answer the goal, and it cannot act.

### D2. The planner sees typed history only
- `Step` gains `extraction: Extraction | None` and `provenance: dict[str, str]`, which maps a target key (`url`, `selector`) to `"user"` or `"page"`.
- `history_block` becomes `Step i: {tool} url={url} [{status}] goal_satisfied={bool} next_url_offered={bool} click_offered={bool}`. There is **no observation and no excerpt**.
- The planner chooses among targets it is given as a structured list, each tagged with an ID and its provenance: `start_url`/goal URLs (user), `next_url` (page), `click_selector` (page). It may also finish.
- The planner cannot invent a URL: `execute_action` rejects any URL that is not one of the offered targets.

### D3. Domain allowlist derived from the goal
- `allowed_domains = {registrable host of every URL in the goal} ∪ {host of start_url} ∪ {bare domains mentioned in the goal, matched by a hostname regex}`.
- A page-proposed URL is kept only if `is_safe_url(url)` holds and its host equals, or is a subdomain of, an allowed domain. Otherwise it is dropped and `web_page_url_rejected` is logged.
- Suffix matching is done on the host with a leading dot (`host == d or host.endswith("." + d)`), so `evil-example.com` does not match `example.com`.
- **Alternative:** warn but allow off-domain links. Rejected, because the allowlist is what bounds the blast radius. The user can name a domain explicitly to widen it.

### D4. Selector validation
- A page-proposed `click_selector` must be ≤ 200 characters, contain no newline, and parse as a single CSS selector (a conservative regex over the allowed selector characters). Anything else is dropped.
- Clicks still require Tier-2 confirmation, and the confirmation labels the selector as page-derived.

### D5. Typed text must be a substring of the goal
- For `type`, `args["text"]` must satisfy `text.strip()` being non-empty and `text.strip()` appearing verbatim in the user's goal. This is checked in `execute_action` before `gate()`, and a failure returns a `rejected` step.
- **Why:** typing is how data leaves, via forms and search boxes. With this rule, page content can never supply the payload.

### D6. Provenance in the confirmation message
- `execute_action` builds the message: `"{tool} → {url} [from: your request | page content]"`, plus the selector line if present, plus a warning line whenever any target is page-derived.
- The gate's own message is kept and prefixed. `confirm_fn` injection and `deny_all` are unchanged.

### D7. Shared `text_sanitizer.py`
- `strip_invisible(text) -> str` removes U+200B–U+200D, U+2060, U+FEFF, U+202A–U+202E, and U+2066–U+2069, and normalizes to NFKC.
- It is applied to page text before `extract()` and before `summarize()`. B6 will reuse it for `research_agent` and MCP descriptions.

## Risks / Trade-offs

- [One extra LLM call per step, 4 max per task, using free-tier quota] → Runs on the `planning` profile, which leads with Groq's higher quota. The step cap already bounds it.
- [A malicious page can still steer the extractor to propose a same-domain URL or an element to click] → Both are constrained to the user's own domains, both are labelled "from page content", and both still need explicit confirmation. This residual risk is accepted and documented.
- [Legitimate cross-domain flows (e.g. docs hosted on a CDN domain) stop working] → The user names the domain in the goal. The rejection reason in the report says exactly that.
- [The extractor fails or returns junk, so the agent makes less progress] → A failed extraction degrades to "nothing offered", and the planner can finish with a clear note. This fails safe.
- [The excerpt reaching the summary could still carry injected claims into the answer text] → The output is user-facing text with no tool call. B5 will neuter URLs in it.

## Migration Plan

- A code-only change with no stored data. Existing web-agent tests that assert the old prompt shape are updated in the same change.
- Rollback: revert the commit. No other module depends on the new step fields.
