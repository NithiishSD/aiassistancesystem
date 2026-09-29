"""Sanitize untrusted text before any language model reads it (ROADMAP B1 / B6).

Block's red team got code execution through its own agent with instructions
hidden in zero-width Unicode inside calendar invites. The user saw nothing and
the model read everything. Stripping these characters is cheap and closes that
specific channel. It is not a prompt-injection defense on its own; the
structural defense is keeping untrusted text away from any model that chooses
actions.

Call sites: the web agent (page text), the research agent (sources), and
mcp_client (tool results, and tool descriptions via sanitize_tool_description).
"""

from __future__ import annotations

import re
import unicodedata

# Zero-width (U+200B-U+200D), word joiner (U+2060), BOM/zero-width no-break space
# (U+FEFF), bidi embedding/override (U+202A-U+202E), bidi isolates (U+2066-U+2069).
_INVISIBLE_RE = re.compile("[​-‍⁠﻿‪-‮⁦-⁩]")


def strip_invisible(text: str | None) -> str:
    """Remove invisible and bidi-control characters, then NFKC-normalize."""
    if not text:
        return ""
    return unicodedata.normalize("NFKC", _INVISIBLE_RE.sub("", text))


# ── MCP tool descriptions (ROADMAP B2) ──────────────────────────────────────
# Third-party tool descriptions reach the tool-selection prompt and the router.
# The documented poisoning attacks hide instructions to the model in them
# (Invariant Labs' <IMPORTANT> blocks, "rug pulls", tool shadowing). These
# patterns target instruction-to-the-model shapes, not descriptions of what a
# tool does; every bundled tool description passes through unchanged.
MAX_TOOL_DESCRIPTION_CHARS = 300

_TAG_BLOCK_RE = re.compile(r"<\s*([A-Za-z][\w-]*)[^>]*>.*?<\s*/\s*\1\s*>", re.DOTALL)
_STRAY_TAG_RE = re.compile(r"<[^<>]{0,200}>")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_INSTRUCTION_RES = [re.compile(p, re.IGNORECASE) for p in (
    # override: "ignore all previous instructions"
    r"\b(ignore|disregard|forget|override)\b.*\b(previous|prior|above|earlier|other|all|system)\b"
    r".*\b(instruction|rule|prompt|message|direction)s?\b",
    # obligation addressed to the model
    r"\byou\s+(must|should|need\s+to|have\s+to|are\s+required\s+to|will)\b",
    r"\b(assistant|ai|model|llm)\s+(must|should)\b",
    # concealment from the user
    r"\b(do\s+not|don't|never|without)\b.*\b(tell|mention|inform|reveal|show|notify|alert)(ing)?\b.*\buser\b",
    # sequencing directives
    r"\b(before|after|instead\s+of|prior\s+to)\s+(using|calling|running|invoking|executing)\b",
    r"\b(always|first)\s+(call|run|use|invoke|execute|read|send)\b",
    # secret files and config
    r"~/|/etc/|\.ssh\b|id_rsa|\.env\b|private\s+key|mcp\.json|mcp_servers\.json",
    # role markers
    r"^\s*(system|assistant|user)\s*:",
    r"\bsystem\s+prompt\b",
)]


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut or text[:limit]


def sanitize_tool_description(text: str | None,
                              other_tool_names: tuple[str, ...] | list[str] = ()) -> tuple[str, int]:
    """Model-facing version of an untrusted tool description.

    Returns (clean_text, removed_count). Removes tag blocks, then drops any
    sentence that addresses the model (see _INSTRUCTION_RES) or names another
    tool (shadowing). The tier gate must NOT use this: it only raises risk, so
    it gets the full text via strip_invisible instead.
    """
    cleaned = strip_invisible(text)
    if not cleaned:
        return "", 0

    removed = 0
    cleaned, blocks = _TAG_BLOCK_RE.subn(" ", cleaned)
    removed += blocks
    cleaned, tags = _STRAY_TAG_RE.subn(" ", cleaned)
    removed += tags

    shadow_res = [re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
                  for name in other_tool_names if name]
    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(cleaned):
        sentence = sentence.strip()
        if not sentence:
            continue
        if any(r.search(sentence) for r in _INSTRUCTION_RES) or any(r.search(sentence) for r in shadow_res):
            removed += 1
            continue
        kept.append(sentence)

    result = " ".join(" ".join(kept).split())
    return _cap(result, MAX_TOOL_DESCRIPTION_CHARS), removed


def model_facing_description(spec) -> str:
    """The only form of a tool's description a model or the router may see.

    Discovery precomputes `prompt_description` (with the other tool names for
    the shadowing check). A spec built without it is sanitized here, so raw
    third-party text never reaches a prompt by default.
    """
    text = getattr(spec, "prompt_description", None)
    if isinstance(text, str):
        return text
    raw = getattr(spec, "description", "")
    return sanitize_tool_description(raw if isinstance(raw, str) else "")[0]
