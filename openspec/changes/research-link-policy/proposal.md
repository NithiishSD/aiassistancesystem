# Proposal

Implements **ROADMAP B5** (neuter non-source URLs in research output).

## Why

The research synthesizer reads fetched web pages. A crafted page can include a link and instructions to surface it, and the answer is shown to the user as Zedek's own words. The model can also invent plausible-looking links. Neither is caught today, and when synthesis fails the raw source text (page links included) is printed as-is.

## What Changes

- **Allowed links:** URLs the user typed, the URL of a page the user asked to fetch, and URLs inside sources whose content is not arbitrary web text: the user's memory, Wikipedia, arXiv and Semantic Scholar results.
- **Everything else is defanged**, not silently dropped: `https://evil.example/x` becomes `[unverified link removed: evil[.]example]` (not clickable, host still visible), and the report notes how many links were removed. Logged with host names only.
- Matching is normalized (scheme, `www.`, trailing slash and punctuation), so a model's harmless rewrite of an allowed link survives, while lookalike hosts (`example.com.evil.io`), userinfo tricks (`example.com@evil.io`) and unsourced paths on an allowed host are defanged.
- Applied to the final answer in `ResearchAgent.research`, which also covers the raw-source fallback.

## Capabilities

### Modified Capabilities
- `research-sources`: which links a research answer may contain.

## Impact

- **Code:** `research_agent.py`.
- **Tier gate:** none; research output is text to the user.

## Non-goals

- Web-agent summaries (the web agent quarantines page content from its planner under B1; its user-facing text can be covered the same way if needed).
