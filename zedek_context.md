# Zedek — AI Assistant System — Build Context

This document is for an AI coding assistant (e.g. GitHub Copilot) picking up
development of this project. Read this FIRST, then read the actual files in
this folder — this document explains the *why* behind decisions that aren't
obvious from the code alone, and lists things already tried/fixed so they
aren't accidentally reintroduced.

## Project goal

A personal AI assistant ("Zedek") for a computer science student, built to:
- Assist with coding/development (with real safety controls, not just raw execution)
- Act as a personalized study/placement-prep tutor (remembers the user's
  actual academic context across sessions — not generic advice)
- Run mostly cloud-backed (student has good internet most of the time) with
  local models (Ollama, Qwen2.5-Coder-7B, Llama3.1-8B) as an offline backup
- Prioritize **safety and honesty over speed** — the user has explicitly
  chosen accuracy-over-latency multiple times when the two conflicted

The user (Nithiish) is building this personally and reviewing/testing every
change themselves — treat this as a real, evolving system with a human
actively verifying behavior, not a one-shot build.

## Hardware / environment constraints

- Laptop GPU: RTX 4050, **6GB VRAM only** — tight. Local LLMs (Qwen2.5-Coder-7B,
  Llama3.1-8B, both Q4_K_M quantized, ~4.5GB each) are meant to run
  **sequentially, not concurrently**. The classifier (DeBERTa) deliberately
  runs on CPU (`device=-1`) specifically to avoid competing for this VRAM.
- Linux (Ubuntu-based), Conda environment named `zedek-env`.
- 100GB total disk, not a constraint — storage was explicitly ruled out as a
  concern early on; don't over-optimize for disk space.
- `torch` MUST be installed CPU-only (`pip install torch --index-url
  https://download.pytorch.org/whl/cpu`) — the default pip install pulls a
  CUDA build that fails to even import without cuDNN installed system-wide.
  This already broke once; don't let requirements.txt silently reintroduce
  the GPU build.
- `HF_HUB_OFFLINE=1` is set in classifier.py deliberately — Hugging Face's
  library does a network check on every load even when the model is already
  cached, and this caused hard failures during a transient DNS issue. Once
  the model is downloaded once, it should never need network again.

## Architecture principles (do not violate these)

1. **Sandboxing over trust**: the LLM never generates raw shell commands.
   `system_agent.py` exposes a fixed, hardcoded set of Python functions —
   the LLM can only pick from this allowlist, never construct arbitrary
   commands. All file operations are hard-restricted to the user's home
   directory (`_validate_path()` in system_agent.py blocks path traversal).

2. **Tier gate is rule-based first, not model judgment**: `tier_gate.py`
   classifies every action 0-3 using hardcoded pattern matching (checks
   BOTH the resolved function args AND the raw user input text — this was
   a real bug fix, see "Known issues already fixed" below). Model judgment
   never overrides a hardcoded escalation rule. **Tier 3 is currently
   disabled at the execution level** (detection stays active, dispatch is
   blocked) — this was an explicit user decision. Do not silently re-enable
   Tier 3 execution; it requires per-task manual override by the user.

3. **Universal no-rollback rule**: any irreversible action (delete, submit,
   etc.) must stop and ask for confirmation with a plain statement of what
   it's about to do and whether it's judged safe — across ALL modules, not
   just forms. This was an explicit design decision generalized from a
   narrower "form checkpoint" idea.

4. **Classification and generation are split across different models
   on purpose**: `classifier.py` (DeBERTa-v3 zero-shot, CPU) decides
   intent/domain. Llama3.1 is used ONLY for narrow downstream tasks:
   extracting function arguments, canonicalizing facts, answering
   questions, generating corrections. This split happened because Llama
   alone was unreliably misrouting requests (see known issues below) —
   **do not merge classification back into a single generative model call**
   without a strong reason; the split was a deliberate fix, not incidental.

5. **Never trust a model's self-reported confidence or its own unvalidated
   output for storage.** Every place that writes to permanent memory
   (`canonicalize_fact`, fact corrections) validates the output before
   storing — rejecting placeholder/hallucinated content like "Unknown" or
   "N/A" rather than storing it. This pattern should be followed for any
   new function that writes to long-term memory.

6. **Short-term vs long-term memory are different mechanisms, kept separate**:
   `SESSION_HISTORY` (in `orchestrator.py`) is an in-RAM list for the
   current session only — fast, exact recall, never written to disk per-turn.
   `memory.py` (ChromaDB) is long-term, cross-session, and is only written
   to via a **distillation pass** (`summarize_and_flush_session()`) that
   extracts what's actually worth keeping — not a raw dump of every turn.
   This was a deliberate redesign after the original "store every turn"
   approach was identified as wasteful.

7. **Cloud-first with graceful local fallback, transparently.** Every
   cloud-backed function (`coding_agent.py`) tries multiple free-tier
   providers in order and ALWAYS reports which one actually answered
   (`source` field) — never hide which tier responded. Order: Gemini →
   Groq → NVIDIA NIM → GitHub Models → Cerebras → local Qwen (guaranteed
   fallback, no quota, works offline).

8. **Multi-user design (not yet built, but planned for)**: every memory
   item is already tagged with `user_id` from day one, even with only one
   user (`nithiish`) currently. This was intentional — retrofitting
   per-user scoping later would be far more painful than building it in
   now. When multi-user support is eventually built: owner-initiated
   enrollment only, guided voice sample capture, permissions assigned by
   the owner afterward in a security panel (not built yet).

## File inventory (what exists, what each does)

- `zedek_logger.py` — structured JSON logging, every module uses this.
  `get_logger(module_name)` writes to `logs/<module_name>.log` AND console.
- `system_agent.py` — sandboxed, read-only system functions: search_files,
  disk_usage_by_folder, top_memory_processes, free_space_summary. Hard
  home-directory restriction via `_validate_path()`.
- `tier_gate.py` — rule-based risk classification (0-3) with hardcoded
  escalation patterns. Tier 3 execution disabled by design (see above).
- `memory.py` — ChromaDB wrapper. `store()`/`retrieve()`/`delete_by_ids()`.
  Domain-partitioned ("personal"/"academic"), user_id-tagged, content_type
  distinguishes "fact" vs "conversation".
- `classifier.py` — DeBERTa-v3 zero-shot intent + domain classifier, CPU-only.
  Categories: search_files, disk_usage_by_folder, top_memory_processes,
  free_space_summary, remember_fact, correct_fact, coding_task, unsupported,
  general_question.
- `orchestrator.py` — the main pipeline. Routes via classifier.py, extracts
  args via a narrow Llama call, runs through tier_gate, executes or answers,
  manages SESSION_HISTORY and memory flush. This is the file most actively
  under development.
- `coding_agent.py` — narrow coding specialist and verifier workflow following
  plan -> patch -> test -> verify, with bounded Python execution through
  bubblewrap. Provider-backed generation and repository patching are not wired
  into this scaffold yet.
- `.env.example` — template for API keys (Gemini, Groq, NVIDIA, GitHub
  Models, Cerebras). Real `.env` is gitignored, never commit it.
- `cleanup_garbage_facts.py` — one-time script, already used to clean up
  bad "Unknown" facts. Can be deleted or kept as a reusable utility.

## Known issues already found and fixed (do not reintroduce)

1. **Router force-matching loose/partial keyword overlaps into the wrong
   function** (e.g. "I study at PSG College" → misrouted to `search_files`
   with an invented, invalid path). Fixed by: (a) adding explicit
   `remember_fact` classification, (b) later replacing Llama-based routing
   entirely with the dedicated classifier.

2. **Hallucinated placeholder facts** — when the router incorrectly
   classified a *question* ("what is my name") as `remember_fact`,
   `canonicalize_fact` would invent "User's name: Unknown" and store it as
   if real. Fixed with explicit rejection of placeholder markers
   ("unknown", "n/a", "not specified", etc.) before any write to memory,
   plus a graceful fallback to answering as a question instead.

3. **Confident hallucination of real-world facts** (e.g. inventing wrong
   details about an obscure village/place) when no relevant memory existed
   — the model filled the gap with its own uncertain pretrained knowledge,
   stated confidently. This is NOT fully solved — the anti-hallucination
   prompt only prevents inventing facts *not in retrieved context*; it does
   not prevent confident misuse of the model's own shaky general knowledge.
   **This is the primary reason an evaluator/verifier agent is still needed**
   (planned, not yet built — see Next Steps).

4. **Stale facts never got corrected, only added on top of** — user said
   "there's no exam next week, that was false" but the system had no
   mechanism to find and retract the old stored fact; it kept getting
   surfaced as true. Fixed by adding `correct_fact` as its own
   classification category with `_handle_correction()` in orchestrator.py,
   which finds the best-matching old fact, deletes it, stores the
   corrected version.

5. **Logging collision**: passing a dict with a key literally named `"args"`
   into Python's `logging` `extra={}` parameter crashes, because `args` is
   a reserved LogRecord attribute. Renamed to `call_args`/`decision` in
   affected calls. Watch for this if adding new log calls with `extra=`.

6. **`psutil.disk_usage()` returns 4 values, not 3** (`total, used, free,
   percent`) — a real bug that crashed `free_space_summary()`.

7. **torch CUDA build vs CPU build** and **HF_HUB_OFFLINE** — see
   Environment section above.

8. **Ambiguous terms like 'astro' are treated as resolved facts too early** —
   a user asking about "astro" could mean astronomy, astrology, or the Astro
   frontend framework. The first loop incorrectly answered as if the term had
   already been resolved, and the follow-up correction path could misclassify
   a clarification as a fact correction. Fixed by adding a deliberate
   ambiguity-detection step in `orchestrator.py`: if a user input contains an
   ambiguous term without enough context, Zedek now asks a clarifying question
   before answering or storing anything. If the user then corrects the
   meaning, the assistant responds with a friendly acknowledgment instead of
   trying to mutate memory as if it were a stale fact.

9. **Tone/style mismatch** — the assistant sometimes answered in a flat,
   generic way instead of matching the user's energy or prompting style.
   Added a lightweight tone adapter that detects casual phrasing such as
   "hey", "bro", "pls", "quick" and responds in a more playful, relaxed,
   conversational style while still staying clear and useful.

## Notable changes added during the Astro/ambiguity debugging pass

- Added `should_treat_as_disambiguation()` to detect when the user is
  clarifying a previous ambiguous term rather than making a real fact update.
- Added `should_ask_ambiguous_term_question()` to catch under-specified inputs
  like "what is astro?" and ask the user which meaning they want before
  answering.
- Added `generate_ambiguity_reply()` to produce a more natural, human-like
  clarification with a warmer tone and a little personality.
- Added `tone_for_prompt()` so the assistant can adapt its voice to a casual
  user without becoming shallow or overly slang-heavy for a formal one.
- Added an explicit safeguard in the main `handle()` path so clarifications no
  longer flow into the `correct_fact` memory mutation path.
- This was validated against the real session behavior where the user first
  asked about "astro" and then clarified "Astro frontend framework"; the
  assistant now avoids the bad correction cycle and instead responds with a
  friendly clarification and a richer follow-up question.

## Testing status

- Phases 1-5 (environment, logging, orchestrator+system agent, tier gate,
  memory) are built and confirmed working via direct user testing.
- Phase 6 (memory integration, session history, fact canonicalization,
  classifier-based routing, fact correction) is built and re-tested after the
  ambiguity/correction fix pass.
- The recent ambiguity/tone pass is complete and verified: short gratitude
  phrases like "okay thank you" no longer trigger `correct_fact`, and the
  literal "astro" clarification flow now resolves the meaning before answering.
- `coding_agent.py` has focused tests for planning, Python syntax verification,
  and bubblewrap execution. Provider-backed generation is not wired or tested
  yet.
- Coding requests now receive a dedicated plan-only response from the
  orchestrator and require explicit approval before future patching work.
- Sandboxed Python execution exists through bubblewrap; broader repository
  mutation and test execution remain deliberately restricted.
- No evaluator/verifier agent yet.
- No watchdog, no security module, no wake-word listener, no remaining
  specialist agents (research, web) yet.

## Completed in the current iteration (do not repeat)

- Fixed the false `correct_fact` classification for acknowledgment phrases such
  as "okay thank you" and "thanks".
- Added ambiguity detection for terms like "astro" so the assistant asks a
  clarifying question before answering or mutating memory.
- Added a friendly clarification flow for corrected meanings like "I meant
  Astro frontend framework, not astrology".
- Added a casual-tone adapter that matches light, relaxed prompting styles
  without becoming sloppy or unprofessional.
- Added regression checks for the ambiguity and gratitude cases in the test
  file(s) for this pass.
- Updated project tracking to reflect that this pass is complete and should not
  be re-opened unless a new regression appears.

## Next steps (in the order previously agreed, now continuing from the current state)

1. **Test provider-backed coding generation** with real free-tier API keys.
2. **Coding specialist + verifier loop (OpenHands / SWE-agent pattern)** —
   add a dedicated coding sub-agent that follows a plan → patch → test → verify
   loop, rather than trying to do everything in one step. This is a proven
   architecture pattern for code-heavy tasks and is especially useful for
   repo changes, bug fixing, and validation workflows.
3. **Expand sandboxed execution carefully** to support isolated test runs;
  the current bubblewrap runner handles Python snippets only and does not
  expose the repository or arbitrary shell commands.
4. **Evaluator/verifier agent** — separate from the task agent and the
   watchdog, ideally using a different model than whichever one performed
   the task, to catch hallucinated/wrong content (see known issue #3 above,
   still unresolved). This should review patch correctness, test results,
   and whether the agent stayed within the user's actual intent.
5. **Task planner / decomposer agent (optional but useful)** — a lightweight
   planning pass that breaks a large request into concrete sub-tasks and
   dependency order before execution. This can be implemented as a small,
   specialized planner rather than a full multi-agent company model.
6. **Remaining specialist agents**: research/RAG agent, web/browser agent.
7. **Watchdog module** — separate process, observes agent actions against
   stated plans, two-checkpoint flow for Tier 3 (pre-fill, pre-submit) —
   scaffolded in design but Tier 3 execution is currently OFF, so this
   isn't urgent yet.
8. **Security module** — confirmation word + rotation, voice-print
   verification (in scope per user, not deferred), separate voice listener,
   password-gated UI panel, isolated encrypted local storage. Not started.
9. **Wake-word general Q&A mode** — always-on lightweight listener, separate
   from the security module's voice channel.
10. **Academic/placement-prep tracking** — the actual "personal tutor" use
    case (DSA/aptitude practice tracking, weak-topic identification) hasn't
    been built yet; this was identified as the real differentiator the user
    wants but is still just a stated goal, not implemented.

### Architecture guidance to keep in the system design

- Use the OpenHands / SWE-agent pattern as a blueprint for the coding layer:
  plan → read context → patch → run tests → fix failures → verify.
- Keep the current personal-assistant architecture as the top-level orchestrator;
  do not replace it with a fully autonomous coding bot.
- Keep the safety and tier gate in front of all execution steps, even for the
  specialist coding agent.
- Treat the verifier as non-optional: any code-generation path should have a
  second pass that checks correctness, not just output style.
- Keep the multi-agent decomposition lightweight and explicit; a full social
  "company-of-agents" structure is not required for this project's current
  goals and would increase complexity faster than value.

> Important: the ambiguity-handling, gratitude guard, and tone adaptation pass
> is complete and should be treated as finished work. Do not reopen or repeat
> these fixes unless a new regression specifically reappears during testing.

## User's stated priorities, in their own words (for judgment calls)

- Accuracy over speed/latency, explicitly, more than once.
- Local models are a backup for no-internet situations only — most usage
  is expected to be cloud-backed, free-tier.
- Prefers narrow, single-responsibility components over one model doing
  many jobs — this preference directly drove the classifier/Llama split.
- Wants to review and test every change personally — don't skip verification
  steps or assume something works without the user confirming test output.
- Explicitly not doing Tier 3 (payment/high-risk) tasks right now, but wants
  the framework built and ready for when that changes.