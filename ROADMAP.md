# Zedek — Roadmap

**Last revised:** 2026-09-29, twice:
1. An external research pass (agent architectures, security/compliance, production
   engineering) plus a live audit of the running system → Phases A–E.
2. A survey of 13 open-source assistants plus RAG-quality and efficiency research →
   Phase F, and changes to A3, A4, C2, D1, D2, E1. Full cited report:
   [`reports/Open source assistant improvements.md`](reports/Open%20source%20assistant%20improvements.md).

This file supersedes `buildingroadmap.txt`, which was a running chat log rather than a
plan. That file is kept for history; this one is the plan.

---

## Where the project actually stands

**Built and tested:** 28 modules, 488 tests passing. Phases 1–6 (environment, logging,
orchestrator + system agent, tier gate, long-term memory, session context) and Roadmap
Items 1–14 (MCP client + 7 servers, sandboxed execution, evaluator agent, task planner,
research agent, web agent, watchdog, security module, wake-word, academic tracking) are
complete.

**Verified against reality, not just mocks:** the research agent has run against the live
network, the web agent against real Chromium, the voice-print pipeline end-to-end through
the encrypted store. That pass found four bugs that mocked tests could never have caught,
including a Wikipedia URL-encoding bug that silently made *every* Wikipedia-backed answer
contentless.

**The honest weak spots**, in the order they hurt:

1. **Memory data quality was broken.** An audit of the live store found ~half of 135
   stored "facts" were junk: 22 were the LLM's own preamble line, 40 had unstripped
   markdown bullets, 19 were duplicates, 4 were null values like
   `User's educational institution: not stated`, and one was an unresolved template
   placeholder (`User's college: <new value>`). Semantic search returns its top-k
   regardless of quality, so this was being fed to the model as ground truth.
   *Status: fixed — see Phase A1.*
2. **No evaluation of routing accuracy.** 488 unit tests, zero measurement of the thing
   that has historically broken most often.
3. **A live prompt-injection hole in the web agent** (Phase B1). Verified in code.
4. **MCP tool descriptions are trusted** in two places (Phase B2). Verified in code.
5. **No observability**: no per-turn trace, no token/cost accounting, no idea which of
   five providers actually answered.
6. **Not proactive.** It only ever speaks when spoken to.

---

## Phase A — Correctness and trust

*Rationale: an assistant that confidently states wrong things is worse than one that
says less. Everything here is about making its outputs trustworthy.*

### A1. Memory hygiene ✅ DONE
- [x] `memory_hygiene.normalize_fact()` — rejects LLM preamble, null values, template
      placeholders, retractions-stored-as-text, headings; strips bullets and quotes.
- [x] Wired into `memory.store()` so the corruption cannot recur.
- [x] `clean_store()` sweeps existing rows — deletes junk and duplicates, and **repairs**
      salvageable ones in place rather than discarding real information.
- [x] `tests/test_memory_hygiene.py` — 43 tests, each rejection case drawn from real
      observed corruption.
- [x] **Cleanup applied to the live store 2026-09-29** with the owner's go-ahead: 36
      rows deleted, 37 repaired, and a re-run finds nothing left. Store is now 113
      personal + 22 academic items. Backup at `chroma_db.backup-20260929-163907/`.
- [ ] Optional: `python memory_hygiene.py --apply --drop-conversations` removes 36 raw
      conversation turns persisted before the tiered-memory redesign.

- [ ] **Reject hedged or speculative facts** (found while verifying F1). The store still holds LLM guesses such as
      "User's Current Studies: Data Structures (*presumably* a course at PSG…)" and "Operating System: *likely* a
      Linux-based system". The first outranked the real college fact on the live store. Add hedge words (presumably,
      likely, probably, possibly, maybe) to `normalize_fact()`'s rejections and sweep the existing rows.

### A2. Bi-temporal facts — the structural fix
The contradictory pair in the store (`"...exam next week"` + `"...exam next week: false"`)
is not a coding slip; there is no representation for *"this fact stopped being true."*
- [ ] Add `valid_at`, `invalid_at`, `superseded_by` to Chroma metadata.
- [ ] On correction, stamp `invalid_at` instead of deleting — history is preserved.
- [ ] Filter `invalid_at IS NULL` at retrieval.
- **Adopt the schema, not the dependency.** Zep/Graphiti want Neo4j and are tuned for
  millions of cold graphs; this is one user. ~50 lines here kills the whole bug class.

### A3. Relevance threshold everywhere ✅ DONE via F1
`research_agent` now filters memory at distance 1.0 (measured: real matches ~0.5–0.7,
junk ~1.4–2.0). `answer_general_question()` still injects top-3 unconditionally.
- [x] ~~Apply the same threshold there.~~ Not done as written: a fixed 1.0 cutoff would have dropped the correct college fact (1.06). General Q&A now uses F1's relevance gate instead.
- **Superseded long-term by F1.** A raw L2 cutoff shifts with query length and phrasing.
  The reranker score from F1 is the proper relevance gate and replaces the 1.0 cutoff.

### A4. Routing evaluation harness
Routing is the historical failure point and is completely unmeasured. ~16 intents and 27
MCP tools now compete for phrasings; measured industry data shows routing accuracy
collapses as tool count grows.
- [ ] `evals/routing_golden.csv` — 200+ labelled utterances, **min 20 per intent**,
      held-out test slice that never leaks into tuning.
- [ ] pytest + `sklearn.metrics.classification_report` / `confusion_matrix`. The
      **confusion matrix is the primary artifact** — aggregate accuracy hides exactly the
      class-level failures that matter.
- [ ] CI gate: fail if any class drops >2 points precision against baseline.
- [ ] Promote real misroutes into the set as they happen (~10 min/week).
- [ ] **"Must stay local" list.** Utterances the classifier must never send to the LLM
      fallback. Home Assistant shipped a regression (issue #139415) that sent every
      command to the LLM. A test list like this would have caught it.
- [ ] **Retrieval eval set: 30–50 real queries with the facts or sources they should
      retrieve.** This is step 0 for all of Phase F. Nobody has published a benchmark on
      a single user's few hundred short facts, so the reranker threshold, the embedding
      choice, and whether an NLI check pays off all have to be measured here.
- **No platform.** pytest + sklearn, both already present. DeepEval later *if* it hurts.

### A5. Confidence calibration
The known bug: a 0.403 score labelled "high confidence". Intent scores are documented to
be bimodal and miscalibrated, clustering at >0.95 or <0.05 and skipping the middle.
- [ ] Per-intent thresholds derived from a reliability diagram, replacing the single
      global 0.35 cutoff.

---

## Phase B — Security hardening

*Proportionality note: this is a single-user local tool with no inbound network surface
and mandatory confirmation on risky actions. B1 and B2 are real and worth doing properly.
B3–B5 are hygiene. None of it is an emergency.*

### B1. Quarantine web-page content from the planner ⚠️ highest real risk
`web_agent.decide_next_action()` pastes raw scraped page text (`observation[:400]`) into
the prompt of the LLM that then **chooses the next tool and its arguments**. That is a
privileged model consuming attacker-controlled input — the architecture every 2026
browser-agent incident shares.

What already protects it, and it is substantial: every action needs typed Tier-2
confirmation via an *injected* `confirm_fn` that defaults to deny, so the agent
structurally cannot self-approve; allowlist and URL validation run before the gate; a
denied action ends the loop. The residual gap is that "run `click` on `<url>`?" invites
rubber-stamping when the URL itself was chosen by injected text.
- [ ] **Dual-LLM pattern**: a separate, tool-less call reads page content and returns
      typed fields (`{found: bool, next_url: str|null}`); the planner never sees raw text.
- [ ] Validate `next_url` against a domain allowlist derived from the user's *original*
      goal.
- [ ] Show in the confirmation prompt when a target came from page content rather than
      from the user.
- **Do not rely on a detector model** — guardrail classifiers fail >90% under adaptive
  pressure. Enforcement belongs in deterministic code.

### B2. Treat MCP tool descriptions as untrusted
Verified in code: `orchestrator._select_mcp_tool()` interpolates every tool's
`description` into an LLM prompt, and `tier_gate.classify()` derives risk escalation from
that same description — so a server that describes a writing tool as "reads data" stays
Tier 1. **That lane fails open.**
- [ ] Strip imperative/instruction-like content from descriptions before they enter any prompt.
- [ ] Make `default_tier` in `mcp_servers.json` mandatory per server and treat it as the
      floor, rather than inferring risk from prose.
- [ ] Hash/version-lock servers; detect metadata drift ("rug pull").

### B3. Keep API keys out of the sandbox
`.env` values live in the ambient process environment, so code executed in-process could
read them. The sandbox isolates the agent but not the secret.
- [ ] Scrub provider keys from the sandbox child's environment.

### B4. Egress allowlist for network-enabled sandbox runs
- [ ] When `allow_network=True`, restrict to an FQDN allowlist rather than the open internet.

### B5. Neuter non-source URLs in research output
Lower risk than B1 (the output is text to the user, with no subsequent tool call), but a
crafted page could still induce a malicious link.
- [ ] Strip or mark links in synthesized answers that don't appear in the source list.

### B6. Invisible-Unicode sanitizer and LLM-declared risk
Block's red team got code execution on employee laptops through its own Goose agent.
The payload was **zero-width Unicode instructions hidden in calendar invites and shared
workflow files**. The user saw nothing and the model read everything.
- [ ] A ~20-line sanitizer that strips zero-width and bidi-control characters from all
      untrusted text (web pages, fetched URLs, RAG sources, tool descriptions) before any
      LLM sees it. Goes in `web_agent.py`, `research_agent.py`, and the MCP registration path.
- [ ] OpenHands-style risk label: the LLM states LOW/MEDIUM/HIGH inside the tool call,
      at no extra inference cost. The effective tier is `max(rule tier, LLM risk)`, so the
      model can raise a tier but never lower it. That fits the tier gate's
      existing "lanes only raise" design.
- [ ] Reject-with-feedback: a denied action goes back to the agent with the user's reason,
      instead of ending the loop with no explanation.

---

## Phase C — Observability and cost

### C1. Per-turn tracing
- [ ] A `trace_id` per `handle()` call, threaded through classifier → planner → provider → MCP.
- [ ] Rename existing log fields to **OpenTelemetry GenAI conventions** (`gen_ai.request.model`,
      `gen_ai.usage.input_tokens`, `gen_ai.operation.name`). Nearly free now, and makes
      later Langfuse/Phoenix adoption a config change rather than a migration.
- **Don't self-host Langfuse yet** — that means ClickHouse + Docker on a laptop.

### C2. Provider accounting
Five free-tier providers with silent fallthrough: when Gemini hits quota there is no way
to tell whether an answer came from Gemini or the local 8B. That's an invisible accuracy cliff.
- [ ] Per-provider call/token counters; surface `last_provider` in responses.
- [ ] Cooldown after 429 so an exhausted provider isn't hammered.
- [ ] Keep the fallback chain **bounded** — unbounded chains cause cascading retry storms
      when every provider 429s at once.
- [ ] **Move OpenRouter off the head of `process_reasoning`.** OpenRouter's `:free` tier
      allows **50 requests/day** (1,000 only after buying $10 of credit), which is verified
      against its docs. Yet `TASK_PROVIDERS["process_reasoning"]` lists it first, so the
      most quota-scarce provider gets hit first. Order chains by quota headroom, not habit.
- [ ] Read rate-limit headers and skip providers that are expected to be exhausted,
      instead of calling them to find out.
- **Don't swap in LiteLLM.** The existing chain works and is tested; port the ideas.

---

## Phase D — Making it feel like an assistant

### D1. Proactivity — the biggest perceived-intelligence win
Today Zedek only reacts. The academic tracker and memory already hold everything needed for:
> "You haven't touched graphs in 12 days and you're at 40% there. Your DS exam is next
> week — want three problems?"
- [ ] Daily digest job surfacing stale topics, weak areas, and upcoming commitments.
- Scheduled prompts are now standard across the field: Open WebUI Automations, LibreChat
  Scheduled Chats, Khoj automations, AnythingLLM Scheduled Jobs. Implement with
  APScheduler + SQLite (1–2 days). The digest itself is a stored prompt run on a cron,
  so it goes through the normal pipeline and tier gate.

### D2. Streaming and progress feedback
Fully blocking today; a 12-second cloud call is indistinguishable from a hang. Accuracy
over speed is the right call, but *perceived* responsiveness is a separate axis and costs
no accuracy.
- [ ] Token streaming (`stream=True`) + a thinking indicator.
- [ ] **In the voice path, split the stream at sentence boundaries and send each sentence
      to TTS as it arrives.** Home Assistant measured time-to-speech falling from
      **6.62 s → 0.51 s (cloud TTS) and 5.31 s → 0.56 s (local Piper)** (project-reported).
      This is the single biggest perceived-latency win found, and it uses no extra quota.
- Hedged requests (firing a backup provider after ~p95 latency) belong on voice turns
  only, and only once calls are non-blocking. With blocking `requests`, an abandoned call
  still burns quota.

### D3. Local model refresh
- [ ] `llama3.1:8b` → **Qwen3 8B**: same footprint, materially better instruction-following
      and JSON reliability — which matters because the local fallback does structured
      extraction. Keep Ollama (llama.cpp is only 10–20% faster; vLLM targets 10–1000
      concurrent users). ~30 minutes, then re-run the evals from A4.

### D4. Packaging
- [ ] `systemd --user` unit with `Restart=on-failure` so it survives the shell and starts on boot.
- [ ] **Chainlit** for a UI — a framework driven from your own Python, which fits because
      the orchestrator is the product. (Open WebUI is a finished app wrapped around a
      model; wrong shape here.)

### D5. Abstract the confirmation channel
`input()` is called directly, which blocks and can't work from voice or a UI.
- [ ] A confirmation interface so the same gated flows work across terminal, UI, and voice.

---

## Phase E — Structure

### E1. One declarative capability registry
Adding a capability today means editing four files in sync — `classifier.py`,
`classifier_tools.py`, the `execute()` dispatch, and `tier_gate`. That desynced three
times in a single session; a consistency test caught each one. A single `CAPABILITIES`
definition that all four derive from removes the entire bug class.

**Concrete design, borrowed from OpenClaw and Open Interpreter.** Both now define a
capability as a folder containing a `SKILL.md` with a small metadata header:
- [ ] One folder per capability: `capability.yaml` (name, description, router
      utterances, tier, input schema, required binaries/env vars) plus `handler.py`.
- [ ] One loader generates the `classifier.py` routes, the `classifier_tools.py`
      entries, the `tier_gate.py` table, the dispatch, and the help text.
- [ ] Skip a capability at load time when its requirements are missing. That replaces
      today's scattered `available()` probes.
- [ ] Only name + description go into any LLM prompt, within a fixed budget. OpenClaw
      spends about 24 tokens per skill and drops descriptions first when over budget.
- [ ] The `input schema` field also feeds F2's structured-output adapter.
- Effort: 3–5 days to migrate everything. **Do not copy OpenClaw's public skill
  registry.** It had 1,184+ malicious skills. Capabilities stay local, reviewed code.

### E2. Typed state between pipeline steps
Replace the free-form `dict` flowing through `route_request → execute` with a dataclass.
This is the one genuinely useful idea to borrow from LangGraph — as a pattern, not a dependency.

---

## Phase F — Answer quality and efficiency

*Source: survey of 13 open-source assistants plus RAG and efficiency research,
2026-09-29 ([full report](reports/Open%20source%20assistant%20improvements.md)). The
headline finding: **at Zedek's scale, neither the model nor the framework is the
bottleneck. The weak point is what the LLM gets to see.** That means three dense facts
behind a hand-tuned cutoff, or six research pages cut off at 4,000 characters. On top of
that, requests are lost to malformed JSON and exhausted providers. All of the fixes below
run on CPU.*

**Step 0 is the retrieval eval set in A4.** Almost every gain number below was measured
on 10k+ document corpora, reported by vendors, or measured on GPUs. Keep a change only if
it improves Zedek's own eval set.

### F1. Cross-encoder reranker ✅ DONE 2026-09-29
*Shipped as OpenSpec change `2026-09-29-add-memory-reranker`; main spec in `openspec/specs/memory-retrieval/`.*
*Measured: median 64 ms for 20 candidates on CPU. On the labelled fixture: recall 15/15 vs 8/15 for the old gate, and 0/5 general-question leaks.
Needed one addition beyond the plan: first-person queries are rewritten to third person before scoring, because without it no single threshold met the spec.*

- [ ] Retrieve 15–20 candidates instead of 3, rerank with a small cross-encoder
      (FlashRank `ms-marco-MiniLM-L-12-v2`, ~4 MB, or `bge-reranker-base`), keep the top 3.
- [ ] Replace the L2 ≤ 1.0 cutoff with a **reranker-score threshold** tuned on the eval set.
- Evidence: an independent Jan-2026 CPU benchmark measured **+31 ms mean, ~0.3% of
  end-to-end time**. Anthropic (vendor) reports reranking on top of hybrid search cut top-20
  retrieval failures by 67%. A Sept-2026 academic study used bge-reranker-base as its
  "strong baseline" default.
- Touches: `memory.retrieve()`, the memory gate in `research_agent.py`, the fact lookups
  in `orchestrator.py`. Effort: 0.5–1 day.

### F2. Schema-constrained JSON on every provider
Today `llm_provider.py` only asks for "some JSON" (`json_object`, `responseMimeType`,
Ollama `format: "json"`): valid syntax, no schema.
- [ ] A Pydantic model per structured task (arg extraction, fact canonicalization,
      query planning, intent fallback).
- [ ] A hand-written `schema_for(provider, Model)` adapter for each provider's native mode:
      Gemini JSON-schema output; Groq `strict: true` (GPT-OSS models only); NVIDIA NIM
      `guided_json` via `extra_body`; Ollama `format=<schema>`.
- [ ] Validate with Pydantic, **re-ask once** with the validation error, then move to the
      next provider.
- [ ] Intent fallback uses a single-field enum schema. That's the cheapest and most
      reliable form, because provider schema coverage drops sharply as schemas grow.
- Evidence (independent): 0.6B–4B models go from 7–21% schema-invalid output to 0%.
  JSONSchemaBench found constrained decoding also *raised* accuracy by up to ~4 points.
- Touches: `generate_chat()`, `_openai_compatible`, `_gemini`, `_local`, and the classifier's
  LLM fallback. Effort: 1–1.5 days. **No Instructor/Outlines** (see rejections).

### F3. BM25 + reciprocal-rank-fusion hybrid search
- [ ] An in-memory `bm25s` index beside Chroma, merged with dense results by RRF (~10
      lines). This fixes exact-token misses such as course codes, roll numbers, and names.
- **Local ChromaDB cannot do this natively.** Its sparse/BM25 support is Cloud-only.
  Verified: local raises "Sparse vector indexing is not enabled in local" (chroma #6185).
- RRF discards absolute scores, which is why F1's reranker threshold is required.
- Evidence (independent, large corpus): up to +8.1pp Recall@5 over either method alone.
  bm25s is 100–500× faster than rank_bm25. Touches `memory.store()` / `retrieve()`. 0.5–1 day.

### F4. Chunk research pages instead of truncating them
`research_agent.gather()` keeps each source's first 4,000 characters, so anything past
that is lost.
- [ ] Recursive ~250-token chunks (MiniLM truncates at 256 word-pieces), each prefixed
      with "title — section". Rerank all chunks against the question and send only the
      best to `synthesize()`.
- Evidence: plain chunking matches semantic chunking (NAACL 2025), and the free title
  prefix did well in a 2026 comparison of eight methods. Better sources, smaller prompts. 1 day.

### F5. Static-first prompts + exact-match cache
- [ ] Put the system prompt, schema, and few-shot examples **byte-identical and first**,
      then timestamps, memory, and the user query **last**, so provider prefix caches can
      hit. Cerebras and Groq don't count cached tokens against rate limits. Gemini's implicit
      cache needs 2,048–4,096-token prefixes, so short extraction prompts will mostly miss it.
- [ ] SQLite exact-match hash cache for deterministic sub-tasks only, such as fact
      canonicalization and argument extraction. **Never** cache anything touching memory,
      time, calendar, or the web. Effort: 0.5 day.

### F6. Core memory blocks (Letta / OpenClaw pattern)
- [ ] A `user_profile` block (1–2k characters: name, college, courses, target companies)
      kept **always in the prompt**, stored as git-tracked markdown so every change is
      diffable and revertible.
- The bi-temporal store (A2) stays the system of record. The block removes retrieval
  misses for the most-used facts. Anthropic advises that knowledge under 200k tokens can
  go straight into the prompt, and Zedek's facts are far below that. 1–2 days. *Benefit
  over pure RAG is unmeasured, so check it on the eval set.*

### F7. Embedding model upgrade (decide with the eval set)
- [ ] Memory: `snowflake-arctic-embed-s` (51.98 MTEB retrieval nDCG@10) or
      `bge-small-en-v1.5` (51.68). Both are 384-dim like MiniLM, so storage size doesn't
      change. Requires a re-embed and a query prefix. Scores are self-reported on model cards.
- [ ] Router: `model2vec` `potion-base-8M` is up to 500× faster on CPU at ~92% of MiniLM's
      MTEB average. It's a candidate for `classifier.py` only, never for memory. Accept it
      only if routing accuracy holds in A4.

### F8. Condenser + stuck detection
- [ ] At ~70% of the smallest provider's context window, summarize the middle of the
      session locally, keeping the start and recent tail. Before that, run OpenClaw's
      silent "save anything durable" turn. OpenHands (project-reported) halved per-turn cost
      with no loss in solve rate (54% vs 53%).
- [ ] Watchdog flags repeated identical tool calls or error→retry cycles and stops to ask
      the user. Effort: 2–3 days.

### F9. Two-tier voice recognition
- [ ] A second Vosk recognizer restricted to a **grammar generated from the router's
      utterances** for commands (HA's Speech-to-Phrase: ~150 ms), keeping the free-form
      recognizer for Q&A. Pass recently handled local turns to the LLM so follow-ups work.
      Effort: 1.5–2.5 days. HA itself says the closed-vocabulary approach is "not for LLMs"
      open Q&A, hence two tiers.

### F10. Local citation check (measure before adopting)
- [ ] A small NLI model (MiniCheck, ~0.4B) checks each `[S#]` sentence against its source
      locally, with no LLM call. Motivation: up to 57% of RAG citations are
      "post-rationalized", meaning the model answers from memory and then cites something
      that looks like a match. **CPU latency is unpublished, so benchmark it first.**

### Process decision: OpenSpec ✅ ADOPTED 2026-09-29
Spec-driven change management for AI coding tools (MIT, ~70k stars; works with Claude
Code, Continue, Gemini/Antigravity, Cursor). Changes go through
propose → review → apply → archive. Fits the stated rule that every change is reviewed
before it happens, and gives every AI tool used on this repo one shared plan format.
- Adopt it **as a replacement, not an addition**. ROADMAP.md stays as the high-level
  plan. `task.md` is retired in favor of `openspec/changes/`. `zedek_context.md` shrinks
  over time as current truth moves into `openspec/specs/` and history into
  `openspec/archive/`.
- No backfilling specs for all 28 modules. A spec is written only when a change touches
  that capability.
- Turn off its default-on telemetry. Requires Node ≥ 20.19 (installed: 20.20.2).
- Installed (v1.13.2) and initialized for Claude Code, Continue, and Antigravity; project
  context and rules are set in `openspec/config.yaml`. Telemetry confirmed off.
- First two changes, both proposed and validated, **awaiting review**:
  `openspec/changes/add-memory-reranker` (F1, absorbs A3) and
  `openspec/changes/quarantine-web-observations` (B1, plus the shared sanitizer from B6).
- **Why A3 was folded into F1:** after the cleanup, the correct answer to "what college
  do I study at" comes back at L2 distance **1.06**. A fixed 1.0 cutoff everywhere would
  have dropped the right fact, which is direct evidence that distance thresholds are
  the wrong gate.

---

## Deliberately rejected (and why)

Recording these so they don't get relitigated:

- **Agent frameworks (LangChain / LangGraph / CrewAI).** LangGraph is production-proven,
  but its value is durable execution — checkpointing, pausing for days, resuming. Zedek is
  a single-user, interactive, sub-minute-task assistant with no long-running-job problem.
  Adopting it means paying a 30–80ms-per-call abstraction tax for a feature it never uses.
  The 2026 industry trend is *away* from frameworks for this profile.
- **Zep / Graphiti as a dependency.** Take the bi-temporal schema (A2); skip the Neo4j.
- **LiteLLM.** Port the routing ideas into the existing tested chain (C2).
- **Evaluation SaaS (Braintrust / LangSmith / Langfuse cloud).** Not until A4 and C1 exist
  and actually hurt.
- **Replacing the hybrid router.** Semantic-router-then-LLM-fallback *is* the current
  recommended pattern: ~65× cheaper and 5,000ms → 100ms versus pure LLM routing, with the
  LLM layer covering exactly the out-of-distribution weakness embeddings have.
- **Tier 3 execution.** Stays off. The framework is built and tested for when that changes.

*Added from the Phase F research:*
- **LLM-generated contextual retrieval (Anthropic-style).** Built for long chunked
  documents. Zedek's facts are already self-contained, and an independent 2026 study found
  it slow, costly, and not consistently better. The free title prefix (F4) gets most of it.
- **Semantic or late chunking.** No consistent gain over plain token chunking; late
  chunking did poorly at scale.
- **HyDE / multi-query on personal memory.** Inventing "hypothetical" user facts pulls in
  wrong memories. A 5-method ensemble *hurt* AmbigNQ by 2.49pp, and each method costs a
  call. Only rewrite queries in the research agent, and only when confidence is low.
- **Self-RAG, multi-round reflection, full CRAG.** Self-RAG needs a fine-tuned generator,
  which isn't possible with cloud APIs. CRAG's independently reproduced gain is only
  +0.4–3.0pp, and its evaluator mostly keys on named-entity overlap, which the F1 reranker
  already captures.
- **Semantic response cache (GPTCache-style).** 5–15% hit rate on conversational
  traffic, and a single user's answers depend on personal state and time, so it would
  serve confidently stale answers. Exact-match caching only (F5).
- **Always-parallel fan-out or speculative generation.** Doubles quota burn on budgets as
  small as 50 requests/day.
- **RouteLLM / learned routers.** Built for cost gaps between paid models. Ordering by
  quota plus escalating on validation failure (C2, F2) captures the value here.
- **Instructor / Outlines.** Instructor brings SDK clients, which goes against the
  no-LiteLLM decision. Ollama's `format` already does grammar-constrained decoding
  locally, so Outlines adds nothing.
- **Large rerankers first (bge-reranker-v2-m3, mxbai-v2).** Only GPU latency is
  published. Start small (F1) and move up only if the eval set shows a gap.
- **OpenClaw's skill registry, network gateway, and messaging bridges.** 1,184+ malicious
  skills found on its registry; CVE-2026-25253 gave remote code execution through its
  localhost WebSocket gateway, which had no Origin check. When Chainlit (D4) opens a port,
  bind to 127.0.0.1 and check Origin.
- **Enterprise/multi-user features.** RBAC/SSO, many vector-DB backends, visual flow
  builders, OVOS MessageBus/HiveMind, the Letta server/Postgres stack. Built for many
  users on many machines; Zedek is one user, one process.

---

## Suggested order

1. **A1 cleanup run** — one command, immediately improves every answer.
2. **A3** — three lines, stopgap until F1.
3. **B1 + B6 sanitizer** — the one live architectural hole, plus its cheapest hardening.
4. **A4 including the retrieval eval set**. Every item in Phase F is measured against it.
5. **C2 quota fixes**. Move OpenRouter off the head of `process_reasoning`. It takes minutes.
6. **F1 → F2 → F3 → F4**. About 4–5 working days total. These are the biggest direct
   answer-quality gains found.
7. **D2 streaming + D3 Qwen3 + F5 prompt order**. Speed and quota.
8. **C1 tracing**.
9. **E1 capability manifests**. Do it before the next big capability push, not after.
10. **F6–F10, D1, D4**. Structural work, scheduled once the first batch shows measured gains.

If OpenSpec is adopted, each numbered step above becomes one `openspec/changes/` proposal.
