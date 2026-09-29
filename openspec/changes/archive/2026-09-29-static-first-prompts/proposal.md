# Proposal

Implements **ROADMAP F5** (static-first prompts + exact-match cache).

## Why

- **Prompt layout defeats provider prefix caches.** Every orchestrator prompt interpolates the user's text, retrieved facts, or the last assistant question into the *middle* of the instructions, then sends the whole thing as one message. No two requests share a prefix, so Groq/Cerebras prompt caching (cached tokens don't count against rate limits) and Gemini implicit caching can never hit. `answer_general_question` is worst: the facts block sits inside the system prompt, so even the session history behind it is a fresh prefix every turn.
- **Deterministic sub-tasks pay full price every time.** Fact canonicalization and academic-intent extraction map the same sentence to the same structured result, yet a repeated sentence costs a full provider call (and a re-ask on a bad reply) against free-tier budgets as small as 50 requests/day.

## What Changes

- **Static-first layout.** Each orchestrator prompt is split into a module-level constant system message (instructions, schema description, few-shot examples: byte-identical across calls) and a final user message carrying everything that varies (user text, facts, candidates, timestamps). For general Q&A the order becomes: static system → session history (append-only, so it stays a stable prefix turn to turn) → one user message holding the retrieved facts, the turn note, and the question.
- **Exact-match cache** (`llm_cache.py`, stdlib `sqlite3`). `generate_structured(..., cache=True)` looks up a SHA-256 key over (cache version, task, schema name + JSON schema, messages). A hit returns the stored validated data with `source="cache"` and zero token usage, no provider call. Opt-in only; enabled for `canonicalize_fact` and `_extract_academic_intent`.
  - Only validated cloud results are stored. A local-fallback result is not cached, so a weaker 8B answer never gets locked in.
  - Entries expire after 30 days. `LLM_CACHE=off` disables it; `ZEDEK_LLM_CACHE_PATH` overrides the file (`data/llm_cache.sqlite3`, gitignored).
  - Any cache error (corrupt file, locked DB) is logged and treated as a miss — the cache can never fail a request.

## Capabilities

### New Capabilities
- `prompt-caching`: prompt layout rules and the exact-match structured-output cache.

## Impact

- **Code:** `orchestrator.py` (prompt construction only), `llm_provider.py` (`cache` flag), new `llm_cache.py`, `.gitignore`.
- **Behavior:** same instructions and content, reordered; answers should be unchanged. Repeated fact statements / practice logs cost no provider call.
- **Tier gate / watchdog / untrusted input:** none. Cached values are the same validated Pydantic data the provider would have returned; nothing touching memory retrieval, time, calendar, or the web is cacheable (the correction prompt, general Q&A, and routing are never cached).
- **Dependencies:** none (stdlib `sqlite3`, `hashlib`).

## Non-goals

- Semantic / similarity caching (rejected in ROADMAP).
- Explicit provider cache APIs (Gemini `cachedContents`): prompts here are below its 2,048–4,096-token minimum.
- Restructuring prompts in the specialist agents (research, web, coding, planner). They get the same treatment when next touched.
- Caching routing decisions: a wrong route would become sticky and feed the dynamic-utterance learner.
