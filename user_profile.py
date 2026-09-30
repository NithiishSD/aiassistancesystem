"""
The core profile block (ROADMAP F6): a handful of the user's most-used facts
that accompany every question about the user, whether or not retrieval found
them.

Why: retrieval misses rewordings. "What do people call me" does not retrieve
"User's name: ..." with any embedding or reranker that was measured
(evals/retrieval_eval.py). The facts that matter most are few, so they are
simply included.

Built fresh from the facts that are currently valid, so a correction shows up
at once and nothing is kept in a second file that could go stale. Two limits:
  - Only for questions that refer to the user (I / my / me). A general
    question still gets no stored facts, and sends none to a cloud model.
  - Sensitive attributes (address, contact details, IDs) are never part of
    the block; they are returned only when retrieval finds them relevant.
"""

from __future__ import annotations

import re

from zedek_logger import get_logger

log = get_logger("user_profile")

MAX_PROFILE_CHARS = 1200
MAX_PROFILE_FACTS = 12

# Attribute names (the part between "User's" and the colon) that belong in the
# block, in display order. Anchored to the end of the name so that
# "course code for Java" or "lab partner's name" do not count as core.
_CORE = [
    r"^(full |first |preferred )?name$",
    r"\b(college|university|institution|school)$",
    r"\b(degree|program|programme|major|branch|department)$",
    r"\b(semester|year of study)$",
    r"^(current |this semester's )?(subjects|courses)$",
    r"\bgoal$",
    r"\btarget compan(y|ies)$",
    r"^placement\b.*\bfocus$",
]
_CORE_RES = [re.compile(pattern, re.IGNORECASE) for pattern in _CORE]
_SENSITIVE_RE = re.compile(
    r"\b(location|address|hometown|home|city|district|phone|mobile|email|e-mail|roll|room|hostel|"
    r"password|passcode|pin|id|account|username|handle|birthday|date of birth|dob|age|salary|bank)\b",
    re.IGNORECASE)
_FACT_RE = re.compile(r"^\s*user'?s\s+(?P<attribute>[^:]{1,80}):\s*(?P<value>.+)$", re.IGNORECASE | re.DOTALL)
_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|im|i've|i'd|my|mine|me|myself)\b", re.IGNORECASE)


def refers_to_user(question: str) -> bool:
    """True when the question is about the user (first person)."""
    return bool(_FIRST_PERSON_RE.search(question or ""))


def _core_rank(text: str) -> int | None:
    match = _FACT_RE.match(text or "")
    if not match:
        return None
    attribute = match.group("attribute")
    if _SENSITIVE_RE.search(attribute):
        return None
    for rank, pattern in enumerate(_CORE_RES):
        if pattern.search(attribute.strip().lower()):
            return rank
    return None


def select(facts: list[dict]) -> list[dict]:
    """The core facts among `facts` (memory.current_facts items): one per
    attribute name, the newest, in a stable order, within the size limits."""
    newest: dict[str, tuple[int, dict]] = {}
    for fact in facts:
        rank = _core_rank(fact.get("text", ""))
        if rank is None:
            continue
        attribute = _FACT_RE.match(fact["text"]).group("attribute").strip().lower()
        stamp = (fact.get("metadata") or {}).get("timestamp") or 0
        kept = newest.get(attribute)
        if kept is None or stamp >= ((kept[1].get("metadata") or {}).get("timestamp") or 0):
            newest[attribute] = (rank, fact)

    chosen: list[dict] = []
    used = 0
    for _, (rank, fact) in sorted(newest.items(), key=lambda kv: (kv[1][0], kv[0])):
        if len(chosen) >= MAX_PROFILE_FACTS or used + len(fact["text"]) > MAX_PROFILE_CHARS:
            break
        chosen.append(fact)
        used += len(fact["text"])
    return chosen


def for_question(question: str, domain: str = "personal") -> list[dict]:
    """Core facts to accompany `question`, or [] when it is not about the user.
    Never raises: a broken store means no profile, not a failed answer."""
    if not refers_to_user(question):
        return []
    try:
        import memory
        return select(memory.current_facts(domain))
    except Exception as error:
        log.info("user_profile_unavailable", extra={"error": str(error)[:200]})
        return []


if __name__ == "__main__":
    import memory

    for item in select(memory.current_facts("personal")):
        print(f"- {item['text']}")
