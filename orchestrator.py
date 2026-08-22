"""
Phase 4: Orchestrator with tier gate.

Takes a text request, asks Llama3.1 (local) to decide which system_agent
function to call (if any) and with what arguments, validates that choice
against the allowlist, passes it through the tier gate (Phase 4) for
classification and confirmation, then executes and returns a plain-language
answer.

Pipeline: request -> intent -> tier gate -> validated function call -> answer.
"""

import json
import ollama
from zedek_logger import get_logger
from system_agent import AVAILABLE_FUNCTIONS
from tier_gate import gate

log = get_logger("orchestrator")

ROUTING_MODEL = "llama3.1:8b"

SYSTEM_PROMPT = """You are a routing assistant. Given a user request, decide which function \
to call from this list, and with what arguments. Respond ONLY with JSON, no other text.

Available functions:
- search_files(query: str, root_dir: str = "~") — find files by name
- disk_usage_by_folder(root_dir: str = "~", top_n: int = 10) — largest folders
- top_memory_processes(top_n: int = 10) — processes using the most RAM
- free_space_summary() — overall disk space used/free

If the request doesn't match any function (it's a general question), respond with:
{"function": null, "args": {}, "reason": "general question, no system action needed"}

Otherwise respond with:
{"function": "<function_name>", "args": {"<arg_name>": "<value>"}}
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


def execute(decision: dict) -> str:
    """Validates the routing decision against the allowlist, runs it through
    the tier gate, and executes only if the gate allows it."""
    func_name = decision.get("function")

    if func_name is None:
        return "This looks like a general question — not a system task. (General Q&A routing comes in a later phase.)"

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
    """Full pipeline: route -> validate -> execute -> answer."""
    decision = route_request(user_input)
    decision["_original_input"] = user_input
    return execute(decision)


if __name__ == "__main__":
    print("=== Zedek Orchestrator (Phase 4: tier gate active) — interactive test ===")
    print("Try things like: 'how much free space do I have', 'what's using the most memory', 'find my resume file'")
    print("Type 'quit' to exit.\n")

    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit"):
            break
        if not user_input:
            continue
        answer = handle(user_input)
        print(f"Zedek: {answer}\n")