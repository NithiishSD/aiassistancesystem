"""
GitHub Read-Only MCP Server for Zedek.

Exposes read-only GitHub API tools for repository and code discovery.
No write operations — this server never modifies any repository.

Authentication:
    Without GITHUB_TOKEN env var: 60 req/hour (unauthenticated).
    With GITHUB_TOKEN env var:   5000 req/hour (authenticated, recommended).

Tools exposed:
    github_search_repos   — Search GitHub repositories by keyword.
    github_search_code    — Search code across GitHub (requires auth for full results).
    github_get_repo       — Get metadata for a specific repository.
    github_get_readme     — Fetch the README of a repository.

Run manually:
    python3 -m mcp_github_server
"""

from __future__ import annotations

import asyncio
import base64
import os

import httpx

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com"
_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_HEADERS: dict[str, str] = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Zedek-AI-Assistant/1.0",
}
if _TOKEN:
    _HEADERS["Authorization"] = f"Bearer {_TOKEN}"

_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=5.0)
_MAX_CHARS = 4_000


def _truncate(text: str, max_chars: int = _MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated at {max_chars} characters]"


def _get(path: str, params: dict | None = None) -> dict | str:
    """GET from GitHub API; returns parsed JSON dict or [error] string."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{_GITHUB_API}{path}", headers=_HEADERS, params=params)
    except httpx.TimeoutException:
        return "[error] GitHub API request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"

    if resp.status_code == 403:
        return "[error] GitHub API rate limit exceeded. Set GITHUB_TOKEN env var for higher limits."
    if resp.status_code == 401:
        return "[error] GitHub token invalid or expired."
    if resp.status_code == 422:
        return "[error] Code search requires authentication. Set GITHUB_TOKEN env var."
    if resp.status_code not in (200, 201):
        return f"[error] GitHub API returned HTTP {resp.status_code}."

    try:
        return resp.json()
    except Exception:
        return f"[error] Failed to parse GitHub response."


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="github_tools",
    version="1.0.0",
    instructions=(
        "Read-only GitHub API tools for repository search, code search, "
        "and README retrieval. Never modifies any repository."
    ),
)


# ── Tool: github_search_repos ─────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches GitHub repositories by keyword and returns name, description, "
        "stars, language, and URL for matching repositories."
    ),
)
def github_search_repos(query: str, max_results: int = 5) -> str:
    """Search GitHub repositories by keyword.

    Args:
        query: Search keywords (e.g. 'python MCP server', 'react dashboard').
        max_results: Max repositories to return (1-10, default 5).
    """
    if not query.strip():
        return "[error] Query must not be empty."
    max_results = max(1, min(max_results, 10))

    data = _get("/search/repositories", {
        "q": query, "sort": "stars", "order": "desc", "per_page": max_results,
    })
    if isinstance(data, str):
        return data

    items = data.get("items", [])
    if not items:
        return f"No repositories found for: '{query}'."

    results = []
    for r in items:
        results.append(
            f"**{r.get('full_name', '?')}** ⭐{r.get('stargazers_count', 0):,}\n"
            f"Language: {r.get('language') or 'N/A'}\n"
            f"Description: {r.get('description') or 'No description.'}\n"
            f"URL: {r.get('html_url', '')}"
        )
    return "\n\n---\n\n".join(results)


# ── Tool: github_search_code ──────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches code across GitHub repositories and returns file paths, "
        "repository names, and links to matching code. Requires GITHUB_TOKEN."
    ),
)
def github_search_code(query: str, max_results: int = 5) -> str:
    """Search code files across GitHub.

    Args:
        query: Code search query (e.g. 'asyncio.run mcp', 'tier_gate classify').
        max_results: Max results to return (1-10, default 5).
    """
    if not query.strip():
        return "[error] Query must not be empty."
    max_results = max(1, min(max_results, 10))

    if not _TOKEN:
        return (
            "[error] GitHub code search requires authentication. "
            "Set the GITHUB_TOKEN environment variable and restart the server."
        )

    data = _get("/search/code", {"q": query, "per_page": max_results})
    if isinstance(data, str):
        return data

    items = data.get("items", [])
    if not items:
        return f"No code found for: '{query}'."

    results = []
    for item in items:
        repo = item.get("repository", {})
        results.append(
            f"**{item.get('name', '?')}** in `{repo.get('full_name', '?')}`\n"
            f"Path: {item.get('path', '?')}\n"
            f"URL: {item.get('html_url', '')}"
        )
    return "\n\n---\n\n".join(results)


# ── Tool: github_get_repo ─────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns metadata for a specific GitHub repository including description, "
        "stars, forks, language, topics, and license."
    ),
)
def github_get_repo(owner: str, repo: str) -> str:
    """Get metadata for a specific GitHub repository.

    Args:
        owner: The repository owner (username or organization).
        repo: The repository name.
    """
    if not owner.strip() or not repo.strip():
        return "[error] Both owner and repo must be provided."

    data = _get(f"/repos/{owner}/{repo}")
    if isinstance(data, str):
        return data

    topics = ", ".join(data.get("topics", [])) or "none"
    license_name = (data.get("license") or {}).get("name", "None")
    return (
        f"**{data.get('full_name', '?')}**\n"
        f"Description: {data.get('description') or 'No description.'}\n"
        f"Stars: {data.get('stargazers_count', 0):,}  |  "
        f"Forks: {data.get('forks_count', 0):,}  |  "
        f"Open Issues: {data.get('open_issues_count', 0):,}\n"
        f"Language: {data.get('language') or 'N/A'}\n"
        f"Topics: {topics}\n"
        f"License: {license_name}\n"
        f"Default Branch: {data.get('default_branch', 'main')}\n"
        f"URL: {data.get('html_url', '')}"
    )


# ── Tool: github_get_readme ───────────────────────────────────────────────────

@_server.tool(
    description=(
        "Fetches and returns the README of a GitHub repository as plain text."
    ),
)
def github_get_readme(owner: str, repo: str) -> str:
    """Fetch the README of a GitHub repository.

    Args:
        owner: The repository owner (username or organization).
        repo: The repository name.
    """
    if not owner.strip() or not repo.strip():
        return "[error] Both owner and repo must be provided."

    data = _get(f"/repos/{owner}/{repo}/readme")
    if isinstance(data, str):
        return data

    encoded = data.get("content", "")
    if not encoded:
        return "No README found."

    try:
        decoded = base64.b64decode(encoded.replace("\n", "")).decode("utf-8", errors="replace")
    except Exception as exc:
        return f"[error] Failed to decode README: {exc}"

    return _truncate(decoded)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
