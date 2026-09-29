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

## Project documentation rule

Every code, configuration, test, or architecture change made in this project
must also be recorded in this file. Update the relevant inventory, testing
status, known issues, completed work, or next-steps section in the same change
so project context stays current without requiring the user to repeat this
instruction.

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
  
8**Hybrid Cascading Strategy**: 
   - **Local Path**: Uses `semantic-router` (running `sentence-transformers/all-MiniLM-L6-v2` on CPU) for immediate, high-confidence local matches (`score >= 0.65`).
   - **Cloud/LLM Escalation**: If local confidence drops below `0.65` or returns `general_question`, the input cascades to `llm_provider.py` using Function/Tool Calling schemas (`classify_intent`) for accurate intent identification.

9. **Dynamic Online Learning**:
   - Rather than relying on hardcoded starter utterances, every prompt successfully classified via the LLM function-calling fallback is dynamically saved to local disk (`data/dynamic_utterances.json`).
   - On future runs, these newly learned user-phrasings are loaded back into the local vector index, converting slow LLM fallbacks into instant local hits over time.
   - **Quality Guardrail**: Prompts over 15 words are excluded from auto-saving to prevent cluttering the local vector space with multi-sentence or narrative prompts.

10. **Function-Calling Schema**:
   - Centralized tool declarations (`ROUTER_TOOLS`) map directly to Zedek's existing intent enumeration (`search_files`, `disk_usage_by_folder`, `top_memory_processes`, `free_space_summary`, `directory_size`, `remember_fact`, `correct_fact`, `coding_task`, `unsupported`, `list_processes_detailed`, `open_application`, `general_question`).
11. **Multi-user design (not yet built, but planned for)**: every memory
   item is already tagged with `user_id` from day one, even with only one
   user (`nithiish`) currently. This was intentional — retrofitting
   per-user scoping later would be far more painful than building it in
   now. When multi-user support is eventually built: owner-initiated
   enrollment only, guided voice sample capture, permissions assigned by
   the owner afterward in a security panel (not built yet).

## File inventory (what exists, what each does)

- `zedek_logger.py` — structured JSON logging, every module uses this.
  `get_logger(module_name)` writes to `logs/<module_name>.log` AND console.
- `system_agent.py` — sandboxed system functions: read-only filesystem and
  process inspection plus controlled application launching. Hard
  home-directory restriction via `_validate_path()`.
- `tier_gate.py` — rule-based risk classification (0-3) with hardcoded
  escalation patterns. Tier 3 execution disabled by design (see above).
- `command_verifier.py` — extra verifier for dynamically generated coding-agent
  commands. Static checks block Tier 3 patterns, safely execute recognized
  read-only commands, and pass write/destructive commands to the normal tier
  gate; isolated destructive dry-runs are not implemented yet.
- `llm_provider.py` — shared task-aware provider chain: Gemini, Groq, NVIDIA
  NIM, OpenRouter, Cerebras, and local Ollama fallback. It returns both the
  generated answer and provider source. Coding prompts currently pass through
  a full-access placeholder for the future LLM-backed security model;
  `ALLOW_CLOUD_CODING=false` keeps coding local until that model exists.
  `ALLOW_CLOUD=false` and `force_local=True` bypass cloud entirely.
- `memory.py` — ChromaDB wrapper with an explicit local CPU
  `all-MiniLM-L6-v2` embedding pipeline. `store()`/`retrieve()`/
  `delete_by_ids()` are domain-partitioned ("personal"/"academic"),
  user_id-scoped, and content_type distinguishes "fact" vs "conversation".
- `classifier_tools.py` — Centralized JSON function-calling tool schema (`ROUTER_TOOLS`
  and `VALID_INTENT_NAMES`) for the 12 known intents. Used exclusively by the Layer 2
  LLM escalation path inside `classifier.py`. Must be kept in sync with `INTENT_UTTERANCES`.
- `classifier.py` — Hybrid Cascading classifier. Layer 1: `semantic-router` (CPU MiniLM
  encoder) with a `0.65` cosine threshold for fast local routing (< 5 ms). Layer 2:
  LLM tool-calling via `llm_provider.py` when the Layer 1 score drops below `0.65`.
  Dynamic online learning: successful LLM classifications are saved to
  `data/dynamic_utterances.json` and hot-reloaded into the in-memory `RouteLayer`,
  converting slow LLM paths into fast local hits over time. Quality guardrail: phrases
  > 15 words and `general_question` outcomes are never saved.
- `data/dynamic_utterances.json` — runtime-created JSON store of dynamically learned
  user phrasings, keyed by intent name. Created automatically on first LLM-classified
  save; gitignored (personal phrasing, not code). Loaded at classifier startup and
  merged into the `RouteLayer` before any classification occurs.
- `tests/test_hybrid_router.py` — 27-test offline suite (all LLM calls mocked) for the
  hybrid router: Layer 1 local hits, Layer 2 escalation, dynamic persistence (save /
  dedup / hot-reload), 15-word quality guard, `general_question` exclusion, and
  `ROUTER_TOOLS` schema integrity. All prior regression cases re-tested here too.

- `orchestrator.py` — the main pipeline. Routes via classifier.py, extracts
  args via a narrow Llama call, runs through tier_gate, executes or answers,
  manages SESSION_HISTORY and memory flush. This is the file most actively
  under development. It also owns the ambiguity and tone helpers, including
  `should_treat_as_disambiguation()`,
  `should_ask_ambiguous_term_question()`, `generate_ambiguity_reply()`, and
  `tone_for_prompt()`. Nuanced process questions are answered by
  `_reason_over_process_data()` using the collected process data only.
- `coding_agent.py` — SWE-agent pattern coding specialist (plan → read context → patch → test → verify).
  Read boundary: `/home/nithiish/Documents` (50KB cap).
  Write boundary: `/home/nithiish/Documents/ai_assistanceworkspace` only.
  Includes context-aware planning, structured code patching, automated test generation, bubblewrap sandboxing
  (isolated and project-aware modes with optional network for API testing), second-pass LLM review
  (using `general_qa` profile for a true second opinion), automatic timestamped backups (`.backups/`),
  and explicit user confirmation before applying file modifications.
- `tests/test_coding_agent.py` — 35 offline unit tests for `coding_agent.py` covering path validation,
  file reading limits, markdown stripping, plan/patch/test generation, LLM review parsing, file application with backups,
  and bubblewrap sandbox runner modes.
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
10. **Static Vector Router Misrouting & Missed Phrasings**:
   - **Issue**: Static seed utterances in `semantic-router` failed on novel phrasing or slang, forcing requests into `general_question` or incorrect routes. Hardcoded updates were inefficient.
   - **Fix**: Implemented Strategy 2 (Hybrid Cascading) with Tool Calling in `llm_provider.py` as Layer 2. Added automatic vector ingestion (`add_utterance_dynamically`) to persist LLM-routed queries locally in `data/dynamic_utterances.json`.

11. **Mid-session buffer flush wiping active context (0 session turns used)**:
   - **Issue**: When `SESSION_HISTORY` reached 11 turns, `summarize_and_flush_session` cleared the entire history to `[]`. The very next turn (e.g. user answering "no its not correct" to a question Zedek asked) had 0 context turns, causing Zedek to lose all track of the active dialogue.
   - **Fix**: Added `keep_recent=4` so mid-session buffer flushes keep a rolling window of the last 4 turns in RAM, ensuring dialogue continuity is never lost. Full clearing only occurs on session exit (`quit`).

12. **Multi-fact statements conflated into a single malformed fact**:
   - **Issue**: Statements containing multiple distinct details (e.g. "software system is the program by amcs department and ml java are the subjects") were squashed into `User's subject: ML Java, Software System` because the prompt only permitted one line.
   - **Fix**: Updated `canonicalize_fact` to parse and output multiple distinct standardized lines (`list[str]`) for distinct attributes, storing each one cleanly in memory.

13. **Fact retraction and deletion rejected by `_handle_correction`**:
   - **Issue**: When a user wanted to purely remove/retract a mistaken or false fact (e.g. "no you mistook that, remove that from memory"), `_handle_correction` rejected it because it required `corrected_fact` to not be null.
   - **Fix**: Updated `_handle_correction` to handle pure deletions/retractions when `corrected_fact` is null/None, deleting the old fact and confirming removal without needing a replacement fact.

14. **Vector index poisoning via unverified dynamic learning**:
   - **Issue**: `classify_intent` was saving LLM tool-calling classifications immediately before execution. If the LLM made a mistake (e.g. classifying "build a website using html css" as `open_application`), it saved that bad phrasing into `data/dynamic_utterances.json`, which caused future similar queries to immediately hit Layer 1 at 0.65 similarity and fail without reaching the LLM.
   - **Fix**: Implemented the 3-pillar safety system:
     (1) **Execution-verified ingestion**: Dynamic utterances are only persisted in `orchestrator.py` after verified execution success (e.g. `open_application` actually found and launched the binary, coding plan was approved, fact was stored).
     (2) **Semantic sanity filters**: `_is_semantically_valid_for_intent` blocks impossible pairings (e.g. creation/coding verbs are strictly forbidden from being ingested into `open_application`).
     (3) **Self-healing pruning**: `remove_utterance_dynamically` automatically purges previously ingested utterances if the user provides a correction on the following turn.

15. **Fragile manual utterance lists replaced with Description-Based Semantic Routing & LRU Fast Caching**:
   - **Issue**: Manually curating and hardcoding lists of user sentences was unscalable, fragile to novel phrasing, and impossible to exhaustively test.
   - **Fix**: Upgraded Layer 1 to use **rich semantic capability descriptions** for each intent route instead of sparse sentence examples. Added an **in-memory LRU fast cache** (`_ROUTING_CACHE`, capacity 256) providing instant `< 1ms` resolution for repeated/frequent commands, while Layer 2 LLM tool-calling handles any ambiguous/novel queries.

16. **Meta-questions misclassified into `open_application`, thinking tags leakage, and cloud request timeout bottleneck**:
   - **Issue**:
     (a) Informational questions like "what are all application in this system you can open it" matched `open_application` on Layer 1 due to high keyword overlap ("application", "system", "open"), and `_extract_application_name` passed the entire sentence as the target binary.
     (b) Reasoning models on Groq output raw `<think>...</think>` traces to the CLI.
     (c) `REQUEST_TIMEOUT` was 45s, causing consecutive timeout cascades (up to 90s latency) when Gemini experienced read timeouts.
   - **Fix**:
     (a) Added semantic sanity verification on Layer 1 router hits in `classifier.py` preventing informational questions from executing `open_application`; hardened `_extract_application_name` and `execute_action` to reject multi-word non-app queries.
     (b) Added `strip_thinking_tags` in `llm_provider.py` to scrub reasoning traces before presenting responses.
     (c) Reduced `REQUEST_TIMEOUT` from 45s to 12s in `llm_provider.py` and deduplicated candidate pools.

17. **Fixed-function bottleneck resolved with Dynamic Read-Only Command Inspection & Fast-Path Tool Caching**:
   - **Issue**: Requests for system diagnostics or environment metrics outside the 6 hardcoded functions in `system_agent.py` (e.g. package counts, battery level, CPU specs, kernel version) were forced into `general_question` where the LLM refused with generic advice.
   - **Fix**: Added `system_inspect` intent across `classifier.py`, `classifier_tools.py`, and `orchestrator.py`. Connected `command_verifier.py` to validate generated bash pipelines against risk rules and execute read-only commands (`dpkg`, `uname`, `lscpu`, `free`, `ip`, `ps`, `wc`, `grep`, etc.). Persisted successful queries to `data/dynamic_system_tools.json` and hot-rebuilt router utterances, enabling sub-50ms resolution on subsequent calls without LLM command regeneration.

18. **Desktop Application Discovery, XDG Resolution, Argument Extraction, and Terminal Output Suppression**:
   - **Issue**:
     (a) `open_application` relied solely on `shutil.which(app_name)`, failing on common desktop applications where binary names differ from GUI names (e.g. "App Center" -> `/snap/bin/snap-store`, "Files" -> `/usr/bin/nautilus`, "VS Code" -> `code`), desktop names containing spaces/capitalization, and virtual system targets like "trash" (`gio open trash:///`).
     (b) `_extract_application_name` in `orchestrator.py` truncated multi-word application names, parsed `"open app center"` as `'center'`, and did not strip conversational prefixes (e.g. `"its App center"`).
     (c) Background desktop GUI processes (such as Brave, Chrome, Snap apps) inherited `stdout`/`stderr` file descriptors, causing GTK module warnings, Gtk-Message notices, and Chromium diagnostic logs to pollute the interactive CLI prompt.
   - **Fix**:
     (a) Added `_get_desktop_entries()` to scan and index XDG `.desktop` files across `/usr/share/applications`, `/var/lib/snapd/desktop/applications`, `~/.local/share/applications`, etc.
     (b) Implemented `_resolve_application()` in `system_agent.py` supporting virtual system targets (trash), common desktop aliases (files, appcenter, terminal, calculator, vscode), exact and normalized `.desktop` application name matching, generic name matching, and PATH fallbacks.
     (c) Updated `_extract_application_name()` in `orchestrator.py` to preserve multi-word application names, strip conversational prefixes (`"its "`, `"no, "`, `"i mean "`), and trim trailing app descriptors.
     (d) Redirected `stdout`, `stderr`, and `stdin` to `subprocess.DEVNULL` in `subprocess.Popen` within `open_application()`, ensuring clean CLI output when background GUI applications are launched.
     (e) Added unit tests in `tests/test_open_application.py` and configured `pytest.ini` for reliable test discovery.




## Notable changes added during the Astro/ambiguity debugging pass

## Recent routing fix

- Fixed an unsupported-control routing bug where "stop the music played in
  Spotify" was classified as `correct_fact`, causing an unrelated stored fact
  to be deleted and replaced with the command text. The classifier now detects
  unsupported media and application-control commands before model
  classification and routes them to `unsupported`.
- Added a regression test ensuring the Spotify request cannot enter the fact
  correction path.

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
  memory) are built and confirmed working. Phase 5 now uses the same explicit
  local CPU embedding model as semantic-router; acceptance testing confirmed
  user isolation, domain isolation, content-type filtering, and
  ownership-aware deletion in a temporary ChromaDB database.
- Phase 6 (memory integration, session history, fact canonicalization,
  classifier-based routing, fact correction) is built and re-tested after the
  ambiguity/correction fix pass.
- The recent ambiguity/tone pass is complete and verified: short gratitude
  phrases like "okay thank you" no longer trigger `correct_fact`, and the
  literal "astro" clarification flow now resolves the meaning before answering.
- `coding_agent.py` has focused verification coverage for Python syntax and
  bubblewrap execution. Its generation loop now uses `llm_provider`, retries a
  failed generated patch once, and is dispatched by `coding_task`; live
  provider calls and repository patch application still need testing/building.
- Bubblewrap namespace/resource setup errors are now reported as sandbox
  unavailability rather than generated-code failures, so infrastructure
  failures do not trigger a pointless second generation attempt. A harmless
  sandbox run was re-tested successfully after this change.
- A live provider smoke test reached the fallback chain but ended at local
  Ollama after Gemini returned a model 404, Groq and Cerebras returned 404
  responses, NVIDIA timed out, and OpenRouter was reported unconfigured. The
  OpenRouter mismatch was caused by `.env` using `OPENROUTER_KEY` while the
  adapter expected `OPENROUTER_API_KEY`; both names are now accepted. The
  remaining provider responses need separate model, endpoint, and credential
  verification. Any credentials exposed during testing must be rotated.
- Provider exception logging now redacts API-key query parameters and known
  configured provider-key values before writing errors to the structured log.
- Provider request compatibility was updated from the supplied provider
  examples: Cerebras now defaults to `gpt-oss-120b`, and OpenRouter accepts an
  explicit `OPENROUTER_MODEL` (default `~openai/gpt-latest`) and sends its
  optional `HTTP-Referer` and `X-OpenRouter-Title` headers. The OpenRouter
  endpoint is reachable when it returns `429`; that indicates rate limiting,
  not an invalid URL. Gemini's model 404 and remaining provider failures still
  require independent account/model verification.
- `llm_provider.py` supports task-aware provider ordering and global/per-call
  local-only operation. Coding messages currently pass unchanged through a
  placeholder boundary reserved for the future LLM-based security scanner.
  Use `ALLOW_CLOUD_CODING=false` for local-only coding until that scanner is
  implemented; `ALLOW_CLOUD=false` or `force_local=True` skips all cloud
  providers.
- Coding requests now pass through the tier gate before generating and running
  code in the resource-limited sandbox; repository patching still requires a
  future explicit approval workflow.
- Detailed process analysis now returns memory, CPU, and running-time data for
  Llama to interpret, while `open_application` launches PATH-resolved apps as
  a Tier 1 reversible action.
- Dynamic coding commands now have an additional `command_verifier.py` layer
  before tier-gate handling. Read-only commands use `shell=False`; destructive
  dry-run isolation remains a planned follow-up.
- Coding sandbox execution now passes through the tier gate, with OS-level CPU,
  memory, process, and file-size limits in addition to its wall-clock timeout.
- General-question answering now identifies whether the latest user message
  answers Zedek's previous question or starts a new topic, and keeps the
  assistant's identity separate from stored user facts to reduce blended or
  contradictory replies.
- Step 2 Coding Specialist is fully implemented and tested (35 unit tests in `tests/test_coding_agent.py`
  plus 34 hybrid router/ambiguity tests = 69 total tests passing, 0 regressions).
- Three-checkpoint approval workflow verified in `orchestrator.py`: (1) Tier gate, (2) Plan approval,
  (3) Pre-apply confirmation showing code diff/preview and LLM review verdict.
- Safe file application implemented with automated timestamped backups in `.backups/` and strict write boundary
  enforcement (`/home/nithiish/Documents/ai_assistanceworkspace`).
- Bubblewrap sandbox execution supports both fully isolated and project-aware execution (read-only mount of Documents
  directory and optional network capability for API testing).

## Completed in the current iteration (do not repeat)

- Replaced the DeBERTa zero-shot classifier with `semantic-router` using the
  local CPU embedding model `sentence-transformers/all-MiniLM-L6-v2`.
  `classifier.py` now defines explicit route utterance lists for every
  existing intent and for the personal/academic domain split. Starter
  utterances are intentionally easy to replace with examples from real use.
  The repository currently has 12 intent labels (including
  `general_question`), despite earlier notes referring to 11.
- The semantic-router layers now initialize at classifier startup on CPU.
  The 11 actionable categories are routes; `general_question` is returned as
  the fallback when no route clears the configured 0.45 similarity threshold.
  Static `RouteChoice` results in semantic-router 0.0.72 omit their retrieval
  score, so matched routes expose the configured threshold through the legacy
  `score` field while unmatched routes retain score 0.0.
- `setup.sh` now performs the one-time CPU download/cache of
  `sentence-transformers/all-MiniLM-L6-v2` after installing dependencies, and
  `classifier.py` reports a direct setup instruction if that cache is missing.
- The embedding cache now lives in the project-local ignored `models/`
  directory. Both classifier and Chroma memory load that same local path,
  avoiding separate Hugging Face cache locations and Chroma's default ONNX
  download. The cache was populated and both modules were verified importing
  with `HF_HUB_OFFLINE=1`.
- Pinned `posthog==3.5.0` because ChromaDB 0.5.20 uses PostHog's legacy
  three-argument `capture()` API; newer PostHog 7.x emits a telemetry warning
  even when Chroma anonymized telemetry is disabled.
- Completed Phase 5 memory hardening: ChromaDB now receives embeddings from a
  shared semantic-router `HuggingFaceEncoder` on CPU instead of downloading
  its separate default ONNX model. Reserved identity metadata cannot be
  overwritten, inputs are validated, and deletion checks `user_id` ownership.
- Broadened the `open_application` semantic-router examples so generic app
  requests such as "open Brave application" reach the existing allowlisted
  launcher. Application closing remains intentionally unsupported because no
  safely gated close function exists yet.
- Verified the application routing regression: six focused tests pass. The
  launcher still requires the requested executable to be discoverable through
  `PATH`; this environment currently exposes neither `brave` nor
  `brave-browser`. Closing applications remains unsupported by design.
- Narrowed the ambiguity guard so meta-questions about ambiguous terms, such
  as asking which other words could be confused, proceed to normal Q&A instead
  of receiving the canned `astro` clarification prompt. Added a regression
  test for the exact user wording.
- `llm_provider.py` now logs the effective `ALLOW_CLOUD` and
  `ALLOW_CLOUD_CODING` settings at startup and records when a request is routed
  directly to local Ollama, making accidental cloud-mode configuration visible.
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
- **Hybrid Architecture with Dynamic Online Learning fully implemented** (this iteration):
  - Created `classifier_tools.py` — `ROUTER_TOOLS` JSON schema and `VALID_INTENT_NAMES`
    frozenset covering all 12 intent categories. This is the function-calling contract
    used by the LLM escalation path. It must stay in sync with `INTENT_UTTERANCES`.
  - Rewrote `classifier.py`: raised both `ROUTE_THRESHOLD` and `CONFIDENCE_THRESHOLD`
    from 0.45 → **0.65** per the architecture spec. Added `_load_dynamic_utterances()`,
    `_save_dynamic_utterances()`, `add_utterance_dynamically()` (with quality guardrail:
    > 15 words or `general_question` → skip), and `_build_intent_router()` now merges
    static + dynamic utterances before constructing the `RouteLayer`. Added
    `query_llm_with_tools()` for Layer 2 LLM escalation via structured JSON prompt +
    `json_mode=True`. `classify_intent()` now returns a `via_llm` flag and routes
    through the complete two-layer cascade with the dynamic feedback loop.
  - Updated `orchestrator.py`: `route_request()` captures and logs the new `via_llm`
    flag from `classify_intent()` and passes it through in the routing decision dict.
  - Created `tests/test_hybrid_router.py`: **34 tests, all passing** (27 new hybrid
    tests + 7 regression tests). All LLM calls mocked for offline execution. Covers
    Layer 1 local hits without LLM involvement, Layer 2 LLM escalation, dynamic
    persistence (save/dedup/hot-reload), 15-word quality guard, `general_question`
    exclusion, and `ROUTER_TOOLS` schema integrity.

- **Coding Specialist + Verifier Loop (SWE-agent Pattern) fully implemented** (this iteration):
  - Rewrote `coding_agent.py`:
    - **Path boundaries & security**: Set `CODING_READ_ROOT = "/home/nithiish/Documents"` and `CODING_WRITE_ROOT = "/home/nithiish/Documents/ai_assistanceworkspace"`. Path traversal blocked with strict validation. 50KB file size safety cap.
    - **Phase 2A (Context-aware planning & patching)**: `plan_task()` auto-discovers relevant files or accepts explicit paths, gathering project context for the planning prompt. `patch()` produces structured patches targeting write workspace files.
    - **Phase 2B (Test generation & execution)**: `generate_tests()` synthesizes isolated Python unit assertions. `SandboxedPythonRunner` extended with `project_aware=True` (read-only mount) and `allow_network=True` (for API testing).
    - **Phase 2C (LLM-assisted verification)**: `Verifier.review_with_llm()` uses `task="general_qa"` to obtain a genuine independent second-opinion review on code correctness and safety.
    - **Phase 2D & 2E (Orchestrator integration & safe application)**: Added 3-checkpoint approval workflow in `orchestrator.py`. `apply_patch()` creates timestamped backups in `.backups/` and writes only after explicit user confirmation.
    - **Created `tests/test_coding_agent.py`**: 35 comprehensive unit tests (all passing). Full test suite now at **69 passed tests**.

## Next steps (in the order previously agreed, now continuing from the current state)

1. ~~**Test provider-backed coding generation** with real free-tier API keys.~~ ✅ **DONE** (2026-08-25) — all providers verified working.
2. ~~**Coding specialist + verifier loop (OpenHands / SWE-agent pattern)**~~ ✅ **DONE** (2026-08-25) — SWE-agent pattern fully built and verified without external dependencies.
3. **MCP client support (COMPLETED)** — Model Context Protocol client layer implemented
   following a strict boundary 10/10 architecture:
   - `mcp_client.py`: Strict boundary preventing any MCP SDK/asyncio objects from escaping.
     Internal dataclasses (`MCPServerConfig`, `MCPToolSpec`), internal error taxonomy,
     isolated `_TOOL_REGISTRY` & `_SERVER_REGISTRY`, robust cache lifecycle with
     (mtime, size) fingerprinting, sorted deterministic discovery, per-server timeout
     isolation, automatic Python environment resolution (`sys.executable`), process
     cleanup, and normalized result dicts (`{"result": ..., "error": ...}`).
   - `mcp_demo_server.py`: Pure Python MCP server (`mcp.server.mcpserver.MCPServer` +
     `run_stdio_async`) exposing `current_time`, `word_count`, and `summarize_text`.
   - `mcp_servers.json`: Declarative configuration file for MCP servers.
   - `tier_gate.py`: Dynamic Tier 1 default for `mcp_*` tools with universal
     force-pattern escalations (Tier 2/3) enforced on top.
   - `classifier.py` & `classifier_tools.py`: Added `mcp_tool` intent route and
     reversible `register_mcp_tools(tools)` for hot-reloading router embeddings from tool descriptions.
   - `orchestrator.py`: Startup discovery, schema-driven argument extraction, JSON schema
     validation via `jsonschema`, and `_execute_mcp_tool()` with internal tier gate enforcement.
   - `tests/test_mcp_client.py`: 25 comprehensive unit and real-STDIO integration tests.
     All 115 total tests in the project suite pass (152 after Phase 5 below).
4. **Read-only online MCP server (COMPLETED)** — `mcp_online_server.py` adds live
   internet access to the assistant via three pure-Python, read-only MCP tools:
   - `fetch_url(url)`: HTTP GET any public URL; returns clean stripped text.
     SSRF-guarded: all private/loopback/link-local ranges (127/8, 10/8, 172.16/12,
     192.168/16, 169.254/16, IPv6 loopback/ULA/link-local) and `localhost` are
     blocked before any I/O. Fail-closed on DNS failure.
   - `search_wikipedia(query, limit)`: Wikipedia OpenSearch + REST Summary API.
   - `search_arxiv(query, max_results)`: arXiv Atom feed; returns titles, authors,
     published dates, and abstracts.
   All tools: explicit `httpx.Timeout(connect=10, read=20)` independent of MCP server
   timeout; identifying `User-Agent` header per Wikipedia/arXiv API ToS; graceful
   `[error]`/`[blocked]` string returns — no exceptions propagate to caller.
   Registered as `online_tools` in `mcp_servers.json` (array schema, timeout 15 s).
   `tests/test_mcp_online.py`: 37 tests covering SSRF blocklist, HTML helpers,
   tier-gate no-false-escalation (including `write`-in-query collision regression test),
   and mocked HTTP responses for all three tools. Full suite: **152 passed, 0 failures**.
   ~~**Next (separate pass):** verb-escalation expansion in `tier_gate.py`~~ — **COMPLETED** (see item 5 below).
5. **Tier gate verb-escalation, description inspection & `default_tier` precedence (COMPLETED)**
   Prerequisites satisfied: Puppeteer/Playwright/GitHub-write can now be safely wired in.
   - `tier_gate.py`: Added `MCP_ACTION_VERB_PATTERNS` (11 action verbs: click, submit,
     type into, press, send, purchase, confirm, write to, commit, push, patch) compiled
     with `\b...(s|es|ed|ing)?\b` + `IGNORECASE` — matches inflected forms (`clicks`,
     `submits`, `sends`) while blocking substring collisions (`dispatch`≠`patch`,
     `compress`≠`press`, `committee`≠`commit`).
   - `_extract_effect_description()`: strips `Args:`/`Parameters:` blocks before verb
     matching — verbs in parameter descriptions never trip escalation.
   - `_server_default_tier()`: lazy-imports `mcp_client`, resolves owning server by
     qualified-name prefix, returns its `default_tier`. Scoped to `mcp_*` only — non-MCP
     functions use `FUNCTION_TIERS` exclusively, preventing cross-contamination.
   - `classify()`/`gate()`: additive `tool_description=""` parameter (backward-compatible);
     four independent lanes all feed into `max()` — pattern-from-args/user-input,
     effect-verb-from-description, server-default-tier, FUNCTION_TIERS/mcp-base. Each lane
     can only raise the tier, never lower it.
   - `mcp_client.py`: `default_tier: int` field on `MCPServerConfig`, parsed from
     `mcp_servers.json` with validation and fail-safe to 1. New `get_server_registry()`
     public accessor (same copy-return pattern as `get_tool_registry()`).
   - `orchestrator.py`: one-line change — `tool_spec.description` threaded into `gate()`.
   - `tests/test_tier_gate_verbs.py`: 36 tests — three critical cases (collision-avoidance,
     genuine verb escalation, monotonic `default_tier` precedence), substring regression
     (`dispatch`/`compress`/`committee`/`progress`), per-verb coverage, backward-compat.
   Full suite: **188 passed, 0 failures** (was 152).
6. **Expanded MCP Tool Servers Ecosystem (COMPLETED)**
   Added 5 new MCP servers (bringing the total to **27 tools across 7 servers**):
   - **`playwright_tools`** (`mcp_playwright_server.py`): Browser automation tools (`browser_navigate`,
     `browser_click`, `browser_type`, `browser_get_text`, `browser_screenshot`). Configured with
     `"default_tier": 2` in `mcp_servers.json`, full SSRF blocklist, `--no-sandbox` headless execution.
     Verified live with E2E confirmation gate and live Chromium click execution on local test page.
   - **`github_tools`** (`mcp_github_server.py`): Read-only repo search (`github_search_repos`),
     code search (`github_search_code`), metadata (`github_get_repo`), README retrieval (`github_get_readme`).
     Supports `GITHUB_TOKEN` for 5000 req/hr (gracefully falls back to 60 req/hr without token).
   - **`weather_news_tools`** (`mcp_weather_news_server.py`): Real-time weather (`get_weather`,
     `get_weather_forecast` via public wttr.in) and news headline search (`search_news` via NewsAPI).
   - **`codeforces_tools`** (`mcp_codeforces_server.py`): Competitive programming tools for placement prep
     (`cf_user_info`, `cf_user_submissions`, `cf_problem_search` with tag/rating filtering,
     `cf_contest_list`, `cf_problem_by_id`). Fully public Codeforces API.
   - **`semantic_scholar_tools`** (`mcp_semantic_scholar_server.py`): Academic paper research
     (`scholar_search_papers`, `scholar_paper_details`, `scholar_paper_citations`, `scholar_author_papers`).
   - `tests/test_mcp_expansion_servers.py`: Unit and mock tests for all expansion servers.
7. **Tiered & Isolated Sandboxed Execution Engine (COMPLETED)**
   Built dedicated [`sandbox_runner.py`](file:///home/nithiish/Desktop/aiassistancesystem/sandbox_runner.py) to support full test suites and snippets:
   - **Tiered Backend Architecture**: Linux namespace isolation with `bwrap` when permitted;
     graceful fallback to Process Isolation with OS resource limits (`setrlimit` CPU, AS 512MB,
     NPROC 64, FSIZE 10MB) + clean ephemeral directories on AppArmor-restricted kernels.
   - **Multi-Mode Support**:
     - `SNIPPET`: Stateless Python string execution in empty temp directory.
     - `PROJECT_READ_ONLY`: Project files mounted/read-only with PYTHONPATH configured.
     - `PROJECT_COPY_ON_WRITE`: Fast workspace mirroring for isolated test runs (`pytest`, `unittest`)
       ensuring tests, artifacts, and bytecode caches never pollute or mutate the original repository.
   - Integrated into `coding_agent.py`'s `SandboxedPythonRunner` with full backward compatibility.
   - `tests/test_sandbox_runner.py`: 9 tests verifying syntax pre-checks, snippet runs, timeout protection,
     and copy-on-write isolation guarantees.
8. **Evaluator & Verifier Agent with Three-Pillar Architecture (COMPLETED)**
   Built dedicated [`evaluator_agent.py`](file:///home/nithiish/Desktop/aiassistancesystem/evaluator_agent.py) to catch hallucinations, logic errors, regressions, and security violations:
   - **Pillar 1: Static & Security Analysis**: AST validation, forbidden import checks (`pty`, `posix`, etc.),
     dangerous command pattern checks, and AST scope diffing against original code to prevent accidental function deletion.
   - **Pillar 2: Dynamic Execution Verification**: Runs self-contained test assertions or pytest suites
     in isolated copy-on-write sandboxes via `SandboxRunner`.
   - **Pillar 3: Dual-Model LLM Intent & Correctness Evaluation**: Added `"evaluation"` task profile to
     `llm_provider.py` (`["gemini", "groq", "cerebras", "openrouter", "local"]`) to ensure the reviewer
     uses a distinct model family from the code generator (`"coding"` starts with `"nvidia_nim"`/`"openrouter"`).
   - Generates structured `EvaluationReport` (`is_approved`, `verdict: accept/retry/reject`, `score`, `issues`, `remediation_advice`).
   - Integrated into `coding_agent.py` (`Verifier` and `CodingSpecialist.implement_and_verify()` retry loop).
   - `tests/test_evaluator_agent.py`: 14 tests covering all three pillars, schema validation, and pipeline integration.
   Full test suite: **221 passed, 0 failures** (was 207).
9. **Task planner / decomposer agent (COMPLETED)** — [`task_planner.py`](file:///home/nithiish/Desktop/aiassistancesystem/task_planner.py)
   breaks a bundled request into ordered atomic sub-requests before execution.
   - `should_decompose()`: regex-only heuristic (no LLM call) biased toward false
     negatives — sequence words, semicolons, numbered lists, or 3+ action verbs
     trigger it. A bare "and" ("open brave and vscode") deliberately does NOT.
   - `decompose()`: LLM pass (`task="planning"`) returning atomic sub-requests, capped
     at 5. Degrades to a punctuation split, then to the unchanged original — it never raises.
   - `{{result_of_N}}` placeholders let a later sub-task consume an earlier one's answer.
   - `orchestrator.handle()` refactored into `_handle_single()` / `_handle_decomposed()`;
     each sub-task runs through the normal routing + tier gate path, nothing bypasses it.
   - `tests/test_task_planner.py`: 19 tests. Suite: **240 passed**.
10. **Remaining specialist agents (COMPLETED)**
    - **Research / RAG agent** ([`research_agent.py`](file:///home/nithiish/Desktop/aiassistancesystem/research_agent.py)):
      plan queries → gather → synthesize with `[S1]`-style inline citations.
      Sources: ChromaDB memory + Wikipedia/arXiv/Semantic Scholar/direct URL fetch.
      Explicit read-only tool allowlist; **every** MCP call passes `gate()`, and a
      blocked *or* confirmation-gated tool is skipped rather than auto-approved
      (research runs unattended, so it must never self-confirm). Zero sources ⇒ it
      says so instead of answering from model recall. New `"research"` task profile.
      Intent `research_task` registered in `classifier.py` + `classifier_tools.py`.
      `tests/test_research_agent.py`: 27 tests.
    - **Web / browser agent** ([`web_agent.py`](file:///home/nithiish/Desktop/aiassistancesystem/web_agent.py)):
      bounded observe → act loop over the Playwright MCP tools. Note those tools are
      **stateless** — each call reloads the page from an explicit URL — so the loop is a
      sequence of self-contained operations, not a session walk. Browser-tool allowlist,
      http(s)-only URL validation ahead of the gate, and a 4-step budget. Tier 2
      confirmation is **injected** (`confirm_fn`); the default callback denies and the
      orchestrator passes the interactive prompt, so the agent can never self-approve.
      A denied or blocked action ends the loop rather than routing around the refusal.
      Intent `web_task` registered. `tests/test_web_agent.py`: 28 tests.
11. **Watchdog module (COMPLETED)** — [`watchdog.py`](file:///home/nithiish/Desktop/aiassistancesystem/watchdog.py)
    answers the question the tier gate cannot: *is this the action the agent said it
    would take?* An agent that declares a read-only plan and then writes elsewhere
    trips no single high-tier action, but has deviated from stated intent.
    - `register_plan()` / `observe()`: an undeclared action at Tier 2+ is refused; an
      undeclared read-only action is allowed but flagged. An unknown plan fails closed.
    - Tier 3 two-checkpoint flow (`checkpoint_prefill` → `checkpoint_presubmit`):
      pre-submit **requires** pre-fill, and while `TIER3_EXECUTION_ENABLED = False`
      it refuses even on approval — matching the tier gate's system-wide Tier 3 block.
    - Append-only JSONL audit trail at `logs/watchdog_audit.jsonl`, with sensitive
      argument values (cvv, ssn, password, token…) redacted before they hit disk.
      Audit write failures degrade observability, never execution.
    - **Wired into the coding flow**: the approved plan's declared file writes are
      registered, and a patch targeting an undeclared file is blocked *before* the
      apply prompt.
    - Honest scope note: this runs in-process. It is written as pure state + an
      append-only trail with no orchestrator dependency so it *can* move
      out-of-process later, but no IPC boundary exists today.
    - `tests/test_watchdog.py`: 26 tests.
12. **Security module (COMPLETED — except the audio backend)** —
    [`security_module.py`](file:///home/nithiish/Desktop/aiassistancesystem/security_module.py),
    four independent fail-closed pieces:
    - `SecureStore`: Fernet encryption with a PBKDF2 (480k iterations) key derived from
      a master password, per-store random salt, 0600 file mode. Tests assert the
      plaintext canary never appears in the on-disk vault.
    - `PasswordGate`: PBKDF2 hash only — the password itself is never stored —
      `hmac.compare_digest` comparison, lockout after 5 failures.
    - `ConfirmationWord`: rotating, **single-use**, expiring word. Rationale: a plain
      "y" can be satisfied by an agent loop, a replay, or a stray keystroke; a word
      that changes every use and is consumed on *both* success and failure cannot be
      pre-computed or replayed.
    - `VoicePrintVerifier`: interface + enrollment flow + fail-closed contract are
      implemented and tested. **The audio backend is NOT installed** (`resemblyzer`,
      `sounddevice` absent), so `available()` is False and verification returns
      `"unavailable"` — it never degrades into an accidental allow. Wiring a real
      backend means implementing `_embed()`; the surrounding guarantees already hold.
    - `tests/test_security_module.py`: 45 tests (security invariants, not feature tests).
13. **Wake-word general Q&A mode (COMPLETED — except the audio backend)** —
    [`wake_word.py`](file:///home/nithiish/Desktop/aiassistancesystem/wake_word.py).
    - Wake word with STT-variant tolerance (`zedeck`, `zedak`, `sedek`…), matched only
      at utterance start so ordinary mentions don't activate it. 30-second activation
      window that refreshes on each handled utterance; sleep phrases close it.
    - **Critical boundary — Q&A only**: the default handler routes to
      `orchestrator.answer_general_question()`, bypassing `execute()` entirely. There
      is no code path from a spoken utterance to a system action, because speech is a
      low-confidence channel (STT mishears; anyone in earshot can speak). Tests assert
      `execute()` is never called even for action-phrased utterances.
    - Kept separate from the security voice channel by design: hearing the wake word
      proves *someone* spoke, not *who* — it is never an authorization signal.
    - No capture backend installed, so `listen_forever()` refuses loudly rather than
      looking like a listener that simply never hears anything. The utterance-processing
      core is backend-independent and fully tested via `process_utterance()`.
    - `tests/test_wake_word.py`: 26 tests.
14. **Academic / placement-prep tracking (COMPLETED)** — the "personal tutor" use case,
    [`academic_tracker.py`](file:///home/nithiish/Desktop/aiassistancesystem/academic_tracker.py).
    - Its own structured JSON store (`data/academic_progress.json`), deliberately NOT
      ChromaDB: practice history is numeric and queried by aggregation, and semantic
      similarity is the wrong retrieval model for "accuracy per topic".
    - Weakness score = 0.55·inaccuracy + 0.30·staleness + 0.15·low-volume, where
      accuracy is **Laplace-smoothed** so one bad day doesn't permanently pin a topic
      to the top, staleness surfaces topics that were strong but have gone cold, and
      low volume nudges barely-tried topics up. Every recommendation reports which
      factor dominated, so it explains itself instead of just ranking.
    - Cold start returns "no data, that would just be a guess" rather than inventing
      weak topics. A corrupt progress file degrades to empty instead of crashing.
    - Practice streaks count yesterday as still-alive (the day isn't over).
    - Intent `academic_tracking` registered; `orchestrator._handle_academic_tracking()`
      extracts log-vs-review intent and **defaults to review on any extraction failure**
      — reading progress is harmless, guessing at a log entry would corrupt the history.
    - `tests/test_academic_tracker.py`: 37 tests.

### Codebase health pass (done alongside Items 9–14)

- **Real bug fixed**: `evaluator_agent.py` referenced `os.path.isdir()` without ever
  importing `os` — a guaranteed `NameError` on every project-root verification run.
  Pillar 2's project-level test-suite path had been dead on arrival.
- **Real bug fixed**: the user-approved coding plan was being thrown away —
  `implement_and_verify()` called `plan_task()` again internally, so the user approved
  plan A while the agent generated code against a freshly regenerated plan B. It now
  accepts the approved plan and reuses it.
- **Signature mismatch caught pre-merge**: `research_agent` initially called
  `scholar_search_papers` with `limit` instead of its actual `max_results` parameter.
- Dead imports/variables removed across 8 modules; pyflakes is clean apart from three
  harmless redundant `global` declarations (mutation-only, left alone deliberately).
- The cross-file `INTENT_UTTERANCES` ⇄ `ROUTER_TOOLS` consistency test caught each new
  intent that was registered in one file but not the other — it did its job three times.

### Architecture guidance to keep in the system design

- Use the OpenHands / SWE-agent pattern as a blueprint for the coding layer:
  plan → read context → patch → run tests → fix failures → verify.
  **Do not integrate OpenHands as a dependency** — build the pattern inside
  `coding_agent.py` using the existing provider chain and bubblewrap sandbox.
- Keep the current personal-assistant architecture as the top-level orchestrator;
  do not replace it with a fully autonomous coding bot.
- Keep the safety and tier gate in front of all execution steps, even for the
  specialist coding agent.
- Treat the verifier as non-optional: any code-generation path should have a
  second pass that checks correctness, not just output style.
- Keep the multi-agent decomposition lightweight and explicit; a full social
  "company-of-agents" structure is not required for this project's current
  goals and would increase complexity faster than value.
- **MCP client calls are subject to the same tier gate as all other actions.**
  An MCP tool that deletes files is still a destructive action, regardless of
  how it was discovered. The MCP client must translate tool metadata into
  tier-gate-compatible risk classifications before execution.
  - **Amended 2026-09-29: tool descriptions are UNTRUSTED input.** The original
    guidance above led to deriving risk escalation *from the description text*,
    which fails open — a server that describes a writing tool as "reads data"
    stays Tier 1. The same descriptions are also interpolated into an LLM prompt
    in `_select_mcp_tool()`, a documented tool-poisoning surface (Unit 42 measured
    a 78.3% attack success rate with five MCP servers on one agent; Zedek runs
    seven). Risk must come from a **pinned per-server `default_tier` treated as a
    floor**; description parsing may only ever *raise* a tier, never be the sole
    source of it. See ROADMAP B2.
- **Never let an LLM that chooses actions read untrusted content directly.**
  Web pages, fetched URLs, and tool descriptions go through a tool-less
  quarantine step that returns typed fields; the privileged planner sees only
  those fields. See ROADMAP B1.

> Important: the ambiguity-handling, gratitude guard, and tone adaptation pass
> is complete and should be treated as finished work. Do not reopen or repeat
> these fixes unless a new regression specifically reappears during testing.

## 2026-09-29 — External research pass + live audit

Three parallel research streams (agent architectures, security/compliance,
production engineering) plus a direct audit of the running system. The canonical
outputs now live in dedicated files — **read these before planning new work**:

- [`ROADMAP.md`](ROADMAP.md) — the plan. Supersedes `buildingroadmap.txt` (kept as history).
- [`TECH_STACK.md`](TECH_STACK.md) — every component with *why it was chosen over the alternative*.
- [`COMPLIANCE.md`](COMPLIANCE.md) — DPDP / EU AI Act position and the tripwires that change it.
- [`reports/Open source assistant improvements.md`](reports/Open%20source%20assistant%20improvements.md) —
  a second, cited survey of 13 open-source assistants (Open WebUI, Home Assistant Assist,
  OpenHands, Letta, OpenClaw, Goose, …) plus RAG-quality and efficiency research. It
  became ROADMAP **Phase F**. Headline finding: at Zedek's scale the bottleneck is
  *retrieval* (3 dense facts behind a fixed L2 cutoff; research pages cut off at 4k
  characters) and *wasted quota* (schema-less JSON; OpenRouter's 50-requests/day tier
  sitting first in `process_reasoning`). It is not the model or the framework.
  Best-value fix: a CPU cross-encoder reranker (+31 ms measured).

### What the audit found in the live system

**Memory was badly corrupted.** Of 135 stored facts, roughly half were not facts:
22 were the LLM's own preamble line (`"Here are the extracted facts:"`), 40 had
unstripped markdown bullets, 19 were duplicates (`User's name: Nithiish` appeared
four times), and several held null or placeholder values (`User's college: <new value>`,
`User's educational institution: not stated`). Semantic search returns its top-k
regardless of quality, so this junk was reaching the model as ground truth.
Most of it appears to be legacy — written by earlier code before the current
`startswith("User's")` filters existed — which is exactly why fixing the writer
alone was never going to be enough.
- **Fixed:** `memory_hygiene.py` gates every fact on the way into `memory.store()`,
  and `clean_store()` sweeps existing rows (deletes junk, **repairs** salvageable
  rows in place). 43 tests, every rejection case drawn from observed corruption.
- **Not yet applied to the live store** — the cleanup deletes the user's data, so it
  waits for his go-ahead. Dry run: 36 rows to delete, 37 to repair.

**`requirements.txt` had drifted.** It omitted `cryptography`, `httpx`, `playwright`,
and the entire audio stack. A fresh install would have broken the vault import.
Fixed; all pinned to installed versions.

### What the research confirmed Zedek already gets right

Worth recording, because it argues against churn:
- **Hand-rolled orchestration is correct for this profile.** The 2026 trend is away
  from frameworks for single-user interactive assistants; LangGraph's real value is
  durable multi-day execution, which Zedek doesn't need, at a 30–80ms/call tax.
- **The hybrid router is the recommended pattern**, not a stopgap — ~65× cheaper and
  ~50× faster than pure-LLM routing.
- **Security posture is ahead of comparable open-source assistants.** OpenClaw (~300k
  stars, the closest comparable) shipped without encrypted credential storage and had a
  third-party skill found exfiltrating data. Zedek has an encrypted vault, explicit
  allowlists, and a tier gate that model judgment cannot lower.
- **Injected, deny-by-default confirmation** in the web agent is exactly the control
  that browser agents which failed in public (e.g. one letting through 97 of 103
  phishing attempts) lacked.

### What the research found genuinely behind

In priority order (details and rationale in ROADMAP.md):
1. **Prompt-injection hole in `web_agent`** — the planner reads raw page text and then
   chooses the next tool. Verified in code (`web_agent.py:150`). → B1
2. **MCP descriptions trusted** in the selection prompt and the tier gate. Verified in
   code (`orchestrator.py:521`, `tier_gate.py:151`). → B2
3. **No routing evaluation** despite routing being the historical failure point. → A4
4. **No bi-temporal facts** — no way to represent "this stopped being true", which is
   the structural cause of the contradictory-fact bug. → A2
5. **No tracing or provider accounting** across a five-provider fallback chain. → C1, C2
6. **`llama3.1:8b` is a dated local fallback**; Qwen3 8B is the same footprint and
   better at the structured extraction the fallback path does. → D3

## User's stated priorities, in their own words (for judgment calls)

- Accuracy over speed/latency, explicitly, more than once.
- Local models are a backup for no-internet situations only — most usage
  is expected to be cloud-backed, free-tier. and also manual turn of cloud service incase of problem or token issue
- Prefers narrow, single-responsibility components over one model doing
  many jobs — this preference directly drove the classifier/Llama split.
- Wants to review and test every change personally — don't skip verification
  steps or assume something works without the user confirming test output.
- Explicitly not doing Tier 3 (payment/high-risk) tasks right now, but wants
  the framework built and ready for when that changes.