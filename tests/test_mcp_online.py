"""
Tests for mcp_online_server.py.

Covers:
  - Unit tests for the SSRF blocklist (_ssrf_check).
  - Unit tests for _strip_html and _truncate helpers.
  - Tier-gate tests: all three online tools must default to Tier 1 with no
    false escalations from their descriptions or names.
  - Mocked HTTP integration tests for fetch_url, search_wikipedia, search_arxiv.
"""

from __future__ import annotations

import sys
import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest

# Ensure the project root is on sys.path so mcp_online_server imports cleanly.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import mcp_online_server as mos
import tier_gate


# ── SSRF guard unit tests ─────────────────────────────────────────────────────

class TestSSRFCheck:
    def test_public_https_url_is_allowed(self):
        result = mos._ssrf_check("https://en.wikipedia.org/wiki/Python")
        assert result is None, f"Expected None, got: {result}"

    def test_localhost_is_blocked(self):
        result = mos._ssrf_check("http://localhost/admin")
        assert result is not None
        assert "blocked" in result.lower() or "private" in result.lower()

    def test_loopback_ip_is_blocked(self):
        result = mos._ssrf_check("http://127.0.0.1/secret")
        assert result is not None

    def test_aws_metadata_ip_is_blocked(self):
        result = mos._ssrf_check("http://169.254.169.254/latest/meta-data/")
        assert result is not None

    def test_private_10_range_is_blocked(self):
        result = mos._ssrf_check("http://10.0.0.1/internal")
        assert result is not None

    def test_private_192_168_range_is_blocked(self):
        result = mos._ssrf_check("http://192.168.1.100/router-admin")
        assert result is not None

    def test_private_172_16_range_is_blocked(self):
        result = mos._ssrf_check("http://172.16.0.1/internal")
        assert result is not None

    def test_non_http_scheme_is_rejected(self):
        result = mos._ssrf_check("file:///etc/passwd")
        assert result is not None
        assert "not allowed" in result.lower() or "scheme" in result.lower()

    def test_ftp_scheme_is_rejected(self):
        result = mos._ssrf_check("ftp://ftp.example.com/file")
        assert result is not None

    def test_empty_hostname_is_rejected(self):
        result = mos._ssrf_check("http:///path")
        assert result is not None


# ── Helper unit tests ─────────────────────────────────────────────────────────

class TestStripHtml:
    def test_removes_tags(self):
        assert mos._strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_removes_script_block(self):
        html = "<html><script>alert('xss')</script><p>text</p></html>"
        result = mos._strip_html(html)
        assert "alert" not in result
        assert "text" in result

    def test_removes_style_block(self):
        html = "<style>body { color: red; }</style><p>content</p>"
        result = mos._strip_html(html)
        assert "color" not in result
        assert "content" in result

    def test_collapses_whitespace(self):
        result = mos._strip_html("<p>  hello   world  </p>")
        assert "  " not in result


class TestTruncate:
    def test_short_text_unchanged(self):
        assert mos._truncate("hello", 100) == "hello"

    def test_long_text_truncated(self):
        long = "x" * 200
        result = mos._truncate(long, 100)
        assert len(result) > 100  # includes the truncation notice
        assert "truncated" in result

    def test_exact_length_unchanged(self):
        text = "a" * 50
        assert mos._truncate(text, 50) == text


# ── Tier gate tests — no false escalations ────────────────────────────────────

class TestOnlineToolTierGate:
    """Verify online tools stay at Tier 1 and aren't falsely escalated."""

    def test_fetch_url_tier_1_default(self):
        decision = tier_gate.gate(
            "mcp_online_tools_fetch_url",
            {"url": "https://en.wikipedia.org/wiki/Python"},
        )
        assert decision["tier"] == 1
        assert decision["action"] == "notify"

    def test_search_wikipedia_tier_1_default(self):
        decision = tier_gate.gate(
            "mcp_online_tools_search_wikipedia",
            {"query": "machine learning"},
        )
        assert decision["tier"] == 1
        assert decision["action"] == "notify"

    def test_search_arxiv_tier_1_default(self):
        decision = tier_gate.gate(
            "mcp_online_tools_search_arxiv",
            {"query": "transformer architecture"},
        )
        assert decision["tier"] == 1
        assert decision["action"] == "notify"

    def test_write_in_query_does_not_escalate(self):
        """A query containing 'write' must not falsely escalate to Tier 2.

        'write' appears in FORCE_TIER_2_PATTERNS only if the text matches the
        pattern. This test confirms a read-only search query for something like
        'write a summary' does not trigger a false escalation.
        """
        # Note: 'write' is NOT currently in FORCE_TIER_2_PATTERNS — this test
        # documents the no-collision guarantee and will catch a future regression
        # if someone naively adds 'write' to the pattern list.
        decision = tier_gate.gate(
            "mcp_online_tools_search_wikipedia",
            {"query": "how to write a summary of text"},
        )
        # Should remain Tier 1 — 'write' is not in FORCE_TIER_2_PATTERNS.
        assert decision["tier"] == 1

    def test_delete_in_query_escalates_correctly(self):
        """A query containing 'delete' SHOULD escalate to Tier 2 (this is correct)."""
        decision = tier_gate.gate(
            "mcp_online_tools_search_wikipedia",
            {"query": "delete temporary files tutorial"},
        )
        assert decision["tier"] >= 2

    def test_credit_card_in_query_escalates_to_tier_3(self):
        decision = tier_gate.gate(
            "mcp_online_tools_fetch_url",
            {"url": "https://example.com"},
            user_input="fetch my credit card details",
        )
        assert decision["tier"] == 3
        assert decision["action"] == "blocked"


# ── Mocked HTTP integration tests ─────────────────────────────────────────────

class TestFetchUrl:
    def test_returns_text_from_html_response(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = "<html><body><p>Hello world</p></body></html>"

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.fetch_url("https://example.com")

        assert "Hello world" in result

    def test_returns_error_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {"content-type": "text/html"}

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.fetch_url("https://example.com/missing")

        assert "[error]" in result
        assert "404" in result

    def test_blocks_private_url(self):
        result = mos.fetch_url("http://127.0.0.1/admin")
        assert "[blocked]" in result

    def test_returns_error_on_timeout(self):
        import httpx as _httpx
        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = _httpx.TimeoutException("timeout")

            result = mos.fetch_url("https://example.com")

        assert "[error]" in result
        assert "timed out" in result.lower()

    def test_rejects_file_scheme(self):
        result = mos.fetch_url("file:///etc/passwd")
        assert "[blocked]" in result


class TestSearchWikipedia:
    def _make_search_response(self, titles, urls):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = [
            "query",
            titles,
            ["" for _ in titles],
            urls,
        ]
        return mock

    def _make_summary_response(self, extract):
        mock = MagicMock()
        mock.status_code = 200
        mock.json.return_value = {"extract": extract}
        return mock

    def test_returns_results(self):
        search_resp = self._make_search_response(
            ["Python (programming language)"],
            ["https://en.wikipedia.org/wiki/Python_(programming_language)"],
        )
        summary_resp = self._make_summary_response(
            "Python is a high-level programming language."
        )

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [search_resp, summary_resp]

            result = mos.search_wikipedia("Python programming")

        assert "Python" in result
        assert "programming language" in result.lower()

    def test_empty_query_returns_error(self):
        result = mos.search_wikipedia("   ")
        assert "[error]" in result

    def test_returns_error_on_api_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.search_wikipedia("Python")

        assert "[error]" in result

    def test_no_results_returns_friendly_message(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["query", [], [], []]

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.search_wikipedia("xyzzy_no_such_article_abc")

        assert "No Wikipedia" in result


class TestSearchArxiv:
    _ARXIV_XML = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <id>http://arxiv.org/abs/2401.00001v1</id>
            <title>Attention Is All You Need</title>
            <summary>We propose a new architecture called the Transformer.</summary>
            <published>2017-06-12T00:00:00Z</published>
            <author><name>Ashish Vaswani</name></author>
            <author><name>Noam Shazeer</name></author>
          </entry>
        </feed>
    """)

    def test_returns_paper_details(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = self._ARXIV_XML

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.search_arxiv("transformer attention")

        assert "Attention Is All You Need" in result
        assert "Ashish Vaswani" in result
        assert "Transformer" in result

    def test_empty_query_returns_error(self):
        result = mos.search_arxiv("")
        assert "[error]" in result

    def test_returns_error_on_api_failure(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.search_arxiv("transformer")

        assert "[error]" in result

    def test_empty_feed_returns_friendly_message(self):
        empty_xml = textwrap.dedent("""\
            <?xml version="1.0" encoding="UTF-8"?>
            <feed xmlns="http://www.w3.org/2005/Atom">
            </feed>
        """)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = empty_xml

        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = mock_resp

            result = mos.search_arxiv("xyzzy_no_papers")

        assert "No arXiv" in result

    def test_timeout_returns_error(self):
        import httpx as _httpx
        with patch("mcp_online_server.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = lambda s: mock_client
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = _httpx.TimeoutException("timeout")

            result = mos.search_arxiv("transformers")

        assert "[error]" in result
        assert "timed out" in result.lower()
