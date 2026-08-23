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
import ollama
from zedek_logger import get_logger
from system_agent import AVAILABLE_FUNCTIONS
from tier_gate import gate
import memory
import classifier

log = get_logger("orchestrator")

ROUTING_MODEL = "llama3.1:8b"

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

    intent_result = classifier.classify_intent(user_input)
    domain = classifier.classify_domain(user_input)

    func_name = intent_result["function"]
    confidence = intent_result["confidence"]

    decision = {"function": func_name, "domain": domain, "confidence": confidence,
                "score": intent_result["score"]}

    # Only real functions (not remember_fact/unsupported/None) need argument
    # extraction — and this is now a narrow, well-defined task for Llama,
    # not a classification decision.
    if func_name in AVAILABLE_FUNCTIONS:
        decision["args"] = _extract_args(func_name, user_input)
    else:
        decision["args"] = {}

    log.info("routing_decision", extra={"decision": decision})
    return decision


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

    response = ollama.chat(model=ROUTING_MODEL, messages=[{"role": "user", "content": prompt}], format="json")
    try:
        result = json.loads(response["message"]["content"])
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

    response = ollama.chat(model=ROUTING_MODEL, messages=[{"role": "user", "content": prompt}])
    return response["message"]["content"].strip()


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

    response = ollama.chat(
        model=ROUTING_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    canonical = response["message"]["content"].strip()
    canonical = canonical.strip('"').strip("'")  # strip stray wrapping quotes the model sometimes adds

    # Guard: reject placeholder/empty extractions even if the model didn't
    # correctly say NO_FACT — catches "Unknown", "N/A", "Not specified", etc.
    rejected_markers = ["no_fact", "unknown", "n/a", "not specified", "not provided", "not given"]
    if any(marker in canonical.lower() for marker in rejected_markers):
        log.info("fact_extraction_rejected", extra={"raw": raw_text, "attempted": canonical})
        return None

    log.info("fact_canonicalized", extra={"raw": raw_text, "canonical": canonical})
    return canonical


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

    prompt = f"""You are Zedek, a helpful personal assistant.

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

    response = ollama.chat(model=ROUTING_MODEL, messages=messages)
    answer = response["message"]["content"]
    log.info("general_qa_answered", extra={"facts_used": len(relevant_facts), "session_turns_used": len(SESSION_HISTORY)})
    return answer


def execute(decision: dict) -> str:
    """Validates the routing decision against the allowlist, runs it through
    the tier gate, and executes only if the gate allows it."""
    func_name = decision.get("function")
    domain = decision.get("domain", "personal")
    if domain not in ("personal", "academic"):
        domain = "personal"

    confidence = decision.get("confidence", "high")
    original_input = decision.get("_original_input", "")

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
        return format_result(func_name, result)
    except Exception as e:
        log.info("execution_error", extra={"function": func_name, "call_args": args, "error": str(e)})
        return f"Error running {func_name}: {e}"


def _coerce_arg_types(func_name: str, args: dict) -> dict:
    """
    LLM JSON output doesn't guarantee correct Python types (e.g. '10' instead of 10).
    Coerce known integer arguments before they hit the function.
    """
    int_args = {
        "top_memory_processes": ["top_n"],
        "disk_usage_by_folder": ["top_n"],
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
    return str(result)


def handle(user_input: str) -> str:
    """Full pipeline: route -> validate -> execute -> answer -> store turn."""
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