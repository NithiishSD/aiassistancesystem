# ⚠️ Superseded by OpenSpec (2026-09-29)

Active work is now tracked as OpenSpec changes, not in this file:

    openspec list                 # open changes and task progress
    openspec show <change>        # read a proposal
    /opsx:apply <change>          # implement an approved change (Claude Code)
    /opsx-apply <change>          # same, in Continue / Antigravity

The plan itself is ROADMAP.md. This file is kept as the history of Items 1-14 and the
2026-09-29 hardening pass.

---

# Current task: Production-hardening pass (ROADMAP.md Phases A–E)

Items 1–14 are complete (history below). The active plan is now **[`ROADMAP.md`](ROADMAP.md)**,
produced from an external research pass + live system audit on 2026-09-29.

## Done this pass (2026-09-29)
- [x] Researched 2026 practice: agent architectures, security/compliance, production engineering
- [x] Audited the live memory store — ~half of 135 stored facts were junk
- [x] **A1:** `memory_hygiene.py` — validation gate wired into `memory.store()` + cleanup sweep with in-place repair (43 tests)
- [x] Fixed `requirements.txt` drift — `cryptography`, `httpx`, `playwright`, and the audio stack were all missing
- [x] Wrote `ROADMAP.md`, `TECH_STACK.md`, `COMPLIANCE.md`; amended the MCP guidance in `zedek_context.md`

## Next up, in order
- [ ] **Apply the memory cleanup to the live store** — needs your go-ahead (deletes 36 rows, repairs 37).
      Command: `python memory_hygiene.py --apply`
- [ ] **A3** — relevance threshold in `answer_general_question()` (~3 lines)
- [ ] **B1** — quarantine web-page content from the web agent's planner (dual-LLM)
- [ ] **A4** — routing eval harness: `evals/routing_golden.csv` + pytest/sklearn confusion matrix
- [ ] **B2** — treat MCP descriptions as untrusted; mandatory pinned `default_tier`
- [ ] **C1/C2** — `trace_id` per turn, `gen_ai.*` log fields, per-provider counters
- [ ] **D3** — swap local fallback to Qwen3 8B, re-run evals
- [ ] **D1/D2** — proactive daily digest; token streaming
- [ ] **Phase F** (from the open-source survey) — F1 reranker → F2 schema JSON → F3 BM25 hybrid → F4 page chunking.
      See ROADMAP.md "Suggested order" and `reports/Open source assistant improvements.md`.
- [ ] **Decide on OpenSpec** — proposed as a replacement for this file; not yet installed.

Still open from before:
- [ ] Wake-word loop against LIVE SPEECH — needs you to run it and talk
- [ ] Calibrate the voice-print threshold (0.75) against your real voice

---

# History: Complete remaining roadmap (Items 9–14) ✅

Worked through every remaining roadmap item in order, each with the same
rigor as Item 8: plan → implement → test → run full suite → document in
`zedek_context.md`.

## Item 9 — Task planner / decomposer agent ✅ COMPLETE
- [x] Add `"planning"` task profile to `llm_provider.py`
- [x] Build `task_planner.py` (`should_decompose()` heuristic + LLM `decompose()`)
- [x] Refactor `orchestrator.handle()` into `_handle_single()` / `_handle_decomposed()`
- [x] Support `{{result_of_N}}` placeholders so a later sub-task can use an earlier result
- [x] Test suite `tests/test_task_planner.py` (19 tests)
- [x] Full suite green (240 passed)

## Item 10 — Remaining specialist agents ✅ COMPLETE
### 10a: Research / RAG agent
- [x] Add `"research"` task profile to `llm_provider.py`
- [x] Build `research_agent.py`: plan queries → gather (memory + Wikipedia/arXiv/Scholar/URL) → synthesize with `[S1]` citations
- [x] Read-only tool allowlist; every MCP call passes the tier gate; gated tools are skipped, never auto-run
- [x] Anti-hallucination: zero sources ⇒ refuses to answer instead of guessing
- [x] Register `research_task` intent in `classifier.py` + `classifier_tools.py`; dispatch in `orchestrator.execute()`
- [x] Test suite `tests/test_research_agent.py` (27 tests)

### 10b: Web / browser agent
- [x] Build `web_agent.py`: bounded observe → act loop over the stateless Playwright MCP tools
- [x] Browser-tool allowlist + http(s)-only URL validation
- [x] Tier 2 confirmation is INJECTED (`confirm_fn`); the agent never self-approves, default callback denies
- [x] Denied/blocked action stops the loop rather than routing around the refusal
- [x] Register `web_task` intent in `classifier.py` + `classifier_tools.py`; dispatch in `orchestrator.execute()`
- [x] Test suite `tests/test_web_agent.py` (28 tests)

## Item 11 — Watchdog module ✅ COMPLETE
- [x] `watchdog.py`: plan registration + `observe()` deviation detection
- [x] Undeclared Tier 2+ action refused; undeclared read-only action flagged but allowed
- [x] Two-checkpoint Tier 3 flow — pre-submit requires pre-fill, and refuses entirely while Tier 3 is disabled
- [x] Append-only JSONL audit trail with sensitive-value redaction
- [x] Wired into the coding flow: a patch targeting an undeclared file is blocked before the apply prompt
- [x] Test suite `tests/test_watchdog.py` (26 tests)

## Item 12 — Security module ✅ COMPLETE (audio backend pending)
- [x] `SecureStore`: Fernet + PBKDF2(480k), 0600 perms, plaintext never on disk
- [x] `PasswordGate`: salted hash only, constant-time compare, lockout after 5 failures
- [x] `ConfirmationWord`: rotating, single-use, expiring — cannot be replayed
- [x] `VoicePrintVerifier`: interface + fail-closed contract implemented and tested
- [x] Voice-print AUDIO BACKEND — WIRED. `resemblyzer` + `sounddevice` installed;
      `_embed()` implemented (256-dim), `record()` captures from the mic.
      Verified: identical audio -> 1.0 similarity -> verified; different -> 0.65 -> rejected;
      print encrypted at rest. NOT yet calibrated against real speech.
- [x] Test suite `tests/test_security_module.py` (45 tests)

## Item 13 — Wake-word general Q&A mode ✅ COMPLETE (audio backend pending)
- [x] `wake_word.py`: wake-word detection with STT-variant tolerance, activation window, sleep phrases
- [x] Q&A-ONLY boundary: no code path from a spoken utterance to `execute()`
- [x] Kept separate from the security voice channel — never an authorization signal
- [x] Microphone CAPTURE BACKEND — WIRED. `sounddevice` capture + offline Vosk STT
      (`models/vosk-model-small-en-us-0.15`, 68MB). Local transcription on purpose:
      an always-on mic streaming to a cloud API is a different privacy proposition.
      Verified: model loads, mic detected. NOT yet run against live speech.
- [x] Test suite `tests/test_wake_word.py` (26 tests)

## Item 14 — Academic / placement-prep tracking ✅ COMPLETE
- [x] `academic_tracker.py` with its own structured JSON store (not ChromaDB — wrong retrieval model)
- [x] Practice logging: topic, result, problem, difficulty, minutes, notes
- [x] Weakness scoring: Laplace-smoothed accuracy + staleness + low-volume, each recommendation explains itself
- [x] Cold start admits it has no data instead of inventing weak topics
- [x] Practice streaks; corrupt-file resilience
- [x] Intent `academic_tracking` registered; orchestrator defaults to review on extraction failure
- [x] Test suite `tests/test_academic_tracker.py` (37 tests)

## Live verification pass (mocks are not evidence)
- [x] Research agent run against real network + MCP servers
- [x] Web agent run against real Chromium (example.com), deny-path confirmed to block
- [x] Voice-print enroll -> verify round trip through the encrypted store
- [x] Vosk model + mic detected and loading
- [ ] Wake-word loop against LIVE SPEECH — needs you to run it and talk

### Bugs the live run found that mocks never could
- [x] `mcp_online_server.search_wikipedia` built the REST summary URL with
      `quote_plus`, producing `Binary+search+tree`. That endpoint takes a PATH
      segment, so EVERY Wikipedia summary 404'd and returned "(Summary unavailable)".
- [x] "No X found for ..." responses were being counted as research sources —
      inflating the source count and marking ungrounded reports as grounded.
- [x] `memory.retrieve()` discarded Chroma's distances, so no caller could tell a
      0.49 match from a 1.97 one. Every research question dragged in unrelated
      personal facts. Distances are now returned and filtered at 1.0.
- [x] Wikipedia OpenSearch matches TITLES, so the planner's descriptive queries
      ("binary search tree data structure") silently found nothing. Now retries shortened.

## Final state
- Full suite: **445 passed, 0 failures** (was 221 at the start of this work)
- 7 new modules, 7 new test suites, 3 new intents, 2 new LLM task profiles

## Codebase health pass (alongside the above)
- [x] Fixed real bug: `evaluator_agent.py` used `os` without importing it —
      `NameError` on every project-root verification run
- [x] Fixed `research_agent` calling `scholar_search_papers` with `limit`
      instead of its actual `max_results` parameter
- [x] Removed dead imports/variables across 8 modules (pyflakes clean except
      harmless redundant `global` declarations and deliberate availability probes)
- [x] Fixed real bug: the user-approved coding plan was discarded —
      `implement_and_verify()` re-planned internally, so the user approved plan A
      while the agent coded against a regenerated plan B
