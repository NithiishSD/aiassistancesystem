# Proposal

Closes small open items on ROADMAP A4, C2 and E1, and fixes two defects found on the way.

## Why

Several roadmap boxes were still open although only a small piece was missing, and the
digest-on-request work exposed a defect in how a connected tool is chosen.

## What Changes

- **Tool selection (defect).** `_select_mcp_tool` matched keywords as substrings: "now" inside
  "snow" and "know", "today" in "today's headlines", "date" in "update" all ran the clock tool, and
  "count" in "country" ran the word counter. When the tool-picking model failed or named no tool,
  the first tool in the registry ran. Now the bundled-tool shortcuts match whole phrases only and
  only pick tools of the bundled server, and a request no tool was named for runs nothing.
- **Provider allowance (C2).** Groq reports its remaining requests and tokens on every successful
  reply (seen live: `x-ratelimit-remaining-requests`, `-remaining-tokens`, and their reset times).
  When a reply reports none left (or fewer than 1,000 tokens), the provider is skipped until the
  reported reset instead of being called to collect a 429. The other providers send no such
  headers on success, so nothing changes for them.
- **Which model answered (C2).** `llm_provider.last_answer_source()` and a REPL command
  `providers` (last answer's provider and model, plus the existing usage report).
- **Misroute promotion (A4).** `evals/promote_misroutes.py` lists corrected misroutes from
  `data/misroutes.jsonl` that are new to the golden set and, with `--apply`, appends them to the
  DEV slice only. Dry run by default; `--skip N` leaves rows out; prints a public-repository
  warning.
- **Routing prompt budget (E1).** Loading refuses a description over 700 characters or a
  capability list over 8,000 (today: 18 capabilities, 6,054 characters, longest 559).
- **Flaky tests.** The watchdog redaction test looked for "999", which can occur in a microsecond
  timestamp (already committed). The real-subprocess MCP tests get one discovery retry.
- `ROADMAP.md`: boxes for work that was already shipped are ticked, and the status section is
  brought up to date.

## Non-goals

- No proactive throttling for providers that do not report an allowance.
- No change to how a tool's arguments are extracted, or to tool tiers.
- No automatic promotion of misroutes, and none into the held-out slice.
- Capability descriptions are not shortened: they were tuned against the local-model eval.
- Not done here, and still open on the roadmap: the two A2 follow-ups (superseding on remember,
  "as of" questions), static-first prompts for the specialist agents (F5), owner-side data
  cleanups, and anything that needs the owner's voice.

## Capabilities

### New Capabilities

### Modified Capabilities
- `llm-provider-chain`: allowance-aware skipping; source of the last answer.
- `mcp-tool-trust`: a tool runs only when the request was matched to it.
- `routing-evaluation`: promotion of corrected misroutes.
- `capability-registry`: routing prompt budget.

## Impact

- Code: `llm_provider.py`, `orchestrator.py` (`_select_mcp_tool`, `providers` command),
  `capabilities/__init__.py`, new `evals/promote_misroutes.py`, tests.
- Tier gate: unchanged. Tool selection now fails closed (no tool) where it used to run an
  arbitrary one, so fewer calls reach the gate, never more.
- Watchdog: none.
- Untrusted-input path: provider response headers are parsed as numbers and durations only; a
  malformed value is ignored. A header can only make a provider be skipped for at most an hour.
