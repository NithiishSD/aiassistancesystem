# Zedek — Roadmap

**Last revised:** 2026-09-29, after an external research pass (agent architectures,
security/compliance, production engineering) and a live audit of the running system.

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
- [ ] **Run the cleanup against the live store** (36 rows to delete, 37 to repair).
      Deliberately not done automatically: deleting the user's stored memories needs
      their say-so.

### A2. Bi-temporal facts — the structural fix
The contradictory pair in the store (`"...exam next week"` + `"...exam next week: false"`)
is not a coding slip; there is no representation for *"this fact stopped being true."*
- [ ] Add `valid_at`, `invalid_at`, `superseded_by` to Chroma metadata.
- [ ] On correction, stamp `invalid_at` instead of deleting — history is preserved.
- [ ] Filter `invalid_at IS NULL` at retrieval.
- **Adopt the schema, not the dependency.** Zep/Graphiti want Neo4j and are tuned for
  millions of cold graphs; this is one user. ~50 lines here kills the whole bug class.

### A3. Relevance threshold everywhere
`research_agent` now filters memory at distance 1.0 (measured: real matches ~0.5–0.7,
junk ~1.4–2.0). `answer_general_question()` still injects top-3 unconditionally.
- [ ] Apply the same threshold there. ~3 lines.

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
- **Don't swap in LiteLLM.** The existing chain works and is tested; port the ideas.

---

## Phase D — Making it feel like an assistant

### D1. Proactivity — the biggest perceived-intelligence win
Today Zedek only reacts. The academic tracker and memory already hold everything needed for:
> "You haven't touched graphs in 12 days and you're at 40% there. Your DS exam is next
> week — want three problems?"
- [ ] Daily digest job surfacing stale topics, weak areas, and upcoming commitments.

### D2. Streaming and progress feedback
Fully blocking today; a 12-second cloud call is indistinguishable from a hang. Accuracy
over speed is the right call, but *perceived* responsiveness is a separate axis and costs
no accuracy.
- [ ] Token streaming (`stream=True`) + a thinking indicator.

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

### E2. Typed state between pipeline steps
Replace the free-form `dict` flowing through `route_request → execute` with a dataclass.
This is the one genuinely useful idea to borrow from LangGraph — as a pattern, not a dependency.

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

---

## Suggested order

1. **A1 cleanup run** — one command, immediately improves every answer.
2. **A3** — three lines, same class of win.
3. **B1** — the one live architectural hole.
4. **A4** — protects the historical weak point before adding more capabilities.
5. **C1 + C2** — half a day each, unlocks everything downstream.
6. **D3** — 30 minutes, measurable gain.
7. **D1/D2** — the features that change how it *feels*.
8. **E1** — do before the next big capability push, not after.
