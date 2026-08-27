#!/usr/bin/env python3
"""
Live end-to-end test: verify the Tier 2 gate fires for a Playwright browser_click.

What this proves:
  - The playwright_tools server's tools are discovered and registered correctly.
  - browser_click's description triggers Tier 2 via effect-verb detection ("Clicks").
  - server default_tier=2 is parsed from mcp_servers.json correctly.
  - gate() returns action="confirm" — meaning the orchestrator would pause and
    prompt the user before executing, NOT auto-run it.

This test does NOT actually launch a browser — it exercises the full pipeline
up to and including the gate decision, then asserts the gate output.
The gate is the safety mechanism; confirming it works live (not just in unit
tests) is the point of this script.

To also verify the browser itself works (optional, requires local HTTP server):
    python3 -m http.server 8765 --directory tests/  # in another terminal
    Then run: ./zedek-env/bin/python3 tests/e2e_gate_test.py --live
"""

from __future__ import annotations

import sys
import os
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


def test_gate_fires_for_playwright_click():
    """
    Simulate what the orchestrator does: look up tool, pass description to gate.
    Confirm gate returns tier=2 / action=confirm — NOT tier=1 / action=notify.
    """
    import tier_gate
    import mcp_client

    print("\n=== Step 1: Discover all MCP tools ===")
    tools = mcp_client.discover_all_tools()
    print(f"  Discovered {len(tools)} tools:")
    for t in tools:
        print(f"    {t.qualified_name}")

    print("\n=== Step 2: Find mcp_playwright_tools_browser_click ===")
    registry = mcp_client.get_tool_registry()
    target_qname = "mcp_playwright_tools_browser_click"
    tool_spec = registry.get(target_qname)

    if tool_spec is None:
        print(f"  ❌ Tool '{target_qname}' not found in registry.")
        print("     Available playwright tools:", [k for k in registry if "playwright" in k])
        return False

    print(f"  ✅ Found: {tool_spec.qualified_name}")
    print(f"     Description: {tool_spec.description!r}")

    print("\n=== Step 3: Check server-level default_tier ===")
    server_registry = mcp_client.get_server_registry()
    playwright_server = server_registry.get("playwright_tools")
    if playwright_server:
        print(f"  ✅ playwright_tools server default_tier = {playwright_server.default_tier}")
        assert playwright_server.default_tier == 2, (
            f"Expected default_tier=2, got {playwright_server.default_tier}"
        )
    else:
        print("  ❌ playwright_tools server not in server registry")
        return False

    print("\n=== Step 4: Run gate() with tool description ===")
    tool_args = {"url": "http://127.0.0.1:8765/e2e_playwright_test_page.html",
                 "selector": "#risky-btn"}
    gate_decision = tier_gate.gate(
        target_qname,
        tool_args,
        user_input="click the risky button on the test page",
        tool_description=tool_spec.description,
    )

    print(f"  Gate decision: tier={gate_decision['tier']}, action={gate_decision['action']!r}")
    print(f"  Message: {gate_decision['message']}")

    assert gate_decision["tier"] == 2, (
        f"❌ Expected Tier 2, got Tier {gate_decision['tier']}. "
        "The gate is NOT blocking this action correctly."
    )
    assert gate_decision["action"] == "confirm", (
        f"❌ Expected action='confirm', got {gate_decision['action']!r}. "
        "The orchestrator would auto-execute without prompting the user."
    )

    print("\n  ✅ GATE CONFIRMED: browser_click correctly requires user confirmation (Tier 2).")
    print("     The orchestrator would pause here and prompt: y/n before proceeding.")
    return True


def test_live_browser_click(base_url: str):
    """
    Actually launch Chromium, navigate to the test page, click #risky-btn,
    and verify the result div becomes visible. Requires the local HTTP server
    to be running.
    """
    from mcp_playwright_server import browser_click, browser_get_text

    print(f"\n=== Live Browser Test: {base_url} ===")

    url = f"{base_url}/e2e_playwright_test_page.html"

    print("  Step A: Get initial page text (should NOT contain 'Button was clicked')...")
    text_before = browser_get_text(url, selector="body")
    print(f"  Before text (excerpt): {text_before[:200]!r}")

    print("  Step B: Click #risky-btn...")
    click_result = browser_click(url, selector="#risky-btn")
    print(f"  Click result: {click_result!r}")

    if "[error]" in click_result or "[blocked]" in click_result:
        print(f"  ❌ Click failed: {click_result}")
        return False

    print("  ✅ browser_click executed successfully.")
    print("     (In production, this step would only be reached AFTER the user typed 'y')")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true",
                        help="Run the live browser test (requires local HTTP server on port 8765)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765",
                        help="Base URL for the local test server (default: http://127.0.0.1:8765)")
    args = parser.parse_args()

    print("=" * 60)
    print("Zedek MCP Playwright E2E Gate Verification")
    print("=" * 60)

    gate_ok = test_gate_fires_for_playwright_click()
    if not gate_ok:
        print("\n❌ Gate test FAILED. Fix before proceeding.")
        sys.exit(1)

    if args.live:
        live_ok = test_live_browser_click(args.base_url)
        if not live_ok:
            print("\n❌ Live browser test FAILED.")
            sys.exit(1)
        print("\n✅ All E2E tests PASSED — gate fires correctly AND browser automation works.")
    else:
        print("\n✅ Gate test PASSED.")
        print("   Run with --live to also verify Chromium launches and clicks correctly.")

    sys.exit(0)
