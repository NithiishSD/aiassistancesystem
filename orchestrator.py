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

SYSTEM_PROMPT = """You are a routing assistant. Given a user request, decide which function \
to call from this list, and with what arguments, and which domain this belongs to. \
Respond ONLY with JSON, no other text.

Available functions:
- search_files(query: str, root_dir: str = "~") — find files by name
- disk_usage_by_folder(root_dir: str = "~", top_n: int = 10) — largest folders
- top_memory_processes(top_n: int = 10) — processes using the most RAM
- free_space_summary() — overall disk space used/free

Domain must be either "personal" or "academic" — pick whichever the request is more about.
If unclear, default to "personal".

IMPORTANT — if the user is simply STATING A FACT about themselves (e.g. "I study at X",
"my favorite language is Y", "I work at Z") rather than asking a question or requesting an
action, respond with:
{"function": "remember_fact", "args": {}, "domain": "personal", "reason": "user stated a fact"}

If the request doesn't match any function and isn't a fact statement (it's a general
question), respond with:
{"function": null, "args": {}, "domain": "personal", "reason": "general question, no system action needed"}

Otherwise respond with:
{"function": "<function_name>", "args": {"<arg_name>": "<value>"}, "domain": "personal"}
"""


def route_request(user_input: str) -> dict:
    """Asks the local model to pick a function + args for this request."""
    log.info("routing_started", extra={"user_input": user_input})

    response = ollama.chat(
        model=ROUTING_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_input},
        ],
        format="json",
    )
    raw = response["message"]["content"]

    try:
        decision = json.loads(raw)
    except json.JSONDecodeError:
        log.info("routing_parse_failed", extra={"raw_response": raw})
        return {"function": None, "args": {}, "reason": "could not parse routing decision"}

    log.info("routing_decision", extra={"decision": decision})
    return decision


def canonicalize_fact(raw_text: str) -> str:
    """
    Rewrites a raw user statement into a clean, standardized fact before
    storage. This reduces retrieval mismatch caused by inconsistent phrasing
    ("I study at X" vs "my college is X" vs "currently attending X") by
    ensuring everything stored follows the same structure and wording style,
    rather than relying purely on the embedding model to smooth over
    inconsistent raw sentences.
    """
    prompt = f"""Rewrite the following statement as a single, clean, standardized fact
about the user. Remove filler words. Use this exact format:

"User's <attribute>: <value>"

Examples:
"okay so basically i study at psg college of technology" -> "User's college: PSG College of Technology"
"i really like python a lot" -> "User's favorite programming language: Python"

Statement: {raw_text}

Respond with ONLY the standardized fact, nothing else."""

    response = ollama.chat(
        model=ROUTING_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    canonical = response["message"]["content"].strip()
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

    if func_name is None:
        return answer_general_question(decision.get("_original_input", ""), domain)

    if func_name == "remember_fact":
        fact_text = decision.get("_original_input", "")
        canonical_fact = canonicalize_fact(fact_text)
        memory.store(canonical_fact, domain=domain, content_type="fact")
        log.info("fact_remembered", extra={"domain": domain, "text": canonical_fact})
        return "Got it, I'll remember that."

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