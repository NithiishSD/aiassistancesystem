"""
Codeforces MCP Server for Zedek.

Exposes read-only Codeforces competitive programming tools.
All endpoints use the public Codeforces API — no authentication required.

Tools exposed:
    cf_user_info        — Rating, rank, and stats for a Codeforces handle.
    cf_user_submissions — Recent accepted submissions for a user.
    cf_problem_search   — Find problems by tag and/or difficulty rating.
    cf_contest_list     — List recent or upcoming contests.
    cf_problem_by_id    — Get details for a specific problem by contest+index.

Useful for:
    - Placement prep: track your Codeforces progress, find practice problems.
    - Study planning: search problems by topic (dp, graphs, greedy, etc.) and rating.
    - Contest scheduling: see what's coming up.

Run manually:
    python3 -m mcp_codeforces_server
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_CF_API = "https://codeforces.com/api"
_TIMEOUT = httpx.Timeout(connect=8.0, read=15.0, write=5.0, pool=5.0)


def _cf_get(endpoint: str, params: dict | None = None) -> dict | str:
    """GET from Codeforces API; returns parsed result or [error] string."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(
                f"{_CF_API}/{endpoint}",
                params=params,
                headers={"User-Agent": "Zedek-AI-Assistant/1.0"},
            )
    except httpx.TimeoutException:
        return "[error] Codeforces API request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"

    if resp.status_code != 200:
        return f"[error] Codeforces API returned HTTP {resp.status_code}."

    try:
        data = resp.json()
    except Exception:
        return "[error] Failed to parse Codeforces response."

    if data.get("status") != "OK":
        comment = data.get("comment", "Unknown error")
        return f"[error] Codeforces API error: {comment}"

    return data.get("result", {})


def _ts(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="codeforces_tools",
    version="1.0.0",
    instructions=(
        "Read-only Codeforces competitive programming tools. "
        "Useful for placement prep: track progress, find practice problems by topic/rating, "
        "check upcoming contests."
    ),
)


# ── Tool: cf_user_info ────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns rating, rank, max rating, and basic stats for one or more "
        "Codeforces handles."
    ),
)
def cf_user_info(handles: str) -> str:
    """Get Codeforces user info by handle(s).

    Args:
        handles: Semicolon-separated Codeforces handles (e.g. 'tourist' or 'tourist;Petr').
    """
    if not handles.strip():
        return "[error] Handle(s) must not be empty."

    result = _cf_get("user.info", {"handles": handles.strip()})
    if isinstance(result, str):
        return result

    lines = []
    for u in result:
        lines.append(
            f"**{u.get('handle', '?')}** — {u.get('rank', 'unrated').title()}\n"
            f"Rating: {u.get('rating', 'N/A')}  |  Max: {u.get('maxRating', 'N/A')} "
            f"({u.get('maxRank', '?').title()})\n"
            f"Friends: {u.get('friendOfCount', 0):,}  |  "
            f"Contribution: {u.get('contribution', 0)}\n"
            f"Organisation: {u.get('organization') or 'N/A'}  |  Country: {u.get('country') or 'N/A'}"
        )
    return "\n\n---\n\n".join(lines)


# ── Tool: cf_user_submissions ─────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns recent accepted submissions for a Codeforces user — problem name, "
        "contest, language, and solve time."
    ),
)
def cf_user_submissions(handle: str, max_results: int = 10) -> str:
    """Get recent accepted submissions for a Codeforces user.

    Args:
        handle: Codeforces handle (e.g. 'tourist').
        max_results: Number of recent submissions to return (1-20, default 10).
    """
    if not handle.strip():
        return "[error] Handle must not be empty."
    max_results = max(1, min(max_results, 20))

    result = _cf_get("user.status", {"handle": handle.strip(), "count": 50})
    if isinstance(result, str):
        return result

    accepted = [s for s in result if s.get("verdict") == "OK"][:max_results]
    if not accepted:
        return f"No accepted submissions found for '{handle}'."

    lines = []
    for s in accepted:
        prob = s.get("problem", {})
        name = f"{prob.get('contestId', '?')}{prob.get('index', '?')} — {prob.get('name', '?')}"
        rating = prob.get("rating", "?")
        lang = s.get("programmingLanguage", "?")
        when = _ts(s.get("creationTimeSeconds", 0))
        lines.append(f"✅ **{name}** (Rating: {rating})\nLang: {lang}  |  Solved: {when}")

    return f"Recent accepted submissions for **{handle}**:\n\n" + "\n\n".join(lines)


# ── Tool: cf_problem_search ───────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches Codeforces problems by topic tags and optional difficulty rating range. "
        "Useful for finding practice problems for placement prep."
    ),
)
def cf_problem_search(tags: str, min_rating: int = 0, max_rating: int = 3500,
                       max_results: int = 10) -> str:
    """Search Codeforces problems by tag and rating range.

    Args:
        tags: Semicolon-separated problem tags (e.g. 'dp', 'graphs;bfs', 'greedy;implementation').
        min_rating: Minimum difficulty rating (0-3500, default 0).
        max_rating: Maximum difficulty rating (0-3500, default 3500).
        max_results: Max problems to return (1-20, default 10).
    """
    if not tags.strip():
        return "[error] Tags must not be empty. Examples: 'dp', 'graphs', 'greedy'."
    max_results = max(1, min(max_results, 20))

    # Codeforces API uses semicolon-separated tags
    result = _cf_get("problemset.problems", {"tags": tags.strip()})
    if isinstance(result, str):
        return result

    problems = result.get("problems", [])

    # Filter by rating
    filtered = [
        p for p in problems
        if min_rating <= p.get("rating", 9999) <= max_rating
    ]

    if not filtered:
        return (
            f"No problems found for tags='{tags}' "
            f"with rating {min_rating}–{max_rating}."
        )

    # Sort by rating ascending for progressive practice
    filtered.sort(key=lambda p: p.get("rating", 9999))
    selected = filtered[:max_results]

    lines = []
    for p in selected:
        cid = p.get("contestId", "?")
        idx = p.get("index", "?")
        name = p.get("name", "?")
        rating = p.get("rating", "?")
        ptags = ", ".join(p.get("tags", []))
        url = f"https://codeforces.com/problemset/problem/{cid}/{idx}"
        lines.append(
            f"**{cid}{idx}. {name}** (Rating: {rating})\n"
            f"Tags: {ptags}\nURL: {url}"
        )

    return (
        f"Problems matching '{tags}' (rating {min_rating}–{max_rating}), "
        f"sorted by difficulty:\n\n" + "\n\n".join(lines)
    )


# ── Tool: cf_contest_list ─────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns a list of recent finished and upcoming Codeforces contests "
        "with start times and duration."
    ),
)
def cf_contest_list(show_upcoming: bool = True, max_results: int = 5) -> str:
    """List recent or upcoming Codeforces contests.

    Args:
        show_upcoming: If true, shows upcoming contests; if false, shows recent finished contests.
        max_results: Number of contests to return (1-10, default 5).
    """
    max_results = max(1, min(max_results, 10))

    result = _cf_get("contest.list", {"gym": False})
    if isinstance(result, str):
        return result

    if show_upcoming:
        contests = [c for c in result if c.get("phase") == "BEFORE"]
        contests.sort(key=lambda c: c.get("startTimeSeconds", 0))
        label = "Upcoming"
    else:
        contests = [c for c in result if c.get("phase") == "FINISHED"]
        contests.sort(key=lambda c: -c.get("startTimeSeconds", 0))
        label = "Recent finished"

    selected = contests[:max_results]
    if not selected:
        return f"No {label.lower()} contests found."

    lines = []
    for c in selected:
        name = c.get("name", "?")
        start = _ts(c.get("startTimeSeconds", 0))
        duration_h = c.get("durationSeconds", 0) // 3600
        duration_m = (c.get("durationSeconds", 0) % 3600) // 60
        cid = c.get("id", "?")
        url = f"https://codeforces.com/contest/{cid}"
        lines.append(
            f"**{name}**\nStart: {start}  |  Duration: {duration_h}h {duration_m}m\nURL: {url}"
        )

    return f"{label} Codeforces contests:\n\n" + "\n\n".join(lines)


# ── Tool: cf_problem_by_id ────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns details for a specific Codeforces problem by its contest ID "
        "and problem index, including name, rating, tags, and statement URL."
    ),
)
def cf_problem_by_id(contest_id: int, index: str) -> str:
    """Get a specific Codeforces problem by contest ID and index.

    Args:
        contest_id: The contest number (e.g. 1900).
        index: The problem index letter (e.g. 'A', 'B', 'C', 'D').
    """
    if not index.strip():
        return "[error] Index must be a letter like 'A', 'B', 'C'."

    result = _cf_get("contest.standings",
                      {"contestId": contest_id, "from": 1, "count": 1, "showUnofficial": False})
    if isinstance(result, str):
        return result

    problems = result.get("problems", [])
    target_idx = index.strip().upper()
    match = next((p for p in problems if p.get("index") == target_idx), None)

    if not match:
        return f"Problem {contest_id}{target_idx} not found."

    rating = match.get("rating", "unrated")
    ptags = ", ".join(match.get("tags", []))
    url = f"https://codeforces.com/problemset/problem/{contest_id}/{target_idx}"
    return (
        f"**{contest_id}{target_idx}. {match.get('name', '?')}**\n"
        f"Rating: {rating}\nTags: {ptags}\nStatement: {url}"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
