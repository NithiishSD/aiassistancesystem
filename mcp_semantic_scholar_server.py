"""
Semantic Scholar MCP Server for Zedek.

Exposes read-only academic paper search tools via the Semantic Scholar API.
All endpoints use the public API — no authentication required for basic access.
API key (via SEMANTIC_SCHOLAR_API_KEY env var) gives higher rate limits.

Tools exposed:
    scholar_search_papers   — Search papers by keyword, with abstracts and citations.
    scholar_paper_details   — Full details for a specific paper by DOI or S2 ID.
    scholar_paper_citations — Papers that cite a given paper.
    scholar_author_papers   — Papers by a specific author.

Useful for:
    - Academic research context for any topic.
    - Finding papers related to DSA concepts, AI/ML research, placement prep resources.
    - Discovering key references in a field.

Run manually:
    python3 -m mcp_semantic_scholar_server
"""

from __future__ import annotations

import asyncio
import os

import httpx

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_S2_API = "https://api.semanticscholar.org/graph/v1"
_API_KEY = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")
_HEADERS: dict[str, str] = {"User-Agent": "Zedek-AI-Assistant/1.0"}
if _API_KEY:
    _HEADERS["x-api-key"] = _API_KEY

_TIMEOUT = httpx.Timeout(connect=8.0, read=20.0, write=5.0, pool=5.0)
_MAX_ABSTRACT_CHARS = 800


def _s2_get(path: str, params: dict | None = None) -> dict | str:
    """GET from Semantic Scholar API; returns parsed JSON or [error] string."""
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{_S2_API}{path}", headers=_HEADERS, params=params)
    except httpx.TimeoutException:
        return "[error] Semantic Scholar request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"

    if resp.status_code == 429:
        return (
            "[error] Semantic Scholar rate limit exceeded. "
            "Set SEMANTIC_SCHOLAR_API_KEY env var for higher limits "
            "(free key at semanticscholar.org/product/api)."
        )
    if resp.status_code not in (200, 201):
        return f"[error] Semantic Scholar returned HTTP {resp.status_code}."

    try:
        return resp.json()
    except Exception:
        return "[error] Failed to parse Semantic Scholar response."


def _fmt_paper(p: dict, show_abstract: bool = True) -> str:
    authors = ", ".join(
        a.get("name", "?") for a in (p.get("authors") or [])[:4]
    )
    if len(p.get("authors") or []) > 4:
        authors += " et al."
    year = p.get("year") or "?"
    citations = p.get("citationCount", 0)
    abstract = (p.get("abstract") or "").strip()
    if show_abstract and abstract:
        if len(abstract) > _MAX_ABSTRACT_CHARS:
            abstract = abstract[:_MAX_ABSTRACT_CHARS] + "..."
        abstract_line = f"Abstract: {abstract}\n"
    else:
        abstract_line = ""
    doi = p.get("externalIds", {}).get("DOI", "")
    url = p.get("url") or (f"https://doi.org/{doi}" if doi else "N/A")
    s2id = p.get("paperId", "")
    return (
        f"**{p.get('title', 'No title')}** ({year})\n"
        f"Authors: {authors or 'Unknown'}\n"
        f"Citations: {citations:,}\n"
        f"{abstract_line}"
        f"URL: {url}"
        + (f"\nS2 ID: {s2id}" if s2id else "")
    )


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="semantic_scholar_tools",
    version="1.0.0",
    instructions=(
        "Read-only academic research tools via Semantic Scholar. "
        "Search papers by keyword, look up citations, and find author publications. "
        "Set SEMANTIC_SCHOLAR_API_KEY for higher rate limits."
    ),
)


# ── Tool: scholar_search_papers ───────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches academic papers on Semantic Scholar by keyword and returns "
        "titles, authors, year, citation count, and abstract excerpts."
    ),
)
def scholar_search_papers(query: str, max_results: int = 5,
                           year_from: int = 0) -> str:
    """Search academic papers by keyword.

    Args:
        query: Search keywords (e.g. 'transformer attention mechanism', 'dynamic programming optimization').
        max_results: Number of papers to return (1-10, default 5).
        year_from: Only return papers from this year onward (0 = no filter).
    """
    if not query.strip():
        return "[error] Query must not be empty."
    max_results = max(1, min(max_results, 10))

    fields = "title,authors,year,abstract,citationCount,externalIds,url"
    params: dict = {"query": query.strip(), "limit": max_results, "fields": fields}
    if year_from > 0:
        params["year"] = f"{year_from}-"

    data = _s2_get("/paper/search", params)
    if isinstance(data, str):
        return data

    papers = data.get("data", [])
    if not papers:
        return f"No papers found for: '{query}'."

    return f"Papers matching '{query}':\n\n" + "\n\n---\n\n".join(
        _fmt_paper(p) for p in papers
    )


# ── Tool: scholar_paper_details ───────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns full details for a specific academic paper identified by its "
        "Semantic Scholar paper ID or DOI."
    ),
)
def scholar_paper_details(paper_id: str) -> str:
    """Get full details for a specific paper by Semantic Scholar ID or DOI.

    Args:
        paper_id: A Semantic Scholar paper ID (40-char hex) or DOI (e.g. '10.1145/3290605.3300710').
    """
    if not paper_id.strip():
        return "[error] paper_id must not be empty."

    pid = paper_id.strip()
    # DOIs need the DOI: prefix for the S2 lookup endpoint
    if pid.startswith("10."):
        pid = f"DOI:{pid}"

    fields = "title,authors,year,abstract,citationCount,referenceCount,externalIds,url,venue"
    data = _s2_get(f"/paper/{pid}", {"fields": fields})
    if isinstance(data, str):
        return data

    venue = data.get("venue") or "?"
    ref_count = data.get("referenceCount", 0)
    result = _fmt_paper(data, show_abstract=True)
    return result + f"\nVenue: {venue}  |  References: {ref_count:,}"


# ── Tool: scholar_paper_citations ─────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns papers that cite a given Semantic Scholar paper, "
        "showing which later works built on it."
    ),
)
def scholar_paper_citations(paper_id: str, max_results: int = 5) -> str:
    """List papers that cite a specific paper.

    Args:
        paper_id: Semantic Scholar paper ID or DOI.
        max_results: Number of citing papers to return (1-10, default 5).
    """
    if not paper_id.strip():
        return "[error] paper_id must not be empty."
    max_results = max(1, min(max_results, 10))

    pid = paper_id.strip()
    if pid.startswith("10."):
        pid = f"DOI:{pid}"

    fields = "title,authors,year,citationCount,url"
    data = _s2_get(f"/paper/{pid}/citations",
                   {"fields": fields, "limit": max_results})
    if isinstance(data, str):
        return data

    citations = data.get("data", [])
    if not citations:
        return f"No citing papers found for paper ID '{paper_id}'."

    papers = [c.get("citingPaper", {}) for c in citations]
    return f"Papers citing '{paper_id}':\n\n" + "\n\n---\n\n".join(
        _fmt_paper(p, show_abstract=False) for p in papers
    )


# ── Tool: scholar_author_papers ───────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches for an academic author by name and returns their most-cited "
        "papers with titles, years, and citation counts."
    ),
)
def scholar_author_papers(author_name: str, max_results: int = 5) -> str:
    """Find papers by a specific academic author.

    Args:
        author_name: Full or partial author name (e.g. 'Yoshua Bengio', 'Geoffrey Hinton').
        max_results: Number of papers to return (1-10, default 5).
    """
    if not author_name.strip():
        return "[error] Author name must not be empty."
    max_results = max(1, min(max_results, 10))

    # Step 1: find the author
    a_data = _s2_get("/author/search",
                     {"query": author_name.strip(), "limit": 1,
                      "fields": "name,authorId,paperCount,citationCount"})
    if isinstance(a_data, str):
        return a_data

    authors = a_data.get("data", [])
    if not authors:
        return f"No author found matching: '{author_name}'."

    author = authors[0]
    author_id = author.get("authorId", "")
    author_full_name = author.get("name", author_name)

    if not author_id:
        return f"Could not resolve author ID for '{author_name}'."

    # Step 2: get their papers
    p_data = _s2_get(
        f"/author/{author_id}/papers",
        {"fields": "title,year,citationCount,url", "limit": max_results,
         "sort": "citationCount"},
    )
    if isinstance(p_data, str):
        return p_data

    papers = p_data.get("data", [])
    if not papers:
        return f"No papers found for author '{author_full_name}'."

    header = (
        f"**{author_full_name}** — "
        f"{author.get('paperCount', '?')} papers, "
        f"{author.get('citationCount', 0):,} total citations\n\n"
        f"Top papers:\n\n"
    )
    return header + "\n\n---\n\n".join(_fmt_paper(p, show_abstract=False) for p in papers)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
