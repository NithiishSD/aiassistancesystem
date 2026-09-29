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
import os
import re
import ollama
from zedek_logger import get_logger
from system_agent import AVAILABLE_FUNCTIONS
from tier_gate import gate
import memory
import classifier
import llm_provider
from coding_agent import CodingSpecialist
import mcp_client
import task_planner
import research_agent
from research_agent import ResearchAgent
import web_agent
from web_agent import WebAgent
import coding_agent
import academic_tracker
from academic_tracker import AcademicTracker
from watchdog import Watchdog

log = get_logger("orchestrator")

ROUTING_MODEL = "llama3.1:8b"
CODING_SPECIALIST = CodingSpecialist()
RESEARCH_AGENT = ResearchAgent()
WEB_AGENT = WebAgent()
WATCHDOG = Watchdog()
ACADEMIC_TRACKER = AcademicTracker()


def _declared_write_targets(plan: dict) -> set[str]:
    """The set of file writes a coding plan actually declared.

    A plan that names no files still implicitly authorizes the specialist's
    default target, so that one is included — otherwise every default-target
    patch would read as a deviation.
    """
    declared = {
        f"write:{path}"
        for path in list(plan.get("files_to_create", [])) + list(plan.get("files_to_modify", []))
        if path
    }
    default_target = os.path.join(coding_agent.CODING_WRITE_ROOT, "solution.py")
    declared.add(f"write:{default_target}")
    return declared
LAST_ROUTING_DECISION: dict | None = None


def _init_mcp() -> None:
    """Discover MCP tools at startup. Failure is isolated and logged."""
    try:
        tools = mcp_client.discover_all_tools()
        stats = mcp_client.get_discovery_stats()
        log.info("mcp_discovery_complete", extra=stats)
        if tools:
            classifier.register_mcp_tools(tools)
    except Exception as e:
        log.info("mcp_discovery_failed", extra={"error": str(e)})


_init_mcp()


# Path for dynamically learned/verified system inspection commands
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DYNAMIC_SYSTEM_TOOLS_PATH = os.path.join(_PROJECT_ROOT, "data", "dynamic_system_tools.json")


def _load_dynamic_system_tools() -> dict[str, str]:
    if not os.path.exists(DYNAMIC_SYSTEM_TOOLS_PATH):
        return {}
    try:
        with open(DYNAMIC_SYSTEM_TOOLS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_dynamic_system_tool(query: str, command: str) -> None:
    data = _load_dynamic_system_tools()
    norm = (query or "").strip().lower()
    data[norm] = command.strip()
    try:
        os.makedirs(os.path.dirname(DYNAMIC_SYSTEM_TOOLS_PATH), exist_ok=True)
        with open(DYNAMIC_SYSTEM_TOOLS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.info("dynamic_system_tool_save_failed", extra={"error": str(e)})


def _clean_generated_command(raw: str) -> str:
    """Extract a clean shell command from LLM output, stripping markdown fences and language labels."""
    if not raw:
        return ""
    text = raw.strip()
    match = re.search(r"```(?:bash|sh|zsh)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE)
    if match:
        lines = [line.strip() for line in match.group(1).splitlines() if line.strip() and not line.strip().startswith("#")]
        if lines:
            return lines[0]

    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    for line in lines:
        cleaned = line.strip("`'\" ").strip()
        if cleaned.lower() in ("bash", "sh", "zsh", "shell"):
            continue
        if cleaned.lower().startswith("command:"):
            cleaned = cleaned[8:].strip("`'\" ")
        if cleaned:
            return cleaned
    return text.strip("`'\" ")


def _handle_system_inspect(user_input: str, domain: str) -> str:
    """Safely inspect system metrics/packages using verified read-only commands with dynamic caching."""
    import command_verifier

    norm = (user_input or "").strip().lower()
    cached_tools = _load_dynamic_system_tools()

    command = cached_tools.get(norm)
    via_cache = bool(command)

    if not command:
        prompt = (
            "You are a Linux system administration assistant. Generate a SINGLE safe, non-interactive, "
            "read-only bash command or pipeline to inspect the system and answer the user's question.\n"
            "Rules:\n"
            "- Only read-only commands (dpkg, uname, lscpu, cat /sys/..., cat /proc/..., free, df, ps, uptime, ip, which, wc, grep, awk, cut, etc.)\n"
            "- No modifications, no sudo, no file writes (> or >>), no interactive flags\n"
            "- Output ONLY the raw command string without markdown fences, quotes, or explanation.\n\n"
            f"User Question: {user_input}"
        )
        try:
            res = llm_provider.generate_chat([{"role": "user", "content": prompt}], task="process_reasoning")
            command = _clean_generated_command(res.get("answer", ""))
        except Exception as e:
            log.info("system_inspect_command_gen_failed", extra={"error": str(e)})
            return answer_general_question(user_input, domain)

    if not command:
        return answer_general_question(user_input, domain)

    # Verify and run command through command_verifier
    verified = command_verifier.verify_command(command)
    if not verified.get("proceed"):
        log.info("system_inspect_command_blocked", extra={"command": command, "reason": verified.get("reason")})
        return f"Safety check blocked command '{command}': {verified.get('reason')}"

    dry_run = verified.get("dry_run")
    if not dry_run or not dry_run.get("success"):
        error_msg = dry_run.get("error", "Execution failed") if dry_run else "Execution failed"
        log.info("system_inspect_dry_run_failed", extra={"command": command, "error": error_msg})
        return f"Could not inspect system ({error_msg})."

    output = dry_run.get("stdout", "").strip()
    if not output:
        output = dry_run.get("stderr", "").strip() or "0"

    # If successfully executed and was newly generated, save to dynamic tool cache and router
    if not via_cache and output:
        _save_dynamic_system_tool(user_input, command)
        classifier.add_utterance_dynamically(user_input, "system_inspect")
        log.info("dynamic_system_tool_registered", extra={"query": user_input, "command": command})

    # Synthesize the raw output into a natural response
    summary_prompt = (
        f"The user asked: '{user_input}'\n"
        f"System inspection command `{command}` returned:\n"
        f"{output}\n\n"
        "Provide a concise, direct, helpful one-to-two sentence answer to the user based on this data. "
        "State the exact numbers/facts clearly."
    )
    try:
        synth = llm_provider.generate_chat([{"role": "user", "content": summary_prompt}], task="general_qa")
        return synth.get("answer", f"System result: {output}")
    except Exception:
        return f"System result: {output}"

# --- Short-term session context (this run only, NOT persisted to disk) ---
# Separate from memory.py's long-term ChromaDB store. This holds the last
# few raw turns of THIS conversation so immediate follow-ups work correctly,
# without relying on semantic search to "guess" what you just said.
SESSION_HISTORY: list[dict] = []
MAX_SESSION_TURNS = 10  # last 10 messages (~5 exchanges)


def summarize_and_flush_session(domain: str = "personal", keep_recent: int = 0) -> None:
    """
    Reviews the current session buffer, extracts only what's genuinely worth
    remembering long-term (new facts, decisions, preferences), and stores
    ONLY that distilled summary to ChromaDB — not the raw conversation.
    Called when the session buffer fills up, or when the session ends.
    
    If keep_recent > 0, keeps the latest N turns in SESSION_HISTORY so
    conversational continuity is not broken mid-session.
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

    try:
        response = ollama.chat(model=ROUTING_MODEL, messages=[{"role": "user", "content": prompt}])
        extracted = response["message"]["content"].strip()

        if extracted.upper() == "NONE" or not extracted:
            log.info("session_flush_nothing_worth_storing", extra={"turns_reviewed": len(SESSION_HISTORY)})
        else:
            facts = [line.strip() for line in extracted.split("\n") if line.strip() and line.strip().startswith("User's")]
            for fact in facts:
                memory.store(fact, domain=domain, content_type="fact")
            log.info("session_flush_stored", extra={"facts_stored": len(facts), "turns_reviewed": len(SESSION_HISTORY)})
    except Exception as err:
        log.info("session_flush_error", extra={"error": str(err)})

    if keep_recent > 0 and len(SESSION_HISTORY) > keep_recent:
        SESSION_HISTORY = SESSION_HISTORY[-keep_recent:]
    elif keep_recent == 0:
        SESSION_HISTORY = []


def _add_to_session(role: str, content: str) -> None:
    SESSION_HISTORY.append({"role": role, "content": content})
    if len(SESSION_HISTORY) > MAX_SESSION_TURNS:
        log.info("session_buffer_full_flushing", extra={"turns": len(SESSION_HISTORY)})
        summarize_and_flush_session(keep_recent=4)

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
        "space", "stars", "planet", "game", "metal", "banking",
        "os", "linux", "operating system", "system", "version", "ubuntu", "debian", "arch", "command", "terminal"
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
    correction_signals = ["no", "incorrect", "wrong", "mistake", "mistook", "remove", "delete", "not part of", "correct", "not true", "false"]
    if any(sig in text.lower() for sig in correction_signals):
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

    if classifier.is_acknowledgement(user_input):
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
    elif func_name == "mcp_tool" or (func_name and func_name.startswith("mcp_")):
        decision["args"] = _extract_mcp_args(user_input, target_tool_qname=func_name if func_name.startswith("mcp_") else None)
    else:
        decision["args"] = {}

    log.info("routing_decision",
             extra={"decision": {k: v for k, v in decision.items() if k != "args"},
                    "via_llm": via_llm})
    return decision


def _extract_application_name(user_input: str) -> str:
    """Extract the requested application name cleanly without relying on an LLM."""
    text = re.sub(r"\s+", " ", (user_input or "").strip())
    if not text:
        return ""

    lowered = text.lower()

    # If the user input looks like an informational question, don't treat the sentence as a launch target
    if any(lowered.startswith(q) for q in ["what ", "how ", "why ", "which ", "where ", "who ", "can you tell ", "list "]):
        match = re.search(
            r"\b(?:open|launch|start|run)\s+(?:the\s+)?([a-zA-Z0-9_\-\.\+\s]+?)(?:\s+(?:app|application|program|software))?(?:[?,.!;]|$)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            candidate = match.group(1).strip(" .,!?\"'")
            if candidate.lower() not in ("it", "app", "application", "program", "this", "them", "file", "files", ""):
                return candidate
        return ""

    # Strip conversational prefixes and leading intent commands
    cleaned = re.sub(
        r"^(?:(?:hey|hi|hello|zedek|yo)\s*,?\s*)*"
        r"(?:(?:no|actually|wait|i\s+mean|i\s+said|its|it\'s|it\s+is|just)\s*,?\s*)*"
        r"(?:(?:please|pls|can\s+you|could\s+you|would\s+you|kindly|then|try\s+to|help\s+me)\s+)*"
        r"(?:open|launch|start|run|execute|fire\s+up)\s+"
        r"(?:the\s+|an\s+|a\s+)?",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Strip conversational follow-ups / corrections (e.g. "its App center", "no, AppCenter")
    cleaned = re.sub(
        r"^(?:(?:hey|hi|hello|zedek|yo)\s*,?\s*)*"
        r"(?:(?:no|actually|wait|i\s+mean|i\s+said|its|it\'s|it\s+is|just)\s*,?\s*)+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    # Strip trailing app/application/program descriptors
    cleaned = re.sub(
        r"\s+(?:application|app|program|software|package|tool)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip(" .,!?\"'")

    if not cleaned or cleaned.lower() in ("it", "app", "application", "program", "this", "them", "something") or len(cleaned.split()) > 5:
        return ""

    return cleaned


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


def _select_mcp_tool(user_input: str, target_tool_qname: str | None = None) -> mcp_client.MCPToolSpec | None:
    """Select which registered MCP tool best matches the request."""
    registry = mcp_client.get_tool_registry()
    if not registry:
        return None

    if target_tool_qname and target_tool_qname in registry:
        return registry[target_tool_qname]

    lowered = (user_input or "").lower()

    # 1. Exact or keyword matches against tool names
    for qname, spec in registry.items():
        tname = spec.tool_name.lower().replace("_", " ")
        if tname in lowered:
            return spec

    if any(w in lowered for w in ["time", "date", "clock", "today", "now"]):
        for qname, spec in registry.items():
            if "time" in spec.tool_name or "date" in spec.tool_name:
                return spec

    if any(w in lowered for w in ["word", "words", "count", "character", "chars", "lines", "length"]):
        for qname, spec in registry.items():
            if "word" in spec.tool_name or "count" in spec.tool_name:
                return spec

    if any(w in lowered for w in ["summar", "brief", "shorten", "overview", "tldr"]):
        for qname, spec in registry.items():
            if "summar" in spec.tool_name:
                return spec

    if len(registry) == 1:
        return next(iter(registry.values()))

    # 2. Semantic selection via LLM if multiple tools exist
    tool_list_str = "\n".join(f"- {spec.qualified_name}: {spec.description}" for spec in registry.values())
    prompt = (
        f"Pick the single most appropriate tool for this user request from the list below.\n"
        f"User request: '{user_input}'\n"
        f"Available tools:\n{tool_list_str}\n\n"
        f"Output ONLY the tool qualified_name (e.g. mcp_zedek_tools_current_time). No explanation."
    )
    try:
        res = llm_provider.generate_chat([{"role": "user", "content": prompt}], task="process_reasoning")
        chosen = res.get("answer", "").strip()
        for qname, spec in registry.items():
            if qname in chosen:
                return spec
    except Exception:
        pass

    return next(iter(registry.values())) if registry else None


def _extract_target_text(user_input: str) -> str:
    """Extract payload text from requests like 'count words in hello world'."""
    text = (user_input or "").strip()
    match = re.search(r'["\'](.*?)["\']', text)
    if match:
        return match.group(1).strip()

    cleaned = re.sub(
        r'^(?:(?:hey|hi|hello|zedek|yo)\s*,?\s*)*'
        r'(?:(?:please|pls|can\s+you|could\s+you|would\s+you|help\s+me)\s+)*'
        r'(?:count\s+(?:the\s+)?(?:words|characters|chars|lines)\s+(?:in|of|for)?|'
        r'how\s+many\s+words\s+(?:are\s+)?(?:in|of)?|'
        r'summarize\s+(?:this\s+)?(?:text|paragraph|article)?|'
        r'word\s+count\s+(?:for|of|in)?)\s*',
        '',
        text,
        flags=re.IGNORECASE,
    ).strip()
    return cleaned or text


def _extract_mcp_args(user_input: str, target_tool_qname: str | None = None) -> dict:
    """Extract arguments for an MCP tool based on its JSON input schema."""
    tool_spec = _select_mcp_tool(user_input, target_tool_qname)
    if not tool_spec:
        return {"qualified_name": "", "tool_args": {}}

    schema = tool_spec.input_schema or {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    if not properties and not required:
        return {"qualified_name": tool_spec.qualified_name, "tool_args": {}}

    # Common pattern: single text parameter
    if list(properties.keys()) == ["text"]:
        text_arg = _extract_target_text(user_input)
        return {"qualified_name": tool_spec.qualified_name, "tool_args": {"text": text_arg}}

    # General schema-driven extraction via narrow Llama call
    arg_prompt = f"""Extract arguments for the tool "{tool_spec.tool_name}" with JSON schema:
{json.dumps(schema, indent=2)}

Request: {user_input}

Respond ONLY with a JSON object of argument names to values. If no arguments are mentioned, respond with {{}}."""
    try:
        response = ollama.chat(
            model=ROUTING_MODEL,
            messages=[{"role": "user", "content": arg_prompt}],
            format="json",
        )
        parsed = json.loads(response["message"]["content"])
        if isinstance(parsed, dict):
            return {"qualified_name": tool_spec.qualified_name, "tool_args": parsed}
    except Exception:
        pass

    return {"qualified_name": tool_spec.qualified_name, "tool_args": {}}


def _validate_mcp_args(args: dict, schema: dict) -> str | None:
    """Returns an error message string if arguments fail JSON schema validation, else None."""
    if not schema:
        return None
    try:
        import jsonschema
        jsonschema.validate(instance=args, schema=schema)
        return None
    except jsonschema.ValidationError as e:
        return e.message
    except Exception as e:
        return str(e)



def _handle_correction(raw_text: str, domain: str) -> str:
    """
    Handles a fact correction or retraction/deletion.
    Finds candidate stored facts, and either updates the fact with a new value
    or deletes/retracts it if the user indicated it was false or requested removal.
    Also self-heals by pruning any recently learned dynamic utterance that led to this mistake.
    """
    global LAST_ROUTING_DECISION
    if LAST_ROUTING_DECISION and LAST_ROUTING_DECISION.get("via_llm"):
        classifier.remove_utterance_dynamically(
            LAST_ROUTING_DECISION.get("_original_input", ""),
            LAST_ROUTING_DECISION.get("function")
        )
    last_q = _last_assistant_question()
    search_query = f"{last_q} {raw_text}".strip() if last_q and len(raw_text.split()) <= 6 else raw_text
    candidates = memory.retrieve(search_query, domain=domain, content_type="fact", top_k=4)

    if not candidates:
        log.info("correction_no_matching_fact", extra={"raw_text": raw_text, "query": search_query})
        return "I see you're correcting something, but I don't have a stored fact that matches what you're correcting."

    candidate_list = "\n".join(f"{i}: {c['text']}" for i, c in enumerate(candidates))
    recent_context = f"\nRecent conversation context: Zedek asked: \"{last_q}\"" if last_q else ""

    prompt = f"""The user is correcting, retracting, or removing previously stored information.{recent_context}

Here are the candidate stored facts in memory:
{candidate_list}

The user's statement: "{raw_text}"

Determine which candidate fact (if any) this statement contradicts, negates, or wants removed.
- If the user wants to update the fact with a new value, provide the index and the updated fact.
- If the user wants to remove/delete the fact because it is false or mistaken (without replacing it), provide the index and set "corrected_fact": null.
- If none of the candidates match, set both to null.

Respond ONLY with valid JSON:
{{"index": <number 0-{len(candidates)-1} or null>, "corrected_fact": "User's <attribute>: <new value>" or null}}"""

    llm_result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        json_mode=True,
        task="fact_handling",
    )
    try:
        result = json.loads(llm_result["answer"])
    except (json.JSONDecodeError, TypeError):
        result = {"index": None, "corrected_fact": None}

    index = result.get("index")
    corrected_fact = result.get("corrected_fact")

    if index is None or not isinstance(index, int) or index < 0 or index >= len(candidates):
        log.info("correction_no_confident_match", extra={"raw_text": raw_text, "candidates": candidate_list})
        return "I understand you're correcting that, but I couldn't confidently pinpoint which stored fact to update. Could you mention the specific detail?"

    old_fact = candidates[index]
    memory.delete_by_ids([old_fact["id"]], domain=domain)

    if corrected_fact and str(corrected_fact).strip().lower() not in ("null", "none"):
        memory.store(str(corrected_fact).strip(), domain=domain, content_type="fact")
        log.info("fact_corrected", extra={"old_fact": old_fact["text"], "new_fact": corrected_fact})
        return f"Got it — I've updated that in memory. (Updated: \"{old_fact['text']}\" ➔ \"{corrected_fact}\")"
    else:
        log.info("fact_retracted", extra={"old_fact": old_fact["text"]})
        return f"Got it — I've removed that from memory. (Removed: \"{old_fact['text']}\")"


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


def canonicalize_fact(raw_text: str) -> list[str]:
    """
    Rewrites a raw user statement into clean, standardized fact(s) before storage.
    Supports extracting multiple distinct facts if the statement contains multiple details
    (e.g., program + department + subjects).

    Returns a list of standardized fact strings ["User's <attr>: <val>", ...],
    or [] if no real facts could be extracted.
    """
    prompt = f"""Rewrite the following user statement into clean, standardized facts.
If the statement contains multiple distinct facts, write EACH fact on its own line.
Use this exact format for every line:

User's <attribute>: <value>

Examples:
"okay so basically i study at psg college of technology" ->
User's college: PSG College of Technology

"see software system is the program provided by the amcs department and ml java are the subjects i study" ->
User's program: Software Systems
User's department: AMCS
User's subjects: Machine Learning, Java

"i really like python a lot" ->
User's favorite programming language: Python

If the statement does NOT contain any real, concrete fact about the user (e.g. it is a question,
a greeting, or routine banter), respond with exactly: NO_FACT

Statement: {raw_text}

Respond with ONLY the standardized fact line(s), or NO_FACT, nothing else."""

    result = llm_provider.generate_chat(
        [{"role": "user", "content": prompt}],
        task="fact_handling",
    )
    raw_answer = result["answer"].strip()
    rejected_markers = ["no_fact", "unknown", "n/a", "not specified", "not provided", "not given"]

    facts: list[str] = []
    for line in raw_answer.splitlines():
        line = line.strip().strip('"').strip("'")
        if not line or any(marker in line.lower() for marker in rejected_markers):
            continue
        if line.startswith("User's ") and ":" in line:
            facts.append(line)

    log.info("fact_canonicalized", extra={"raw": raw_text, "facts_extracted": len(facts), "facts": facts})
    return facts


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

    relevant_facts = memory.retrieve_relevant(user_input, domain=domain, content_type="fact", top_k=3)
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


def _handle_research(decision: dict, domain: str) -> str:
    """Dispatch a research question to the grounded ResearchAgent (Roadmap Item 10).

    The agent gates each of its own read-only tool calls internally, so no
    additional gate() call is needed here.
    """
    question = decision.get("_original_input", "")
    report = RESEARCH_AGENT.research(question, domain=domain)

    log.info("research_task_completed", extra={
        "grounded": report.grounded, "source_count": len(report.sources),
    })

    # Only reinforce the router when the research actually produced grounded
    # evidence — a failed, ungrounded run is not a signal that routing was right.
    if decision.get("via_llm") and report.grounded:
        classifier.add_utterance_dynamically(question, "research_task")

    return research_agent.format_research_report(report)


def _extract_academic_intent(user_input: str) -> dict:
    """Work out whether the user is logging practice or reviewing progress.

    Falls back to "review" on any failure — reading progress is harmless,
    whereas guessing at a log entry would write junk into the practice history.
    """
    prompt = f"""The user is talking about their DSA / aptitude / placement practice.

Message: {user_input}

Decide what they want:
- "log"     — they are recording a practice attempt they just did
- "review"  — they are asking what is weak or what to practice next
- "summary" — they are asking about overall progress, accuracy, or streak

If (and only if) the action is "log", also extract:
- topic: the subject area (e.g. "dynamic programming", "graphs", "quantitative aptitude")
- result: exactly one of "solved", "failed", "partial"
- problem: the problem name, if mentioned (else "")
- difficulty: easy/medium/hard, if mentioned (else "")
- minutes: minutes spent as a number, if mentioned (else 0)

Return ONLY valid JSON:
{{"action": "log", "topic": "graphs", "result": "solved", "problem": "", "difficulty": "", "minutes": 0}}"""

    try:
        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}], json_mode=True, task="fact_handling",
        )
        parsed = json.loads(result["answer"])
        if isinstance(parsed, dict) and parsed.get("action") in ("log", "review", "summary"):
            return parsed
    except Exception as e:
        log.info("academic_intent_extraction_failed", extra={"error": str(e)})

    return {"action": "review"}


def _handle_academic_tracking(decision: dict) -> str:
    """Log a practice attempt or report on progress (Roadmap Item 14)."""
    user_input = decision.get("_original_input", "")
    extracted = _extract_academic_intent(user_input)
    action = extracted.get("action", "review")

    if action == "log":
        attempt = ACADEMIC_TRACKER.log_attempt(
            topic=extracted.get("topic", ""),
            result=extracted.get("result", ""),
            problem=extracted.get("problem", ""),
            difficulty=extracted.get("difficulty", ""),
            minutes=extracted.get("minutes", 0) or 0,
        )
        if attempt is None:
            log.info("academic_log_failed_extraction", extra={"user_input": user_input})
            return ("I couldn't tell which topic and outcome to record. Try something like "
                    "\"log that I solved a graphs problem in 20 minutes\".")

        if decision.get("via_llm"):
            classifier.add_utterance_dynamically(user_input, "academic_tracking")

        log.info("academic_attempt_recorded", extra={"topic": attempt.topic, "result": attempt.result})
        stats = ACADEMIC_TRACKER.topic_stats(attempt.topic)
        return (f"Logged: {attempt.result} on {attempt.topic}"
                f"{f' ({attempt.problem})' if attempt.problem else ''}. "
                f"That's {stats.solved}/{stats.attempts} solved on {attempt.topic} so far.")

    if action == "summary":
        if decision.get("via_llm"):
            classifier.add_utterance_dynamically(user_input, "academic_tracking")
        return academic_tracker.format_summary(ACADEMIC_TRACKER.summary())

    if decision.get("via_llm"):
        classifier.add_utterance_dynamically(user_input, "academic_tracking")
    return academic_tracker.format_recommendations(ACADEMIC_TRACKER.recommend())


def _interactive_confirm(message: str) -> bool:
    """Terminal confirmation prompt used for Tier 2 browser actions."""
    print(message)
    return input("> ").strip().lower() == "y"


def _handle_web_task(decision: dict) -> str:
    """Dispatch a browsing goal to the WebAgent (Roadmap Item 10).

    Browser tools are Tier 2, so every action stops for typed confirmation.
    The agent gates each action itself; confirmation is injected here so the
    agent can never self-approve.
    """
    goal = decision.get("_original_input", "")
    urls = research_agent.extract_urls(goal)
    start_url = urls[0] if urls else None

    report = WEB_AGENT.browse(goal, start_url=start_url, confirm_fn=_interactive_confirm)

    log.info("web_task_completed", extra={
        "completed": report.completed, "steps": len(report.steps),
    })

    if decision.get("via_llm") and report.completed:
        classifier.add_utterance_dynamically(goal, "web_task")

    return web_agent.format_browse_report(report)


def _execute_mcp_tool(decision: dict) -> str:
    """Dispatch to MCP layer. Tier gate is called internally — callers need not."""
    args_dict = decision.get("args", {})
    qualified_name = args_dict.get("qualified_name", "")
    tool_args = args_dict.get("tool_args", {})
    original_input = decision.get("_original_input", "")

    if not qualified_name:
        # Try finding tool directly from original input
        spec = _select_mcp_tool(original_input)
        if spec:
            qualified_name = spec.qualified_name
            tool_args = _extract_mcp_args(original_input, target_tool_qname=qualified_name).get("tool_args", {})

    if not qualified_name:
        log.info("mcp_tool_execution_missing_target", extra={"user_input": original_input})
        return "No matching MCP tool found for this request."

    registry = mcp_client.get_tool_registry()
    if qualified_name not in registry:
        log.info("mcp_tool_execution_unknown_tool", extra={"qualified_name": qualified_name})
        return f"Unknown MCP tool: '{qualified_name}'."

    tool_spec = registry[qualified_name]
    arg_error = _validate_mcp_args(tool_args, tool_spec.input_schema)
    if arg_error:
        log.info("mcp_tool_schema_validation_failed", extra={"qualified_name": qualified_name, "error": arg_error})
        return f"Invalid arguments for {tool_spec.tool_name}: {arg_error}"

    # Always pass through gate() — never bypassed
    gate_decision = gate(qualified_name, tool_args, user_input=original_input,
                         tool_description=tool_spec.description)
    if gate_decision["action"] == "blocked":
        return gate_decision["message"]
    if gate_decision["action"] == "confirm":
        print(gate_decision["message"])
        answer = input("> ").strip().lower()
        if answer != "y":
            log.info("mcp_tool_confirmation_denied", extra={"qualified_name": qualified_name})
            return "Cancelled."
        log.info("mcp_tool_confirmation_granted", extra={"qualified_name": qualified_name})
    if gate_decision["action"] == "notify":
        print(gate_decision["message"])

    result = mcp_client.call_mcp_tool(qualified_name, tool_args)
    if result.get("error"):
        log.info("mcp_tool_execution_error", extra={"qualified_name": qualified_name, "error": result["error"]})
        return f"MCP tool error: {result['error']}"

    output = result.get("result", "")
    log.info("mcp_tool_execution_success", extra={"qualified_name": qualified_name})

    if decision.get("via_llm"):
        classifier.add_utterance_dynamically(original_input, "mcp_tool")

    if output is None or output == "":
        return "(no output)"
    return str(output)


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
        print("\nSteps:")
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

        # Execution verified: plan approved by user
        if decision.get("via_llm"):
            classifier.add_utterance_dynamically(original_input, "coding_task")

        # Register the approved plan with the watchdog so any write to a file
        # the user never approved is caught before it reaches the apply prompt.
        plan_id = WATCHDOG.register_plan(
            goal=plan.get("goal", original_input),
            allowed_actions=_declared_write_targets(plan),
            steps=plan.get("steps", []),
        )

        # ── Execute: patch → test → verify ───────────────────────────────
        # The approved plan is passed back in so code is generated against the
        # plan the user actually saw, not a regenerated one.
        result = CODING_SPECIALIST.implement_and_verify(original_input, plan=plan)
        log.info("coding_task_verified", extra={
            "status": result["status"],
            "attempts": result["attempts"],
        })

        # Show result summary
        summary = format_coding_result(result)
        print(summary)

        # ── Watchdog: does the patch target a file the plan declared? ────
        target_file = (result.get("patch") or {}).get("target_file", "")
        if target_file:
            verdict = WATCHDOG.observe(
                plan_id, f"write:{target_file}", {"target_file": target_file}, tier=2,
            )
            if not verdict.allowed:
                log.info("coding_patch_blocked_by_watchdog", extra={
                    "target_file": target_file, "reason": verdict.reason,
                })
                return (
                    f"{summary}\n\n⛔ Watchdog blocked this patch: {verdict.reason}\n"
                    "Nothing was written. Re-run the request if you want this file included in the plan."
                )

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
        canonical_facts = canonicalize_fact(fact_text)
        if not canonical_facts:
            # Router likely misclassified a question/non-fact as remember_fact.
            # Don't store garbage — fall back to answering it as a question instead.
            log.info("remember_fact_fallback_to_qa", extra={"original_input": fact_text})
            return answer_general_question(fact_text, domain)
        for c_fact in canonical_facts:
            memory.store(c_fact, domain=domain, content_type="fact")
            log.info("fact_remembered", extra={"domain": domain, "text": c_fact})

        # Execution verified: facts canonicalized and stored
        if decision.get("via_llm"):
            classifier.add_utterance_dynamically(original_input, "remember_fact")

        return _acknowledge_fact(fact_text)

    if func_name == "correct_fact":
        return _handle_correction(decision.get("_original_input", ""), domain)

    if func_name == "system_inspect":
        return _handle_system_inspect(decision.get("_original_input", ""), domain)

    if func_name == "research_task":
        return _handle_research(decision, domain)

    if func_name == "web_task":
        return _handle_web_task(decision)

    if func_name == "academic_tracking":
        return _handle_academic_tracking(decision)

    if func_name == "unsupported":
        reason = decision.get("reason", "this request")
        log.info("unsupported_capability_requested", extra={"reason": reason,
                                                               "user_input": decision.get("_original_input", "")})
        return f"That capability ({reason}) isn't built yet — it's on the roadmap and still in progress."

    if func_name == "mcp_tool" or (func_name and func_name.startswith("mcp_")):
        return _execute_mcp_tool(decision)

    if func_name not in AVAILABLE_FUNCTIONS:
        log.info("execution_blocked_not_in_allowlist", extra={"attempted_function": func_name})
        return f"Blocked: '{func_name}' is not an allowed function."

    args = decision.get("args", {})
    args = _coerce_arg_types(func_name, args)

    if func_name == "open_application":
        app_name = (args.get("app_name") or "").strip()
        if not app_name:
            log.info("open_application_missing_app_name", extra={"user_input": decision.get("_original_input", "")})
            return "Which application would you like me to open? (e.g. Brave, VS Code, Calculator)"

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

        # Execution-verified dynamic learning: only persist if action truly succeeded
        if decision.get("via_llm"):
            if func_name == "open_application":
                if isinstance(result, dict) and result.get("launched") is True:
                    classifier.add_utterance_dynamically(original_input, "open_application")
            else:
                classifier.add_utterance_dynamically(original_input, func_name)

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


def _handle_single(user_input: str) -> str:
    """Route -> validate -> execute -> store turn, for one atomic request.

    This is the pre-existing single-task pipeline, extracted so the task
    planner (Roadmap Item 9) can run it once per decomposed sub-request
    without duplicating the routing/session-logging logic.
    """
    global LAST_ROUTING_DECISION
    decision = route_request(user_input)
    decision["_original_input"] = user_input
    LAST_ROUTING_DECISION = decision
    domain = decision.get("domain", "personal")
    if domain not in ("personal", "academic"):
        domain = "personal"

    answer = execute(decision)

    # Short-term session context only — nothing written to disk per-turn.
    # Long-term storage happens via summarize_and_flush_session(), not here.
    _add_to_session("user", user_input)
    _add_to_session("assistant", answer)

    return answer


def _substitute_result_placeholders(description: str, prior_results: list[str]) -> str:
    """Replace {{result_of_N}} (1-based) with a previously computed sub-task's answer."""
    def _sub(match: re.Match) -> str:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(prior_results):
            return prior_results[idx]
        return match.group(0)
    return re.sub(r"\{\{result_of_(\d+)\}\}", _sub, description)


def _handle_decomposed(user_input: str) -> str:
    """Break a bundled multi-part request into atomic sub-requests (Roadmap
    Item 9: task planner/decomposer) and run each through the normal
    single-task pipeline in order, substituting earlier results into later
    sub-requests when referenced.
    """
    subtasks = task_planner.decompose(user_input)
    if len(subtasks) <= 1:
        return _handle_single(user_input)

    log.info("task_plan_execution_started", extra={
        "original_input": user_input, "subtask_count": len(subtasks),
    })

    results: list[str] = []
    lines: list[str] = []
    for i, subtask in enumerate(subtasks, start=1):
        description = _substitute_result_placeholders(subtask["description"], results)
        answer = _handle_single(description)
        results.append(answer)
        lines.append(f"{i}. {description}\n   → {answer}")

    log.info("task_plan_execution_finished", extra={"subtask_count": len(subtasks)})
    return "I broke this into steps:\n\n" + "\n".join(lines)


def handle(user_input: str) -> str:
    """Full pipeline: (optional) decompose -> route -> validate -> execute -> answer -> store turn."""
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

    if task_planner.should_decompose(user_input):
        return _handle_decomposed(user_input)

    return _handle_single(user_input)


if __name__ == "__main__":
    print("=== Zedek Orchestrator (Phase 6: tier gate + tiered memory active) — interactive test ===")
    print("Try things like: 'how much free space do I have', 'what's using the most memory', 'find my resume file'")
    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            print("Ending session — reviewing what's worth remembering long-term...")
            summarize_and_flush_session()
            log.info("provider_stats_session_end", extra={"stats": llm_provider.provider_stats()})
            break
        if not user_input:
            continue
        answer = handle(user_input)
        print(f"Zedek: {answer}\n")
