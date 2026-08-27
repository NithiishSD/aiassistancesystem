"""
Tests for tier_gate verb-escalation, description inspection, and
server-level default_tier precedence.

Critical cases (per agreed plan):
  A. Safe tool with collision-prone word in description stays Tier 1.
  B. Tool with genuine effect verb in description escalates to Tier 2.
  C. Server declaring default_tier=2 cannot be pulled back to Tier 1.

Plus supporting coverage:
  - _extract_effect_description() strips Args: block correctly.
  - Each MCP_ACTION_VERB_PATTERN individually escalates on a synthetic description.
  - Substring false-positives (dispatch/patch, compress/press, committee/commit,
    progress/press) do NOT escalate — word-boundary matching confirmed.
  - Existing FORCE_TIER_2/3 arg+user-input patterns still work (regression).
  - gate() called without tool_description — backward-compatible, no crash.
  - Precedence is monotonically non-decreasing across all lanes.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import patch

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import tier_gate


# ── _extract_effect_description ───────────────────────────────────────────────

class TestExtractEffectDescription:
    def test_strips_args_section(self):
        desc = "Fetches a page from the web.\n\nArgs:\n    url: The URL to fetch."
        result = tier_gate._extract_effect_description(desc)
        assert "url" not in result
        assert "fetches" in result

    def test_strips_arguments_section(self):
        desc = "Submits a form.\n\nArguments:\n    selector: CSS selector."
        result = tier_gate._extract_effect_description(desc)
        assert "selector" not in result
        assert "submits" in result

    def test_strips_parameters_section(self):
        desc = "Clicks a button.\n\nParameters:\n    element: The element ID."
        result = tier_gate._extract_effect_description(desc)
        assert "element" not in result
        assert "clicks" in result

    def test_no_args_section_returns_full_text(self):
        desc = "Returns the current page title."
        result = tier_gate._extract_effect_description(desc)
        assert "returns the current page title" in result

    def test_returns_lowercase(self):
        desc = "Sends an Email to the user."
        result = tier_gate._extract_effect_description(desc)
        assert result == result.lower()

    def test_empty_description(self):
        result = tier_gate._extract_effect_description("")
        assert result == ""


# ── Test A: collision-avoidance — safe tools stay Tier 1 ─────────────────────

class TestCollisionAvoidance:
    """
    Proves the design decision works: bare 'write', 'commit', etc. in a
    safe description context do NOT escalate because the pattern list uses
    'write to' (not 'write') and word-boundary matching blocks substrings.
    """

    def test_bare_write_in_description_does_not_escalate(self):
        """'write a summary' must NOT trip 'write to' pattern."""
        decision = tier_gate.gate(
            "mcp_zedek_tools_summarize_text", {},
            tool_description="Returns a brief summary: write a short summary of the provided text.",
        )
        assert decision["tier"] == 1, (
            "Bare 'write' in description falsely escalated to Tier 2. "
            "Pattern should be 'write to', not 'write'."
        )

    def test_dispatch_does_not_match_patch(self):
        """'dispatch' contains 'patch' as a substring — must NOT escalate."""
        decision = tier_gate.gate(
            "mcp_queue_dispatcher", {},
            tool_description="Dispatches a background job to the processing queue.",
        )
        assert decision["tier"] == 1, "False positive: 'dispatch' matched 'patch'."

    def test_compress_does_not_match_press(self):
        """'compress' contains 'press' as a substring — must NOT escalate."""
        decision = tier_gate.gate(
            "mcp_storage_compress", {},
            tool_description="Compresses the output for storage efficiency.",
        )
        assert decision["tier"] == 1, "False positive: 'compress' matched 'press'."

    def test_committee_does_not_match_commit(self):
        """'committee' contains 'commit' as a substring — must NOT escalate."""
        decision = tier_gate.gate(
            "mcp_academic_committee_list", {},
            tool_description="Lists committee members for a given conference.",
        )
        assert decision["tier"] == 1, "False positive: 'committee' matched 'commit'."

    def test_progress_does_not_match_press(self):
        """'progress' contains 'press' as a substring — must NOT escalate."""
        decision = tier_gate.gate(
            "mcp_task_progress", {},
            tool_description="Shows progress of the current indexing task.",
        )
        assert decision["tier"] == 1, "False positive: 'progress' matched 'press'."

    def test_type_annotation_does_not_match_type_into(self):
        """'type annotation' must not match 'type into' pattern."""
        decision = tier_gate.gate(
            "mcp_code_analysis", {},
            tool_description="Infers the type annotation of a Python expression.",
        )
        assert decision["tier"] == 1

    def test_write_report_does_not_escalate(self):
        """'write a report' — 'write to' pattern must not match."""
        decision = tier_gate.gate(
            "mcp_reporter", {},
            tool_description="Generates and returns a write a report of test results.",
        )
        assert decision["tier"] == 1


# ── Test B: genuine effect verbs escalate to Tier 2 ──────────────────────────

class TestEffectVerbEscalation:
    """Proves verb detection works for genuine world-mutating effect descriptions."""

    def test_click_escalates(self):
        decision = tier_gate.gate(
            "mcp_puppeteer_click_element", {"selector": "#submit-btn"},
            tool_description="Clicks an element on the page identified by a CSS selector.",
        )
        assert decision["tier"] == 2
        assert decision["action"] == "confirm"

    def test_submit_escalates(self):
        decision = tier_gate.gate(
            "mcp_browser_submit_form", {},
            tool_description="Submits a form on the current page.",
        )
        assert decision["tier"] == 2

    def test_send_escalates(self):
        decision = tier_gate.gate(
            "mcp_email_sender", {"to": "user@example.com"},
            tool_description="Sends an email to the specified recipient.",
        )
        assert decision["tier"] == 2

    def test_purchase_escalates(self):
        decision = tier_gate.gate(
            "mcp_shop_purchase", {},
            tool_description="Purchase an item from the shop using stored payment info.",
        )
        assert decision["tier"] == 2

    def test_commit_in_effect_escalates(self):
        """'commit' as a standalone word in an effect description escalates."""
        decision = tier_gate.gate(
            "mcp_git_commit", {},
            tool_description="Commit staged changes to the repository with a message.",
        )
        assert decision["tier"] == 2

    def test_push_escalates(self):
        decision = tier_gate.gate(
            "mcp_git_push", {},
            tool_description="Push local commits to the remote repository.",
        )
        assert decision["tier"] == 2

    def test_patch_in_effect_escalates(self):
        """'patch' as a standalone word (not inside 'dispatch') escalates."""
        decision = tier_gate.gate(
            "mcp_api_patch", {},
            tool_description="Patch a resource via HTTP PATCH request.",
        )
        assert decision["tier"] == 2

    def test_press_in_effect_escalates(self):
        """'press' as standalone word escalates."""
        decision = tier_gate.gate(
            "mcp_keyboard_press", {},
            tool_description="Press a key combination on the keyboard.",
        )
        assert decision["tier"] == 2

    def test_write_to_in_effect_escalates(self):
        """'write to' (full phrase) in effect description escalates."""
        decision = tier_gate.gate(
            "mcp_filesystem_write", {},
            tool_description="Write to a file at the specified path.",
        )
        assert decision["tier"] == 2

    def test_type_into_in_effect_escalates(self):
        """'type into' (full phrase) in effect description escalates."""
        decision = tier_gate.gate(
            "mcp_browser_type", {"selector": "#input", "text": "hello"},
            tool_description="Type into an input element identified by a selector.",
        )
        assert decision["tier"] == 2

    def test_confirm_in_effect_escalates(self):
        decision = tier_gate.gate(
            "mcp_dialog_confirm", {},
            tool_description="Confirm a dialog box on the page.",
        )
        assert decision["tier"] == 2

    def test_effect_verb_stripped_if_in_args_section(self):
        """
        A verb in the Args: block must NOT escalate — it's describing a parameter,
        not the tool's effect on the world.
        """
        decision = tier_gate.gate(
            "mcp_text_processor", {},
            tool_description=(
                "Processes and returns a cleaned text string.\n\n"
                "Args:\n"
                "    text: The text to submit for cleaning.\n"
                "    mode: Whether to click through all options."
            ),
        )
        # 'submit' and 'click' are in the Args section — must not escalate.
        assert decision["tier"] == 1


# ── Test C: server default_tier is a monotonic ceiling-raiser ─────────────────

class TestServerDefaultTierPrecedence:
    """
    Proves that a server declaring default_tier=2 cannot be pulled back to
    Tier 1 by an individually low-risk-looking tool.
    """

    def test_server_default_tier_2_overrides_tool_tier_1(self):
        """A benign-looking tool from a Tier-2 server must still be Tier 2."""
        with patch("tier_gate._server_default_tier", return_value=2):
            decision = tier_gate.gate(
                "mcp_playwright_get_title", {},
                tool_description="Returns the title of the current page.",
            )
        assert decision["tier"] == 2, (
            "Server default_tier=2 was overridden by a low-risk tool — "
            "precedence must be monotonically non-decreasing."
        )
        assert decision["action"] == "confirm"

    def test_server_default_tier_2_combined_with_verb_stays_tier_2(self):
        """Tier 2 from server default + Tier 2 from verb = Tier 2 (not doubled)."""
        with patch("tier_gate._server_default_tier", return_value=2):
            decision = tier_gate.gate(
                "mcp_playwright_click", {"selector": "#ok"},
                tool_description="Clicks an element on the page.",
            )
        assert decision["tier"] == 2

    def test_server_default_tier_2_cannot_override_tier_3_pattern(self):
        """Tier 2 server default must not lower a Tier 3 pattern match."""
        with patch("tier_gate._server_default_tier", return_value=2):
            decision = tier_gate.gate(
                "mcp_playwright_fill", {"field": "credit card number"},
                tool_description="Fills a form field with the given value.",
            )
        assert decision["tier"] == 3

    def test_server_default_tier_1_does_not_lower_verb_escalation(self):
        """Server default_tier=1 must not pull down a Tier 2 verb escalation."""
        with patch("tier_gate._server_default_tier", return_value=1):
            decision = tier_gate.gate(
                "mcp_browser_click", {},
                tool_description="Clicks an element on the current page.",
            )
        assert decision["tier"] == 2

    def test_no_server_registry_fails_safe_to_tier_1(self):
        """If mcp_client import fails or registry is empty, default is Tier 1."""
        with patch("tier_gate._server_default_tier", return_value=1):
            decision = tier_gate.gate("mcp_unknown_tool_action", {},
                                      tool_description="Does something harmless.")
        assert decision["tier"] == 1


# ── Backward-compatibility: gate() without tool_description ───────────────────

class TestBackwardCompatibility:
    def test_gate_without_tool_description_still_works(self):
        """Existing callers that don't pass tool_description must not break."""
        decision = tier_gate.gate("mcp_zedek_tools_current_time", {})
        assert decision["tier"] == 1
        assert decision["action"] == "notify"

    def test_classify_without_tool_description_still_works(self):
        tier = tier_gate.classify("mcp_zedek_tools_word_count", {"text": "hello"})
        assert tier == 1

    def test_existing_force_tier_2_args_pattern_still_works(self):
        """FORCE_TIER_2_PATTERNS in args must still escalate (regression guard)."""
        decision = tier_gate.gate(
            "mcp_custom_tool",
            {"query": "delete temporary files"},
            user_input="delete files",
        )
        assert decision["tier"] == 2
        assert decision["action"] == "confirm"

    def test_existing_force_tier_3_args_pattern_still_works(self):
        """FORCE_TIER_3_PATTERNS in args must still block (regression guard)."""
        decision = tier_gate.gate(
            "mcp_custom_tool",
            {"account": "my credit card number is 1234"},
            user_input="check credit card",
        )
        assert decision["tier"] == 3
        assert decision["action"] == "blocked"

    def test_non_mcp_function_still_uses_function_tiers(self):
        decision = tier_gate.gate("search_files", {"path": "/home"})
        assert decision["tier"] == 0
        assert decision["action"] == "auto"

    def test_unknown_function_still_fails_safe_to_tier_3(self):
        decision = tier_gate.gate("totally_unknown_func", {})
        assert decision["tier"] == 3
        assert decision["action"] == "blocked"
