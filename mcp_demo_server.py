"""
Bundled Zedek MCP Demo Server.

A genuine MCP server (real STDIO transport, real MCP protocol) that ships
with Zedek to demonstrate and test the full MCP pipeline end-to-end without
requiring Node.js, npx, or any network tool server.

Run manually:
    python3 -m mcp_demo_server      # via -m (module mode, from project root)
    python3 mcp_demo_server.py      # direct

Tools exposed:
    current_time      — Returns the current date and time as a string.
    word_count        — Returns word, character, and line count for input text.
    summarize_text    — Returns a brief truncated summary of the input text.
"""

import asyncio
from datetime import datetime

from mcp.server.mcpserver import MCPServer

# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="zedek_tools",
    version="1.0.0",
    instructions="Bundled Zedek utility tools for demonstration and testing.",
)


# ── Tool: current_time ────────────────────────────────────────────────────────

@_server.tool(
    description="Returns the current local date and time as a human-readable string.",
)
def current_time() -> str:
    """Return the current date and time."""
    return datetime.now().strftime("%A, %d %B %Y - %H:%M:%S")


# ── Tool: word_count ──────────────────────────────────────────────────────────

@_server.tool(
    description="Counts the number of words, characters, and lines in the given text.",
)
def word_count(text: str) -> str:
    """Count words, characters, and lines in text.

    Args:
        text: The text to analyse.
    """
    if not text:
        return "Words: 0, Chars: 0, Lines: 0"
    words = len(text.split())
    chars = len(text)
    lines = len(text.splitlines()) or 1
    return f"Words: {words}, Chars: {chars}, Lines: {lines}"


# ── Tool: summarize_text ──────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns a brief summary of the provided text: the first sentence "
        "followed by a word count."
    ),
)
def summarize_text(text: str) -> str:
    """Produce a brief summary of the input text.

    Args:
        text: The text to summarize.
    """
    if not text:
        return "(empty text)"
    text = text.strip()
    # Find first sentence boundary (. ! ?)
    for i, ch in enumerate(text):
        if ch in ".!?" and i > 0:
            first_sentence = text[: i + 1].strip()
            total_words = len(text.split())
            return f"{first_sentence} ... [{total_words} words total]"
    # No sentence boundary found — truncate at 20 words
    words = text.split()
    if len(words) <= 20:
        return text
    return " ".join(words[:20]) + f" ... [{len(words)} words total]"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
