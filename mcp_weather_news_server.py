"""
Weather & News MCP Server for Zedek.

Exposes real-time weather and news tools via the MCP protocol.

Tools exposed:
    get_weather    — Current weather for any city via wttr.in (no API key needed).
    get_weather_forecast — 3-day forecast for any city.
    search_news    — Search recent news headlines via NewsAPI.org.
                     Requires NEWS_API_KEY env var.

Authentication:
    get_weather / get_weather_forecast: No API key required (wttr.in is public).
    search_news: Requires NEWS_API_KEY env var (free tier: newsapi.org).
                 Without the key, returns a clear error explaining how to set it.

Run manually:
    python3 -m mcp_weather_news_server
"""

from __future__ import annotations

import asyncio
import os
from urllib.parse import quote

import httpx

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
_TIMEOUT = httpx.Timeout(connect=8.0, read=15.0, write=5.0, pool=5.0)
_MAX_CHARS = 4_000


def _truncate(text: str, max_chars: int = _MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated at {max_chars} characters]"


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="weather_news_tools",
    version="1.0.0",
    instructions=(
        "Real-time weather (no key required) and news search tools. "
        "News search requires NEWS_API_KEY environment variable (free at newsapi.org)."
    ),
)


# ── Tool: get_weather ─────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns current weather conditions for any city or location. "
        "No API key required — uses the public wttr.in service."
    ),
)
def get_weather(location: str) -> str:
    """Get current weather for a city or location.

    Args:
        location: City name or location (e.g. 'Chennai', 'New York', 'Tokyo').
    """
    if not location.strip():
        return "[error] Location must not be empty."

    url = f"https://wttr.in/{quote(location.strip())}?format=4&lang=en"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, headers={"User-Agent": "Zedek-AI-Assistant/1.0"})
        if resp.status_code != 200:
            return f"[error] wttr.in returned HTTP {resp.status_code}."
        text = resp.text.strip()
        if not text or "Unknown location" in text:
            return f"Could not find weather for '{location}'. Try a different city name."
        return text
    except httpx.TimeoutException:
        return "[error] Weather request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"


# ── Tool: get_weather_forecast ────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns a 3-day weather forecast for any city or location including "
        "temperature highs/lows and conditions each day."
    ),
)
def get_weather_forecast(location: str) -> str:
    """Get a 3-day weather forecast for a city or location.

    Args:
        location: City name or location (e.g. 'Mumbai', 'London').
    """
    if not location.strip():
        return "[error] Location must not be empty."

    # wttr.in 'v2' format gives structured forecast
    url = f"https://wttr.in/{quote(location.strip())}?format=%l:+%c+%t+%w&lang=en"
    # Use the detailed multi-line format for forecast
    url_full = f"https://wttr.in/{quote(location.strip())}?1n&lang=en"
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url_full, headers={"User-Agent": "Zedek-AI-Assistant/1.0"})
        if resp.status_code != 200:
            return f"[error] wttr.in returned HTTP {resp.status_code}."
        text = resp.text.strip()
        if not text or "Unknown location" in text:
            return f"Could not find forecast for '{location}'."
        return _truncate(text)
    except httpx.TimeoutException:
        return "[error] Forecast request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"


# ── Tool: search_news ─────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Searches recent news headlines and articles by keyword using NewsAPI. "
        "Returns title, source, published date, and URL for each article. "
        "Requires NEWS_API_KEY environment variable."
    ),
)
def search_news(query: str, max_results: int = 5, language: str = "en") -> str:
    """Search recent news articles by keyword.

    Args:
        query: Keywords to search for (e.g. 'AI safety', 'India cricket', 'budget 2025').
        max_results: Number of articles to return (1-10, default 5).
        language: Language code for results (default 'en').
    """
    if not query.strip():
        return "[error] Query must not be empty."

    if not _NEWS_API_KEY:
        return (
            "[error] NEWS_API_KEY environment variable is not set. "
            "Get a free key at https://newsapi.org/register and set it with: "
            "export NEWS_API_KEY=your_key_here"
        )

    max_results = max(1, min(max_results, 10))

    url = "https://newsapi.org/v2/everything"
    params = {
        "q": query.strip(),
        "language": language,
        "sortBy": "publishedAt",
        "pageSize": max_results,
        "apiKey": _NEWS_API_KEY,
    }

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(url, params=params,
                              headers={"User-Agent": "Zedek-AI-Assistant/1.0"})
    except httpx.TimeoutException:
        return "[error] NewsAPI request timed out."
    except httpx.RequestError as exc:
        return f"[error] Network error: {exc}"

    if resp.status_code == 401:
        return "[error] Invalid NEWS_API_KEY. Check your key at newsapi.org."
    if resp.status_code == 429:
        return "[error] NewsAPI rate limit exceeded (100 req/day on free tier)."
    if resp.status_code != 200:
        return f"[error] NewsAPI returned HTTP {resp.status_code}."

    try:
        data = resp.json()
    except Exception:
        return "[error] Failed to parse NewsAPI response."

    if data.get("status") != "ok":
        return f"[error] NewsAPI error: {data.get('message', 'Unknown error')}."

    articles = data.get("articles", [])
    if not articles:
        return f"No recent news found for: '{query}'."

    results = []
    for a in articles:
        source = (a.get("source") or {}).get("name", "Unknown source")
        pub = a.get("publishedAt", "")[:10]  # YYYY-MM-DD only
        results.append(
            f"**{a.get('title', 'No title')}**\n"
            f"Source: {source}  |  Published: {pub}\n"
            f"URL: {a.get('url', '')}"
        )
    return "\n\n---\n\n".join(results)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
