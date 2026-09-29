# Zedek — Tech Stack

**Last revised:** 2026-09-29.

Every entry says *why* it's here and what it was chosen over. The point is that a future
decision can be re-evaluated against its original reasoning instead of being re-argued
from scratch.

---

## Runtime

| Layer | Choice | Why this, not the alternative |
|---|---|---|
| Language | Python 3.12 | Ecosystem for ML/agents; nothing here needs more speed than it gives. |
| Environment | Conda (`zedek-env`) | Pinned interpreter + native libs (portaudio, ffmpeg deps) that pip alone handles badly. |
| OS target | Ubuntu (Linux) | Sandboxing relies on Linux namespaces (`bwrap`); the system agent is Linux-specific by design. |

## Models and providers

| Role | Choice | Notes |
|---|---|---|
| Cloud chain | Gemini → Groq → NVIDIA NIM → OpenRouter → Cerebras | Free-tier first, per-task ordering via `TASK_PROVIDERS`. Bounded on purpose — unbounded fallback chains cause cascading retry storms when every provider rate-limits at once. |
| Task profiles | `coding`, `evaluation`, `general_qa`, `fact_handling`, `process_reasoning`, `planning`, `research` | Model diversity is a *correctness* feature: the evaluator deliberately runs on a different model family than the code generator, so a model doesn't mark its own homework. |
| Local fallback | Ollama + `llama3.1:8b` | **Scheduled for replacement by Qwen3 8B** (Roadmap D3) — same footprint, better instruction-following and JSON reliability, which matters because the local path does structured extraction. |
| Local serving | Ollama | Keep. llama.cpp is only 10–20% faster for interactive chat; vLLM targets 10–1000 concurrent users on server GPUs — irrelevant for one person. |
| Embeddings | `all-MiniLM-L6-v2` (local, in `models/`) | Shared by the semantic router and Chroma so there's one embedding space, no re-download. |
| Speech-to-text | Vosk `small-en-us-0.15` (offline) | **Deliberately local.** An always-on microphone streaming a room's audio to a cloud API is a materially different privacy proposition from sending a typed question — and it would break whenever the network does. |
| Speaker embedding | Resemblyzer (256-dim) | Used only for voice-print verification, behind the encrypted store. |

## Core architecture

| Concern | Choice | Why |
|---|---|---|
| Orchestration | **Hand-rolled** (`orchestrator.py`) | Deliberate. See ROADMAP "Deliberately rejected". |
| Intent routing | Hybrid: semantic-router (DeBERTa/MiniLM) → LLM tool-calling fallback | This *is* the current recommended pattern: ~65× cheaper and ~50× faster than pure-LLM routing, with the LLM layer covering the out-of-distribution queries embeddings handle badly. |
| Long-term memory | ChromaDB, split `personal` / `academic`, `user_id` on every row from day one | `user_id` threading was speculative multi-user work that turned out to also be the thing that makes future DPDP compliance a config change rather than a rewrite. |
| Memory relevance | `reranker.py`: cross-encoder `ms-marco-MiniLM-L-6-v2` (88 MB, local, via the already-installed `sentence-transformers`) behind `memory.retrieve_relevant()`; first-person queries are rewritten to third person before scoring | Replaced the fixed L2 ≤ 1.0 cutoff, which dropped the correct college fact (1.06). Measured median 64 ms for 20 candidates on the laptop CPU. On the labelled fixture: recall 15/15, general-question leaks 0/5, versus 8/15 for the old gate. |
| Memory hygiene | `memory_hygiene.py` gate on write + cleanup sweep | Added after an audit found ~half of stored "facts" were LLM preamble, bullets, duplicates, or nulls. |
| Short-term context | In-RAM `SESSION_HISTORY`, distilled to long-term on flush | Avoids writing "how much disk space do I have → 62GB" into permanent storage. |
| Tool protocol | MCP (7 servers, 27 tools) | Standard protocol, process isolation per server, hot-reloadable into the router. |
| Risk control | `tier_gate.py`, tiers 0–3 | Rule-based first so model judgment can never lower a tier. Tier 3 detected but execution disabled system-wide. |
| Plan adherence | `watchdog.py` | Answers what the tier gate cannot: *is this the action the agent said it would take?* |
| Code execution | `sandbox_runner.py` — bubblewrap namespaces, `setrlimit` fallback | Network-isolated by default, copy-on-write project mirrors. Proportionate for local single-user code; microVMs would be overkill. |
| Secrets at rest | Fernet + PBKDF2-HMAC-SHA256 (480k iterations), 0600 | Tested invariant: a plaintext canary never appears in the on-disk vault. |
| Logging | Structured JSON per module (`zedek_logger.py`) | **To be renamed to OpenTelemetry GenAI conventions** (Roadmap C1) so later tracing adoption is a config change. |
| Tests | pytest — 488 tests | No evaluation harness yet; that's Roadmap A4 and it's the biggest testing gap. |

## Specialist agents

| Agent | Module | Boundary that matters |
|---|---|---|
| Coding | `coding_agent.py` | Writes only inside one workspace; backs up before overwriting; three user checkpoints. |
| Evaluator | `evaluator_agent.py` | Three pillars: static/AST + sandboxed dynamic + a different-model LLM review. |
| Task planner | `task_planner.py` | Regex gate before the LLM call; biased toward false negatives. |
| Research / RAG | `research_agent.py` | Read-only tool allowlist; gated tools are skipped, never auto-approved; **zero sources ⇒ refuses to answer**. |
| Web / browser | `web_agent.py` | Tier-2 confirmation is *injected*; default callback denies. ⚠️ Planner currently ingests raw page text — Roadmap B1. |
| Academic tracker | `academic_tracker.py` | Own structured JSON store — aggregation queries, not semantic similarity. |
| Wake-word | `wake_word.py` | **Q&A-only**; no path from a spoken utterance to `execute()`. |
| Security | `security_module.py` | Vault, password gate, rotating single-use confirmation word, voice-print. |

## Planned additions (ROADMAP Phase F)

Chosen from the 2026-09-29 open-source survey. Each needs to pass the retrieval eval set
before it's adopted.

| Concern | Planned choice | Why this, not the alternative |
|---|---|---|
| Keyword search | `bm25s` in-memory index + reciprocal rank fusion beside ChromaDB | **Local ChromaDB has no sparse/BM25 search**. That's Cloud-only (verified, chroma #6185). bm25s is 100–500× faster than rank_bm25. |
| Structured output | Pydantic models + hand-written per-provider schema adapter + one re-ask | Every provider in the chain has a native schema mode. Instructor would pull in SDK clients, against the no-LiteLLM decision, and Outlines adds nothing over Ollama's `format`. |
| Caching | Static-first prompt layout (for provider prefix caches) + SQLite exact-match cache for deterministic sub-tasks | Semantic caches hit 5–15% on conversation and serve stale personal answers. |
| Memory embeddings | `snowflake-arctic-embed-s` or `bge-small-en-v1.5` (both 384-dim) | Same size as MiniLM, better retrieval (self-reported). The eval set decides. |
| Router embeddings (candidate) | `model2vec` `potion-base-8M` | Up to 500× faster on CPU. Router only, never memory. Accepted only if routing accuracy holds. |
| Core memory | Always-in-prompt `user_profile` block as git-tracked markdown | Letta/OpenClaw pattern: no retrieval miss on the most-used facts, and every learned change is diffable. |
| Scheduling | APScheduler + SQLite | For the proactive digest. Scheduled prompts are now standard across Open WebUI, LibreChat, Khoj, and AnythingLLM. |
| Voice latency | Sentence-split streaming into TTS; grammar-limited Vosk recognizer for commands | Home Assistant measured 5–6 s → ~0.5 s time-to-speech, and ~150 ms command recognition. |
| Citation check (maybe) | MiniCheck ~0.4B NLI model, local | Catches "post-rationalized" citations with no LLM call. CPU latency is unpublished, so benchmark it first. |

Full evidence and rejected alternatives: [`reports/Open source assistant improvements.md`](reports/Open%20source%20assistant%20improvements.md).

## Development process

| Concern | Choice | Status |
|---|---|---|
| Change management | **OpenSpec** (spec-driven: propose → review → apply → archive) | Proposed; not yet installed. It would replace `task.md` and gradually slim `zedek_context.md`. Telemetry to be disabled. Needs Node ≥ 20.19 (installed: 20.20.2). |

## Dependency policy

1. **Prefer stdlib and what's already installed.** Nearly every roadmap item is
   stdlib + sklearn + config.
2. **Adopt patterns, not platforms.** Bi-temporal memory from Zep, routing ideas from
   LiteLLM, typed state from LangGraph — all as local code, no dependency.
3. **Zero evaluation/observability SaaS** until the free local version exists and hurts.
4. **Pin anything that touches the numeric stack.** An audio install once silently
   upgraded numpy 1.x → 2.5.3. It happened to be fine (all tests still passed), but it
   was unintended and should not have been possible.

## Known stack risks

- **numpy 2.5.3** arrived via an unpinned transitive upgrade. Currently fine; worth a
  `requirements.txt` pin so it's a decision rather than an accident.
- **`webrtcvad` needs `pkg_resources`**, which newer setuptools removes. Currently
  satisfied; a setuptools upgrade could break Resemblyzer.
- **`requirements.txt` had drifted from the real environment** — *fixed 2026-09-29.*
  It pinned 12 packages but omitted `cryptography` (the vault), `httpx` (every MCP
  server), `playwright` (the web agent), and the whole audio stack. A fresh install would
  have produced an environment where the vault failed to import and voice features
  silently reported "unavailable". All are now pinned to their installed versions.
- **Vosk model (68 MB) and MiniLM live in `models/`**, gitignored. A fresh clone needs a
  documented bootstrap step; `setup.sh` should own it.
