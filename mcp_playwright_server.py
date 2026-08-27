"""
Playwright Browser Automation MCP Server for Zedek.

Exposes browser automation tools via the MCP protocol.

RISK LEVEL: Tier 2 (declared in mcp_servers.json via "default_tier": 2).
All tools require explicit user confirmation before execution — this is
enforced by the tier gate, not by this server itself.

Tools exposed:
    browser_navigate   — Navigate to a URL in a headless browser page.
    browser_click      — Click an element identified by a CSS selector.
    browser_type       — Type text into an input element.
    browser_get_text   — Read the text content of a page or element.
    browser_screenshot — Capture a screenshot as a base64 PNG.

Design:
    - Every tool call opens a fresh headless Chromium browser, performs the
      action, and closes it. Stateless per-call: no persistent session.
    - All tools are synchronous at the MCP protocol level (async internals).
    - browser_navigate enforces the same SSRF blocklist as mcp_online_server.
    - All tools return graceful [error] strings on failure — no exceptions.
    - User-Agent identifies the tool for server logs.

Run manually:
    python3 -m mcp_playwright_server
    python3 mcp_playwright_server.py
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import socket
import tempfile
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer

# ── Constants ─────────────────────────────────────────────────────────────────

_USER_AGENT = (
    "Zedek-AI-Browser/1.0 "
    "(https://github.com/your-repo/zedek; research use only)"
)

# Hard cap on returned text content (characters).
_TEXT_MAX_CHARS = 6_000

# ── SSRF blocklist (same rules as mcp_online_server) ─────────────────────────

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _ssrf_check(url: str) -> str | None:
    """Return an error string if the URL is SSRF-blocked, else None."""
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

    # Exception: allow localhost-equivalent only if it resolves to 127.0.0.1
    # and the port is a safe test port (5000-9999) — for local test pages.
    # We keep this narrow and explicit rather than opening the loopback broadly.
    port = parsed.port
    if host.lower() in ("127.0.0.1",) and port and 5000 <= port <= 9999:
        return None  # explicitly allowed for local test servers

    try:
        addr_infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return f"DNS resolution failed for '{host}': {exc}"

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        raw_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            continue
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return (
                    f"URL resolves to a private/reserved address "
                    f"({raw_ip}) and cannot be navigated to."
                )

    return None


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[... truncated at {max_chars} characters]"


# ── Playwright async helpers ───────────────────────────────────────────────────

async def _with_page(url: str, action):
    """
    Launch headless Chromium, navigate to url, run action(page), return result.
    Closes browser on success, failure, and exception.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        try:
            context = await browser.new_context(user_agent=_USER_AGENT)
            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            result = await action(page)
            return result
        finally:
            await browser.close()


# ── Server setup ──────────────────────────────────────────────────────────────

_server = MCPServer(
    name="playwright_tools",
    version="1.0.0",
    instructions=(
        "Browser automation tools using Playwright/Chromium. "
        "All tools are Tier 2 (risky) — they interact with real web pages and "
        "require explicit user confirmation before execution."
    ),
)


# ── Tool: browser_navigate ────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Navigates to a public URL in a headless browser and returns the page "
        "title and a text excerpt. Private network addresses are blocked."
    ),
)
def browser_navigate(url: str) -> str:
    """Navigate to a URL and return page title and text content.

    Args:
        url: The full URL to navigate to (http/https only).
    """
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    async def _action(page):
        title = await page.title()
        body_text = await page.inner_text("body")
        return f"Title: {title}\n\n{_truncate(body_text, _TEXT_MAX_CHARS)}"

    try:
        return asyncio.run(_with_page(url, _action))
    except Exception as exc:
        return f"[error] Navigation failed: {exc}"


# ── Tool: browser_click ───────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Clicks an element on a web page identified by a CSS selector. "
        "Navigates to the URL first, then clicks the matched element."
    ),
)
def browser_click(url: str, selector: str) -> str:
    """Navigate to a URL and click an element matching the CSS selector.

    Args:
        url: The full URL of the page to load.
        selector: A CSS selector identifying the element to click.
    """
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    if not selector.strip():
        return "[error] Selector must not be empty."

    async def _action(page):
        try:
            await page.click(selector, timeout=5_000)
        except Exception as exc:
            return f"[error] Click failed on '{selector}': {exc}"
        # Return updated page state after click
        title = await page.title()
        url_after = page.url
        return f"Clicked '{selector}'. Page is now: {title} ({url_after})"

    try:
        return asyncio.run(_with_page(url, _action))
    except Exception as exc:
        return f"[error] Browser action failed: {exc}"


# ── Tool: browser_type ────────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Types text into an input element on a web page identified by a CSS "
        "selector. Navigates to the URL first, then types into the element."
    ),
)
def browser_type(url: str, selector: str, text: str) -> str:
    """Navigate to a URL and type text into an input element.

    Args:
        url: The full URL of the page to load.
        selector: A CSS selector identifying the input element.
        text: The text to type into the element.
    """
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    if not selector.strip():
        return "[error] Selector must not be empty."

    async def _action(page):
        try:
            await page.fill(selector, text, timeout=5_000)
        except Exception as exc:
            return f"[error] Type failed on '{selector}': {exc}"
        return f"Typed into '{selector}' successfully."

    try:
        return asyncio.run(_with_page(url, _action))
    except Exception as exc:
        return f"[error] Browser action failed: {exc}"


# ── Tool: browser_get_text ────────────────────────────────────────────────────

@_server.tool(
    description=(
        "Returns the visible text content of a web page or a specific element "
        "identified by a CSS selector."
    ),
)
def browser_get_text(url: str, selector: str = "body") -> str:
    """Navigate to a URL and return the text content of an element.

    Args:
        url: The full URL of the page to load.
        selector: CSS selector for the element whose text to return (default: body).
    """
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    async def _action(page):
        try:
            text = await page.inner_text(selector, timeout=5_000)
        except Exception as exc:
            return f"[error] Could not read text from '{selector}': {exc}"
        return _truncate(text, _TEXT_MAX_CHARS)

    try:
        return asyncio.run(_with_page(url, _action))
    except Exception as exc:
        return f"[error] Browser action failed: {exc}"


# ── Tool: browser_screenshot ──────────────────────────────────────────────────

@_server.tool(
    description=(
        "Captures a screenshot of a web page and returns it as a base64-encoded "
        "PNG string. Navigates to the URL first."
    ),
)
def browser_screenshot(url: str) -> str:
    """Navigate to a URL and capture a screenshot as base64 PNG.

    Args:
        url: The full URL of the page to capture.
    """
    ssrf_err = _ssrf_check(url)
    if ssrf_err:
        return f"[blocked] {ssrf_err}"

    async def _action(page):
        png_bytes = await page.screenshot(type="png", full_page=False)
        return base64.b64encode(png_bytes).decode("ascii")

    try:
        return asyncio.run(_with_page(url, _action))
    except Exception as exc:
        return f"[error] Screenshot failed: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(_server.run_stdio_async())
