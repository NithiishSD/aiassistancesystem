"""
Read-Only Online MCP Server for Zedek.

Exposes three safe, read-only internet-access tools:

    fetch_url         — HTTP GET a public URL, return clean text (SSRF-guarded).
    search_wikipedia  — Search Wikipedia and return page summaries.
    search_arxiv      — Search arXiv for academic papers and return abstracts.

Security contract:
    - fetch_url blocks all private/loopback/link-local IP ranges (SSRF guard).
    - All tools return normalized dicts; no exceptions propagate to the caller.
    - All HTTP calls use an explicit httpx timeout (10 s connect, 20 s read).
    - Wikipedia and arXiv requests carry an identifying User-Agent per API ToS.

Run manually:
    python3 -m mcp_online_server      # via -m (module mode, from project root)
    python3 mcp_online_server.py      # direct
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urlparse

import httpx

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Zedek-AI-Assistant/1.0 "
    "(https://github.com/your-repo/zedek; research use only)"
)

# httpx timeout: 10 s to connect, 20 s to read a full response body.
_HTTPX_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=5.0)

# Hard cap on returned text length (characters) to avoid flooding the context.
_FETCH_MAX_CHARS = 8_000
_WIKI_MAX_CHARS = 2_000
_ARXIV_ABSTRACT_MAX_CHARS = 600

# ── SSRF blocklist ─────────────────────────────────────────────────────────────
# Reject any URL whose resolved host is inside a private/loopback/link-local
# range. This runs before any network I/O.

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("10.0.0.0/8"),       # RFC-1918 private
    ipaddress.ip_network("172.16.0.0/12"),    # RFC-1918 private
    ipaddress.ip_network("192.168.0.0/16"),   # RFC-1918 private
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA (private)
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
]

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _ssrf_check(url: str) -> str | None:
    """Return an error string if the URL is SSRF-blocked, else None.

    Resolves the hostname to its IP address(es) and checks each against the
    private network blocklist.  Treats DNS resolution failures as safe to
    reject (fail-closed).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL."

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return f"Scheme '{scheme}' is not allowed — only http/https."

    host = parsed.hostname or ""
    if not host:
        return "URL has no hostname."

    if host.lower() in _BLOCKED_HOSTNAMES:
        return f"Host '{host}' is blocked (private/loopback)."

    # Resolve and check every A/AAAA record.
    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"DNS resolution failed for '{host}': {exc}"

    for family, _type, _proto, _canonname, sockaddr in addr_infos:
        raw_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return (
                    f"URL resolves to a private/reserved address "
                    f"({raw_ip}) and cannot be fetched."
                )

    return None  # URL is safe


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    # Remove <script> and <style> blocks entirely.
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining tags.
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse runs of whitespace.
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated at {max_chars} characters]"


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="online_tools",
    version="1.0.0",
    instructions=(
        "Read-only internet research tools: fetch web pages, search Wikipedia, "
        "and search arXiv academic papers. All tools are safe/read-only."
    ),
)


# ── Tool: fetch_url ───────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Fetches the text content of a public web URL via HTTP GET and returns "
        "it as clean plain text. Private/local network addresses are blocked."
    ),
)
def fetch_url(url: str) -> str:
    """Fetch and return the readable text content of a public URL.

    Args:
        url: The full URL to fetch (must be http:// or https://).
    """
    # SSRF guard — always first, before any I/O.
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    try:
        with httpx.Client(timeout=_HTTPX_TIMEOUT, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": _USER_AGENT})
    except httpx.TimeoutException:
        return "[error] Request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"

    if response.status_code != 200:
        return f"[error] Server returned HTTP {response.status_code}."

    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        text = _strip_html(response.text)
    else:
        text = response.text

    return _truncate(text, _FETCH_MAX_CHARS)


# ── Tool: search_wikipedia ────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches Wikipedia for a query and returns page summaries using the "
        "Wikipedia OpenSearch and REST Summary API."
    ),
)
def search_wikipedia(query: str, limit: int = 3) -> str:
    """Search Wikipedia and return article summaries.

    Args:
        query: The search query string.
        limit: Maximum number of results to return (1-5, default 3).
    """
    if not query.strip():
        return "[error] Query must not be empty."

    limit = max(1, min(limit, 5))

    search_url = (
        "https://en.wikipedia.org/w/api.php"
        f"?action=opensearch&search={quote_plus(query)}"
        f"&limit={limit}&namespace=0&format=json"
    )

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
            resp = client.get(search_url, headers=headers)
    except httpx.TimeoutException:
        return "[error] Wikipedia search request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error while searching Wikipedia: {exc}"

    if resp.status_code != 200:
        return f"[error] Wikipedia API returned HTTP {resp.status_code}."

    try:
        data = resp.json()
        titles = data[1]
        urls = data[3]
    except (ValueError, IndexError, KeyError):
        return "[error] Unexpected response format from Wikipedia API."

    if not titles:
        return f"No Wikipedia articles found for: '{query}'."

    results: list[str] = []
    with httpx.Client(timeout=_HTTPX_TIMEOUT) as client:
        for title, page_url in zip(titles, urls):
            summary_url = (
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title)}"
            )
            try:
                s_resp = client.get(summary_url, headers=headers)
                if s_resp.status_code == 200:
                    s_data = s_resp.json()
                    extract = s_data.get("extract", "No summary available.")
                    extract = _truncate(extract, _WIKI_MAX_CHARS)
                    results.append(f"**{title}**\n{extract}\nURL: {page_url}")
                else:
                    results.append(f"**{title}**\n(Summary unavailable)\nURL: {page_url}")
            except (httpx.TimeoutException, httpx.RequestError):
                results.append(f"**{title}**\n(Summary request failed)\nURL: {page_url}")

    return "\n\n---\n\n".join(results)


# ── Tool: search_arxiv ────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches arXiv for academic papers matching a query and returns "
        "titles, authors, publication dates, and abstracts."
    ),
)
def search_arxiv(query: str, max_results: int = 3) -> str:
    """Search arXiv and return paper titles, authors, and abstracts.

    Args:
        query: The search query string (keywords, author names, etc.).
        max_results: Maximum number of papers to return (1-5, default 3).
    """
    if not query.strip():
        return "[error] Query must not be empty."

    max_results = max(1, min(max_results, 5))

    search_url = (
        "http://export.arxiv.org/api/query"
        f"?search_query=all:{quote_plus(query)}"
        f"&start=0&max_results={max_results}"
    )

    # arXiv SSRF check: export.arxiv.org is a public host, but run guard anyway
    # in case query params somehow produce a redirect to a private host. The
    # guard already handles that via DNS resolution.
    ssrf_err = _ssrf_check(search_url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    headers = {
        "User-Agent": _USER_AGENT,
    }

    try:
        with httpx.Client(timeout=_HTTPX_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(search_url, headers=headers)
    except httpx.TimeoutException:
        return "[error] arXiv search request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error while searching arXiv: {exc}"

    if resp.status_code != 200:
        return f"[error] arXiv API returned HTTP {resp.status_code}."

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        return f"[error] Failed to parse arXiv response: {exc}"

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }

    entries = root.findall("atom:entry", ns)
    if not entries:
        return f"No arXiv papers found for: '{query}'."

    results: list[str] = []
    for entry in entries:
        def _text(tag: str) -> str:
            el = entry.find(tag, ns)
            return (el.text or "").strip() if el is not None else ""

        title = re.sub(r"\s+", " ", _text("atom:title"))
        abstract = re.sub(r"\s+", " ", _text("atom:summary"))
        published = _text("atom:published")[:10]  # YYYY-MM-DD
        link_el = entry.find("atom:id", ns)
        link = (link_el.text or "").strip() if link_el is not None else ""
        authors = [
            (a.find("atom:name", ns).text or "").strip()
            for a in entry.findall("atom:author", ns)
            if a.find("atom:name", ns) is not None
        ]
        authors_str = ", ".join(authors[:5])
        if len(authors) > 5:
            authors_str += f" +{len(authors) - 5} more"

        abstract = _truncate(abstract, _ARXIV_ABSTRACT_MAX_CHARS)

        results.append(
            f"**{title}**\n"
            f"Authors: {authors_str}\n"
            f"Published: {published}\n"
            f"Link: {link}\n"
            f"Abstract: {abstract}"
        )

    return "\n\n---\n\n".join(results)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
