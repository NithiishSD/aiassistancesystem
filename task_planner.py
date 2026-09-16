"""Task planner / decomposer for the Zedek orchestrator (Roadmap Item 9).

A lightweight planning pass that recognizes when a single user request
actually bundles multiple independent asks (e.g. "check my free space,
then find my resume file, and remember that I study at PSG") and breaks
it into an ordered list of atomic sub-requests before execution.

This is intentionally narrow: it does NOT decide what to do with each
sub-request (that remains classifier.classify_intent()'s job) and it does
NOT execute anything (that remains orchestrator.execute()'s job). It only
answers two questions:
  1. should_decompose(text) -> is this actually multiple requests bundled together?
  2. decompose(text) -> what are the atomic sub-requests, in dependency order?

Kept as a single small, single-responsibility module rather than folded
into orchestrator.py, matching the project's stated preference for narrow
components over one module doing many jobs.
"""

from __future__ import annotations

import json
import re
from typing import Any

import llm_provider
from zedek_logger import get_logger

log = get_logger("task_planner")

# Sequencing connectors that suggest the user chained multiple asks together.
_SEQUENCE_CONNECTORS = [
    r"\band then\b",
    r"\bthen\b",
    r"\bafter that\b",
    r"\bafterwards\b",
    r"\bnext,?\b",
    r"\balso,?\b",
    r"\bas well as\b",
]
_SEQUENCE_RE = re.compile("|".join(_SEQUENCE_CONNECTORS), re.IGNORECASE)

# A single bare "and" joining two independent clauses is common, ordinary
# English ("open brave and vscode") and must NOT trigger decomposition on
# its own — only combined with other signals (verb count, punctuation).
_ACTION_VERBS = [
    "open", "close", "find", "search", "check", "remember", "remind",
    "tell", "show", "list", "delete", "create", "write", "run", "build",
    "fix", "summarize", "fetch", "get", "look up", "install", "remove",
]
_VERB_RE = re.compile(r"\b(" + "|".join(re.escape(v) for v in _ACTION_VERBS) + r")\b", re.IGNORECASE)

MAX_SUBTASKS = 5


def _count_action_verbs(text: str) -> int:
    return len(_VERB_RE.findall(text or ""))


def should_decompose(text: str) -> bool:
    """Cheap, regex-only heuristic — no LLM call — for whether a request
    bundles multiple independent asks and is worth running through the
    (LLM-backed) decompose() pass.

    Deliberately conservative: a false negative just means the request is
    handled as one atomic task (the existing, safe default). A false
    positive costs one extra LLM call. We bias toward false negatives.
    """
    if not text or len(text.split()) < 6:
        return False

    has_sequence_word = bool(_SEQUENCE_RE.search(text))
    has_numbered_list = bool(re.search(r"(?:^|\s)(?:1[\.\)]|first,)", text, re.IGNORECASE))
    has_semicolon = ";" in text
    verb_count = _count_action_verbs(text)

    if has_sequence_word or has_numbered_list or has_semicolon:
        return True

    # Two-or-more distinct action verbs joined by "and"/commas is a weaker
    # signal on its own — require at least 3 verbs to avoid over-triggering
    # on simple compound requests like "open brave and vscode".
    if verb_count >= 3 and (" and " in text.lower() or "," in text):
        return True

    return False


def _fallback_split(text: str) -> list[str]:
    """Punctuation-only split used when the LLM call fails or returns
    something unusable. Produces atomic tasks with no cross-references."""
    parts = re.split(
        r"\band then\b|\bthen\b|\bafter that\b|\bafterwards\b|;|\balso,?\b|\bas well as\b",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = [p.strip(" ,.") for p in parts if p.strip(" ,.")]
    return cleaned[:MAX_SUBTASKS] if len(cleaned) > 1 else [text.strip()]


def decompose(text: str) -> list[dict[str, Any]]:
    """Break a bundled request into an ordered list of atomic sub-requests.

    Returns a list of dicts: [{"description": "<atomic request>"}, ...].
    A later subtask's description may reference an earlier one's result
    with the literal placeholder "{{result_of_N}}" (1-indexed), which the
    orchestrator substitutes with that subtask's actual answer text before
    execution (e.g. "remember that {{result_of_1}} is my free space").

    Never raises — falls back to a punctuation split, and ultimately to a
    single-item list containing the original text unchanged, so callers can
    always execute the result even under total LLM failure.
    """
    request = (text or "").strip()
    if not request:
        return [{"description": text}]

    prompt = f"""Break the following user request into an ordered list of independent,
atomic sub-requests. Each sub-request must be a complete, standalone sentence
that could be handled on its own by a personal assistant.

Rules:
- Preserve the original meaning and wording as closely as possible.
- Keep the SAME order the user implied (sequential dependencies matter).
- If a later sub-request needs the RESULT of an earlier one (e.g. "then remember that
  number"), reference it with the literal placeholder {{{{result_of_N}}}} where N is the
  1-based index of the earlier sub-request it depends on.
- Do not invent requests the user did not make.
- If the request is actually just ONE atomic task, return a single-item list.
- Maximum {MAX_SUBTASKS} sub-requests.

User request: {request}

Return ONLY valid JSON with this exact shape:
{{"subtasks": ["first atomic request", "second atomic request", ...]}}"""

    try:
        result = llm_provider.generate_chat(
            [{"role": "user", "content": prompt}],
            json_mode=True,
            task="planning",
        )
        parsed = json.loads(result["answer"])
        raw_subtasks = parsed.get("subtasks", [])
        subtasks = [s.strip() for s in raw_subtasks if isinstance(s, str) and s.strip()]
        if not subtasks:
            raise ValueError("empty subtasks list")
        subtasks = subtasks[:MAX_SUBTASKS]
        log.info("task_decomposed", extra={
            "original": request[:200], "subtask_count": len(subtasks), "source": result.get("source"),
        })
        return [{"description": s} for s in subtasks]
    except Exception as err:
        log.info("task_decompose_llm_failed_fallback", extra={"error": str(err)})
        fallback = _fallback_split(request)
        return [{"description": s} for s in fallback]
