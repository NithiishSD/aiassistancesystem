"""
Phase 6: Orchestrator with tier gate + memory integration.

Takes a text request, asks Llama3.1 (local) to decide which system_agent
function to call (if any), with what arguments, and which domain it belongs
to. Validates the choice against the allowlist, passes it through the tier
gate (Phase 4) for classification and confirmation, executes it, and
returns a plain-language answer. General questions (no matching function)
are answered using relevant memory (Phase 5) retrieved for context. Every
turn — both the user's input and Zedek's answer — is auto-stored into
memory afterward.

Pipeline: request -> intent + domain -> tier gate -> execute or retrieve+answer -> store turn -> answer.
"""

import json
import re
import ollama
from zedek_logger import get_logger
from system_agent import AVAILABLE_FUNCTIONS
from tier_gate import gate
import memory
import classifier
import llm_provider
from coding_agent import CodingSpecialist

log = get_logger("orchestrator")

ROUTING_MODEL = "llama3.1:8b"
CODING_SPECIALIST = CodingSpecialist()

# --- Short-term session context (this run only, NOT persisted to disk) ---
# Separate from memory.py's long-term ChromaDB store. This holds the last
# few raw turns of THIS conversation so immediate follow-ups work correctly,
# without relying on semantic search to "guess" what you just said.
SESSION_HISTORY: list[dict] = []
MAX_SESSION_TURNS = 10  # last 10 messages (~5 exchanges)


def summarize_and_flush_session(domain: str = "personal") -> None:
    """
    Reviews the current session buffer, extracts only what's genuinely worth
    remembering long-term (new facts, decisions, preferences), and stores
    ONLY that distilled summary to ChromaDB — not the raw conversation.
    Called when the session buffer fills up, or when the session ends.
    Clears SESSION_HISTORY afterward.
    """
    global SESSION_HISTORY

    if not SESSION_HISTORY:
        return

    transcript = "\n".join(f"{turn['role']}: {turn['content']}" for turn in SESSION_HISTORY)

    prompt = f"""Below is a conversation transcript. Extract ONLY genuinely useful
long-term facts worth remembering (new personal/academic facts, stated preferences,
decisions) — ignore routine queries and their answers (e.g. disk space checks,
one-off lookups) that have no lasting value.

Respond with each fact on its own line in the format "User's <attribute>: <value>".
If nothing is worth remembering, respond with exactly: NONE

Transcript:
{transcript}"""

    response = ollama.chat(model=ROUTING_MODEL, messages=[{"role": "user", "content": prompt}])
    extracted = response["message"]["content"].strip()

    if extracted.upper() == "NONE" or not extracted:
        log.info("session_flush_nothing_worth_storing", extra={"turns_reviewed": len(SESSION_HISTORY)})
    else:
        facts = [line.strip() for line in extracted.split("\n") if line.strip()]
        for fact in facts:
            memory.store(fact, domain=domain, content_type="fact")
        log.info("session_flush_stored", extra={"facts_stored": len(facts), "turns_reviewed": len(SESSION_HISTORY)})

    SESSION_HISTORY = []


def _add_to_session(role: str, content: str) -> None:
    SESSION_HISTORY.append({"role": role, "content": content})
    if len(SESSION_HISTORY) > MAX_SESSION_TURNS:
        log.info("session_buffer_full_flushing", extra={"turns": len(SESSION_HISTORY)})
        summarize_and_flush_session()

def tone_for_prompt(user_input: str) -> str:
    """Map user wording into a matching conversational tone."""
    lowered = user_input.lower()
    casual_markers = ["hey", "hi", "bro", "pls", "plz", "lol", "gonna", "wanna", "quick", "buddy", "yo"]
    if any(marker in lowered for marker in casual_markers):
        return "friendly, lightly playful, and casual"
    return "warm, clear, and conversational"

def detect_ambiguous_term(text: str) -> str | None:
    """Finds if a known ambiguous term exists in the text as a standalone word."""
    words = re.findall(r'\b\w+\b', text.lower())
    for term in classifier.AMBIGUOUS_TERMS:
        if term in words:
            return term
    return None

def is_term_already_specified(text: str, term: str) -> bool:
    """Checks if the user provided enough surrounding context to resolve the meaning."""
    text_lower = text.lower()
    
    # Specific context markers for common ambiguous terms
    context_signals = [
        "framework", "frontend", "language", "code", "course", "skill", 
        "study", "learning", "project", "subject", "lib", "library", "api",
        "space", "stars", "planet", "game", "metal", "banking"
    ]
    
    # If the user input is longer than 12 words, they are likely providing context, not asking a bare question
    if len(text.split()) > 12:
        return True

    return any(signal in text_lower for signal in context_signals)


def should_ask_ambiguous_term_question(user_input: str, recent_user_turns: list[str] | None = None) -> bool:
    """Only ask for clarification if a bare, under-specified ambiguous term is used."""
    text = user_input or ""
    term = detect_ambiguous_term(text)
    
    if not term:
        return False

    # If the prompt is a correction, statement, or highly detailed, do NOT block it with ambiguity questions
    correction_signals = ["no", "incorrect", "wrong", "mistake", "remove", "delete", "not part of", "correct"]
    if any(sig in text.lower().split() for sig in correction_signals):
        return False

    # If already specified by context in current turn
    if is_term_already_specified(text, term):
        return False

    # Check if previously clarified in recent turns
    if recent_user_turns:
        last = recent_user_turns[-1]
        if is_term_already_specified(last, term):
            return False

    return True


def should_treat_as_disambiguation(previous_input: str, current_input: str) -> bool:
    """Detect when the user is explicitly answering a prior ambiguity question."""
    curr = (current_input or "").lower()
    prev = (previous_input or "").lower()
    
    term = detect_ambiguous_term(prev) or detect_ambiguous_term(curr)
    if not term:
        return False

    clarification_signals = ["i meant", "i mean", "talking about", "actually", "referring to"]
    if any(sig in curr for sig in clarification_signals) and is_term_already_specified(curr, term):
        return True

    return False


def generate_ambiguity_reply(user_input: str, previous_input: str | None = None) -> str:
    """Produces a generalized clarification question based on the detected ambiguous term."""
    term = detect_ambiguous_term(user_input) or detect_ambiguous_term(previous_input or "") or "that term"
    meanings = classifier.AMBIGUOUS_TERMS.get(term, ["multiple different topics"])
    
    meanings_str = " or ".join(meanings)
    tone = tone_for_prompt(user_input)
    
    return (
        f"Hmm, '{term}' can refer to a few different things (like {meanings_str}) 😅. "
        f"Which specific one are you referring to so I can give you a {tone} and accurate response?"
    )

# NOTE: SYSTEM_PROMPT (the old classification prompt) has been removed —
# classification is now handled by classifier.py (DeBERTa zero-shot model),
# not Llama. See route_request() below.


def route_request(user_input: str) -> dict:
    """
    Phase 6.5: Uses the dedicated classifier (classifier.py) for intent +
    domain, NOT Llama. Llama is only invoked afterward, and only if a real
    function needs argument extraction (e.g. search_files needs a query).
    This is the narrowing-of-responsibility fix: classification and
    generation are handled by different, purpose-built models.
    """
    log.info("routing_started", extra={"user_input": user_input})

    normalized = (user_input or "").strip().lower()
    if normalized in {"ok", "okay", "alright", "thanks", "thank you", "thank u", "ty", "thx", "got it", "understood", "sounds good", "appreciate it"} or any(phrase in normalized for phrase in ["thank you", "thanks", "thank u", "thx", "ty", "got it", "understood", "appreciate it"]) and len(normalized.split()) <= 6:
        log.info("routing_acknowledgement_guard", extra={"user_input": user_input})
        return {"function": None, "domain": classifier.classify_domain(user_input), "confidence": "high", "score": 0.0, "args": {}, "clarify": False}

    if should_ask_ambiguous_term_question(user_input, [turn["content"] for turn in SESSION_HISTORY if turn["role"] == "user"]):
        return {"function": None, "domain": classifier.classify_domain(user_input), "confidence": "high", "score": 0.0, "args": {}, "clarify": True}

    intent_result = classifier.classify_intent(user_input)
    domain = classifier.classify_domain(user_input)

    func_name = intent_result["function"]
    confidence = intent_result["confidence"]
    via_llm = intent_result.get("via_llm", False)

    decision = {
        "function": func_name,
        "domain": domain,
        "confidence": confidence,
        "score": intent_result["score"],
        "via_llm": via_llm,  # True when Layer 2 LLM tool-calling was used
    }

    # Only real functions (not remember_fact/unsupported/None) need argument
    # extraction — and this is now a narrow, well-defined task for Llama,
    # not a classification decision.
    if func_name in AVAILABLE_FUNCTIONS:
        if func_name == "open_application":
            decision["args"] = {"app_name": _extract_application_name(user_input)}
        else:
            decision["args"] = _extract_args(func_name, user_input)
    else:
        decision["args"] = {}

    log.info("routing_decision",
             extra={"decision": {k: v for k, v in decision.items() if k != "args"},
                    "via_llm": via_llm})
    return decision


def _extract_application_name(user_input: str) -> str:
    """Extract the requested application without relying on an LLM."""
    text = re.sub(r"\s+", " ", (user_input or "").strip())
    text = re.sub(
        r"^(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|then\s+)?"
        r"(?:open|launch|start)\s+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+(?:application|app|program)$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,!?")


def _extract_args(func_name: str, user_input: str) -> dict:
    """
    Narrow, single-purpose Llama call: given a function is ALREADY decided,
    extract just its arguments from the user's text. This is a much easier
    task than classification and Llama is reliable at it.
    """
    arg_prompt = f"""Extract the arguments for the function "{func_name}" from this request.
Respond ONLY with a JSON object of argument names to values. If no specific arguments
are mentioned, respond with {{}}.

Request: {user_input}"""

    response = ollama.chat(
        model=ROUTING_MODEL,
        messages=[{"role": "user", "content": arg_prompt}],
        format="json",
    )
    try:
        return json.loads(response["message"]["content"])
    except json.JSONDecodeError:
        return {}


def _handle_correction(raw_text: str, domain: str) -> str:
    """
    Handles a fact correction/retraction. Finds the most likely stored fact
    this contradicts, deletes it, stores the corrected version instead of
    just adding a new fact on top of the stale one.
    """
    candidates = memory.retrieve(raw_text, domain=domain, content_type="fact", top_k=3)

    if not candidates:
        log.info("correction_no_matching_fact", extra={"raw_text": raw_text})
        return "I don't have a stored fact that matches what you're correcting — nothing to update."

    candidate_list = "\n".join(f"{i}: {c['text']}" for i, c in enumerate(candidates))
    prompt = f"""The user is correcting previously stored information. Here are the
candidate stored facts that might be what they're correcting:

{candidate_list}

The user's correction: "{raw_text}"

Which numbered fact (if any) does this correction contradict/replace? Respond with ONLY
a JSON object: {{"index": <number or null>, "corrected_fact": "User's <attribute>: <new value>" or null}}
If none of the candidates are actually related to this correction, use null for both fields."""

    llm_result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        json_mode=True,
        task="fact_handling",
    )
    try:
        result = json.loads(llm_result["answer"])
    except json.JSONDecodeError:
        result = {"index": None, "corrected_fact": None}

    index = result.get("index")
    corrected_fact = result.get("corrected_fact")

    if index is None or corrected_fact is None:
        log.info("correction_no_confident_match", extra={"raw_text": raw_text, "candidates": candidate_list})
        return "I see you're correcting something, but I couldn't confidently match it to a stored fact — could you be more specific?"

    old_fact = candidates[index]
    memory.delete_by_ids([old_fact["id"]], domain=domain)
    memory.store(corrected_fact, domain=domain, content_type="fact")
    log.info("fact_corrected", extra={"old_fact": old_fact["text"], "new_fact": corrected_fact})
    return f"Got it — I've updated that. (Was: \"{old_fact['text']}\")"


def _acknowledge_fact(raw_text: str) -> str:
    """
    Generates a brief, natural acknowledgment of what the user just said,
    instead of a flat canned response. Strictly grounded in only what the
    user actually stated — never invents unstated details about them.
    """
    prompt = f"""The user just told you this about themselves: "{raw_text}"

Write a brief (1-2 sentence), warm, natural acknowledgment. You may ask a short,
relevant follow-up question if it fits naturally. Do NOT invent or assume any
details the user didn't actually say — only react to what's explicitly stated."""

    result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        task="fact_handling",
    )
    return result["answer"].strip()


def canonicalize_fact(raw_text: str) -> str | None:
    """
    Rewrites a raw user statement into a clean, standardized fact before
    storage. This reduces retrieval mismatch caused by inconsistent phrasing
    ("I study at X" vs "my college is X" vs "currently attending X") by
    ensuring everything stored follows the same structure and wording style,
    rather than relying purely on the embedding model to smooth over
    inconsistent raw sentences.

    Returns None if no real fact/value could be extracted (guards against
    the model fabricating a placeholder like "Unknown" when the router
    incorrectly classified a question as a fact statement).
    """
    prompt = f"""Rewrite the following statement as a single, clean, standardized fact
about the user. Remove filler words. Use this exact format:

"User's <attribute>: <value>"

Examples:
"okay so basically i study at psg college of technology" -> "User's college: PSG College of Technology"
"i really like python a lot" -> "User's favorite programming language: Python"

If the statement does NOT actually contain a concrete fact about the user (e.g. it's a
question, or has no real information to extract), respond with exactly: NO_FACT

Statement: {raw_text}

Respond with ONLY the standardized fact, or NO_FACT, nothing else."""

    result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        task="fact_handling",
    )
    canonical = result["answer"].strip()
    canonical = canonical.strip('"').strip("'")  # strip stray wrapping quotes the model sometimes adds

    # Guard: reject placeholder/empty extractions even if the model didn't
    # correctly say NO_FACT — catches "Unknown", "N/A", "Not specified", etc.
    rejected_markers = ["no_fact", "unknown", "n/a", "not specified", "not provided", "not given"]
    if any(marker in canonical.lower() for marker in rejected_markers):
        log.info("fact_extraction_rejected", extra={"raw": raw_text, "attempted": canonical})
        return None

    log.info("fact_canonicalized", extra={"raw": raw_text, "canonical": canonical})
    return canonical


def _last_assistant_question() -> str | None:
    """Checks if Zedek's most recent turn ended in a question, so we can
    explicitly tell the model whether the user's new message is likely
    answering it, versus starting something new."""
    for turn in reversed(SESSION_HISTORY):
        if turn["role"] == "assistant":
            content = turn["content"].strip()
            return content if content.endswith("?") else None
    return None


def answer_general_question(user_input: str, domain: str) -> str:
    """
    Handles requests that aren't system-agent function calls. Combines two
    sources of context: (1) this session's recent turns (short-term, exact
    recall of what was just said) and (2) semantically relevant long-term
    facts from ChromaDB (memory.py). Both are given to the model.
    """
    log.info("general_qa_started", extra={"user_input": user_input, "domain": domain})

    relevant_facts = memory.retrieve(user_input, domain=domain, content_type="fact", top_k=3)
    long_term_lines = [f"- {item['text']}" for item in relevant_facts]
    long_term_block = "\n".join(long_term_lines) if long_term_lines else "(no relevant long-term facts found)"

    previous_question = _last_assistant_question()
    turn_structure_note = ""
    if previous_question:
        turn_structure_note = f"""
Your previous message ended with this question: "{previous_question}"
The user's new message below may (a) answer that question, (b) ask something entirely
new, or (c) do both in one message. Identify which parts of their message are a reply
to your question versus a new topic, and address each part clearly and separately —
do not merge them into one confused statement."""

    prompt = f"""You are Zedek, a helpful personal assistant.
The user you are talking to is a separate person — their own name and facts (if known)
are listed below under "Long-term facts." Never confuse your own identity (Zedek, the
assistant) with the user's identity.
Never contradict, reverse, or "correct" a fact already stated about the user below —
treat everything in "Long-term facts" as ground truth about the user, not up for debate.
{turn_structure_note}

Long-term facts relevant to this question:
{long_term_block}

IMPORTANT: If neither the long-term facts above nor the recent conversation below
actually contain the answer, say plainly that you don't have that information yet —
do NOT invent, guess, or use placeholder text. Never fabricate specific facts
(names, places, numbers) that aren't present in the context.

Answer the user's latest message concisely, using the conversation so far as context."""

    messages = [{"role": "system", "content": prompt}]
    messages.extend(SESSION_HISTORY)
    messages.append({"role": "user", "content": user_input})

    result = llm_provider.generate_chat(messages, task="general_qa")
    answer = result["answer"]
    log.info("general_qa_answered", extra={"facts_used": len(relevant_facts),
                                             "session_turns_used": len(SESSION_HISTORY),
                                             "source": result["source"]})
    return answer


def format_coding_plan(plan: dict) -> str:
    """Present a coding plan without implying that files were changed."""
    steps = "\n".join(f"{index}. {step}" for index, step in enumerate(plan["steps"], start=1))
    return (
        f"I can help with this coding task: {plan['goal']}\n\n"
        f"Proposed plan:\n{steps}\n\n"
        "No files have been changed. Approve this plan when you want me to continue."
    )


def execute(decision: dict) -> str:
    """Validates the routing decision against the allowlist, runs it through
    the tier gate, and executes only if the gate allows it."""
    func_name = decision.get("function")
    domain = decision.get("domain", "personal")
    if domain not in ("personal", "academic"):
        domain = "personal"

    confidence = decision.get("confidence", "high")
    original_input = decision.get("_original_input", "")

    if func_name == "coding_task":
        # ── Checkpoint 1: Tier gate ──────────────────────────────────────
        coding_gate = gate("coding_task", {}, user_input=original_input)
        if coding_gate["action"] == "blocked":
            return coding_gate["message"]
        if coding_gate["action"] == "confirm":
            print(coding_gate["message"])
            if input("> ").strip().lower() != "y":
                log.info("coding_task_confirmation_denied", extra={})
                return "Cancelled."
            log.info("coding_task_confirmation_granted", extra={})

        # ── Checkpoint 2: Plan approval ──────────────────────────────────
        plan = CODING_SPECIALIST.plan_task(original_input)
        print("\n" + "=" * 60)
        print("📋 CODING PLAN")
        print("=" * 60)
        print(f"Goal: {plan.get('goal', 'N/A')}")
        print(f"\nSteps:")
        for i, step in enumerate(plan.get('steps', []), 1):
            print(f"  {i}. {step}")
        if plan.get('files_to_create'):
            print(f"\nNew files: {', '.join(plan['files_to_create'])}")
        if plan.get('files_to_modify'):
            print(f"Modify: {', '.join(plan['files_to_modify'])}")
        if plan.get('risks'):
            print(f"\n⚠️  Risks: {', '.join(plan['risks'])}")
        print(f"\nConstraints: {', '.join(plan.get('constraints', []))}")
        print("=" * 60)
        print("Approve this plan? (y/n)")
        if input("> ").strip().lower() != "y":
            log.info("coding_plan_rejected", extra={"goal": plan.get("goal", "")})
            return "Plan rejected. No code was generated or modified."
        log.info("coding_plan_approved", extra={"goal": plan.get("goal", "")})

        # ── Execute: patch → test → verify ───────────────────────────────
        result = CODING_SPECIALIST.implement_and_verify(original_input)
        log.info("coding_task_verified", extra={
            "status": result["status"],
            "attempts": result["attempts"],
        })

        # Show result summary
        summary = format_coding_result(result)
        print(summary)

        # ── Checkpoint 3: File application approval ──────────────────────
        if result["status"] in ("passed", "unverified") and result.get("patch"):
            review = result.get("review", {})
            review_note = ""
            if review.get("approved"):
                review_note = f" (LLM reviewer approved: {review.get('summary', '')})"
            elif review:
                review_note = f" (LLM reviewer flagged issues: {', '.join(review.get('issues', []))})"

            print(f"\n{'=' * 60}")
            print(f"💾 APPLY CHANGES?{review_note}")
            print(f"Target: {result['patch'].get('target_file', 'N/A')}")
            print(f"{'=' * 60}")
            print("Write this code to the file? (y/n)")
            if input("> ").strip().lower() == "y":
                apply_result = CODING_SPECIALIST.apply_patch(result["patch"])
                if apply_result["applied"]:
                    backup_note = f" Backup at: {apply_result['backup']}" if apply_result.get("backup") else ""
                    log.info("coding_patch_applied", extra=apply_result)
                    return f"{summary}\n\n✅ Changes applied to {apply_result['file']} ({apply_result['lines_written']} lines).{backup_note}"
                else:
                    log.info("coding_patch_apply_failed", extra=apply_result)
                    return f"{summary}\n\n❌ Failed to apply changes: {apply_result.get('error', 'unknown')}"
            else:
                log.info("coding_patch_application_declined", extra={})
                return f"{summary}\n\nChanges NOT applied (code was generated but not written to disk)."

        return summary

    # Low-confidence routing to anything other than a plain question is
    # exactly the failure mode that caused the search_files/remember_fact
    # misroutes — don't commit to an action the router itself is unsure about.
    if confidence == "low" and func_name is not None:
        log.info("low_confidence_routing_fallback", extra={"attempted_function": func_name,
                                                              "user_input": original_input})
        return answer_general_question(original_input, domain)

    if func_name is None:
        return answer_general_question(original_input, domain)

    if func_name == "remember_fact":
        fact_text = decision.get("_original_input", "")
        canonical_fact = canonicalize_fact(fact_text)
        if canonical_fact is None:
            # Router likely misclassified a question/non-fact as remember_fact.
            # Don't store garbage — fall back to answering it as a question instead.
            log.info("remember_fact_fallback_to_qa", extra={"original_input": fact_text})
            return answer_general_question(fact_text, domain)
        memory.store(canonical_fact, domain=domain, content_type="fact")
        log.info("fact_remembered", extra={"domain": domain, "text": canonical_fact})
        return _acknowledge_fact(fact_text)

    if func_name == "correct_fact":
        return _handle_correction(decision.get("_original_input", ""), domain)

    if func_name == "unsupported":
        reason = decision.get("reason", "this request")
        log.info("unsupported_capability_requested", extra={"reason": reason,
                                                               "user_input": decision.get("_original_input", "")})
        return f"That capability ({reason}) isn't built yet — it's on the roadmap and still in progress."

    if func_name not in AVAILABLE_FUNCTIONS:
        log.info("execution_blocked_not_in_allowlist", extra={"attempted_function": func_name})
        return f"Blocked: '{func_name}' is not an allowed function."

    args = decision.get("args", {})
    args = _coerce_arg_types(func_name, args)

    gate_decision = gate(func_name, args, user_input=decision.get("_original_input", ""))

    if gate_decision["action"] == "blocked":
        return gate_decision["message"]

    if gate_decision["action"] == "confirm":
        print(gate_decision["message"])
        answer = input("> ").strip().lower()
        if answer != "y":
            log.info("tier2_confirmation_denied", extra={"function": func_name})
            return "Cancelled."
        log.info("tier2_confirmation_granted", extra={"function": func_name})

    if gate_decision["action"] == "notify":
        print(gate_decision["message"])

    try:
        result = AVAILABLE_FUNCTIONS[func_name](**args)
        log.info("execution_success", extra={"function": func_name, "call_args": args})

        if func_name == "list_processes_detailed":
            return _reason_over_process_data(result, original_input)

        return format_result(func_name, result)
    except Exception as e:
        log.info("execution_error", extra={"function": func_name, "call_args": args, "error": str(e)})
        return f"Error running {func_name}: {e}"


def _reason_over_process_data(process_data: list[dict], user_question: str) -> str:
    """Answer a process-analysis question using only the collected process data."""
    prompt = f"""Here is data on currently running processes:
{json.dumps(process_data, indent=2)}

The user asked: "{user_question}"

Answer their question using ONLY the data above. Do not invent process names,
memory values, or running times not present in the data. If the data doesn't
contain enough information to answer, say so plainly."""

    result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        task="process_reasoning",
    )
    return result["answer"]


def format_coding_result(result: dict) -> str:
    """Present coding verification with plan, code, test results, and review."""
    status = result["status"]
    attempts = result.get("attempts", 0)
    lines: list[str] = []

    # Status header
    if status == "passed":
        lines.append(f"✅ Code passed verification after {attempts} attempt(s).")
    elif status == "unverified":
        lines.append(f"⚠️ Code passed syntax but sandbox was unavailable ({attempts} attempt(s)).")
    else:
        lines.append(f"❌ Code failed verification after {attempts} attempt(s).")
        if result.get("error"):
            lines.append(f"   Reason: {result['error']}")

    # Plan summary
    plan = result.get("plan", {})
    if plan.get("goal"):
        lines.append(f"\nGoal: {plan['goal']}")

    # Code preview (truncated)
    code = result.get("code", "")
    if code:
        code_lines = code.splitlines()
        preview = code_lines[:20]
        lines.append(f"\n--- Generated code ({len(code_lines)} lines) ---")
        lines.extend(preview)
        if len(code_lines) > 20:
            lines.append(f"... ({len(code_lines) - 20} more lines)")

    # Test results
    test_result = result.get("test_result", {})
    if test_result:
        test_status = test_result.get("status", "unknown")
        lines.append(f"\nTests: {test_status}")
        if test_result.get("stdout"):
            lines.append(f"Test output: {test_result['stdout'][:300]}")

    # Sandbox execution
    execution = result.get("execution", {})
    if execution and execution.get("stdout"):
        lines.append(f"\nSandbox output: {execution['stdout'][:300]}")

    # LLM review
    review = result.get("review", {})
    if review:
        approved = "✅ Approved" if review.get("approved") else "⚠️ Not approved"
        lines.append(f"\nLLM Review: {approved}")
        if review.get("summary"):
            lines.append(f"  Summary: {review['summary']}")
        if review.get("issues"):
            for issue in review["issues"]:
                lines.append(f"  - {issue}")
        if review.get("reviewer_source"):
            lines.append(f"  Reviewer: {review['reviewer_source']}")

    # Target file
    patch = result.get("patch", {})
    if patch.get("target_file"):
        lines.append(f"\nTarget file: {patch['target_file']}")

    return "\n".join(lines)


def _coerce_arg_types(func_name: str, args: dict) -> dict:
    """
    LLM JSON output doesn't guarantee correct Python types (e.g. '10' instead of 10).
    Coerce known integer arguments before they hit the function.
    """
    int_args = {
        "top_memory_processes": ["top_n"],
        "disk_usage_by_folder": ["top_n"],
        "list_processes_detailed": ["top_n"],
    }
    for key in int_args.get(func_name, []):
        if key in args:
            try:
                args[key] = int(args[key])
            except (TypeError, ValueError):
                pass  # leave as-is; the function call will raise a clear error if truly invalid
    return args


def format_result(func_name: str, result) -> str:
    """Turns raw function output into a short plain-language summary."""
    if func_name == "free_space_summary":
        return f"You have {result['free_gb']}GB free out of {result['total_gb']}GB total ({result['used_gb']}GB used)."
    if func_name == "directory_size":
        return f"'{result['path']}' is {result['size_gb']}GB."
    if func_name == "top_memory_processes":
        lines = [f"{p['name']} — {p['memory_mb']}MB" for p in result]
        return "Top memory-consuming processes:\n" + "\n".join(lines)
    if func_name == "disk_usage_by_folder":
        lines = [f"{f['folder']} — {f['size_gb']}GB" for f in result]
        return "Largest folders:\n" + "\n".join(lines)
    if func_name == "search_files":
        if not result:
            return "No matching files found."
        return f"Found {len(result)} file(s):\n" + "\n".join(result[:20])
    if func_name == "open_application":
        if result["launched"]:
            return f"Opened {result['app']}."
        return result["reason"]
    return str(result)


def handle(user_input: str) -> str:
    """Full pipeline: route -> validate -> execute -> answer -> store turn."""
    recent_user_turns = [turn["content"] for turn in SESSION_HISTORY if turn["role"] == "user"]

    if should_ask_ambiguous_term_question(user_input, recent_user_turns):
        answer = generate_ambiguity_reply(user_input, recent_user_turns[-1] if recent_user_turns else None)
        _add_to_session("user", user_input)
        _add_to_session("assistant", answer)
        return answer

    previous_user_turn = recent_user_turns[-1] if recent_user_turns else ""
    if should_treat_as_disambiguation(previous_user_turn, user_input):
        answer = generate_ambiguity_reply(user_input, previous_user_turn)
        _add_to_session("user", user_input)
        _add_to_session("assistant", answer)
        return answer

    decision = route_request(user_input)
    decision["_original_input"] = user_input
    domain = decision.get("domain", "personal")
    if domain not in ("personal", "academic"):
        domain = "personal"

    answer = execute(decision)

    # Short-term session context only — nothing written to disk per-turn.
    # Long-term storage happens via summarize_and_flush_session(), not here.
    _add_to_session("user", user_input)
    _add_to_session("assistant", answer)

    return answer


if __name__ == "__main__":
    print("=== Zedek Orchestrator (Phase 6: tier gate + tiered memory active) — interactive test ===")
    print("Try things like: 'how much free space do I have', 'what's using the most memory', 'find my resume file'")
    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            print("Ending session — reviewing what's worth remembering long-term...")
            summarize_and_flush_session()
            break
        if not user_input:
            continue
        answer = handle(user_input)
        print(f"Zedek: {answer}\n")
