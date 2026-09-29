"""Sanitize untrusted text before any language model reads it (ROADMAP B1 / B6).

Block's red team got code execution through its own agent with instructions
hidden in zero-width Unicode inside calendar invites. The user saw nothing and
the model read everything. Stripping these characters is cheap and closes that
specific channel. It is not a prompt-injection defense on its own; the
structural defense is keeping untrusted text away from any model that chooses
actions.

Shared helper: the web agent uses it now; the research agent and MCP tool
descriptions are the remaining B6 call sites.
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
