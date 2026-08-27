"""
Unit & integration tests for newly added MCP tool servers:
- github_tools (mcp_github_server)
- weather_news_tools (mcp_weather_news_server)
- codeforces_tools (mcp_codeforces_server)
- semantic_scholar_tools (mcp_semantic_scholar_server)
- playwright_tools (mcp_playwright_server)
"""

import pytest
from unittest.mock import patch
import mcp_github_server
import mcp_weather_news_server
import mcp_codeforces_server
import mcp_semantic_scholar_server
import mcp_playwright_server

# ── GitHub Server Tests ───────────────────────────────────────────────────────

def test_github_search_repos_mock():
    mock_resp = {
        "items": [
            {
                "full_name": "psf/requests",
                "stargazers_count": 50000,
                "language": "Python",
                "description": "Python HTTP for Humans.",
                "html_url": "https://github.com/psf/requests"
            }
        ]
    }
    with patch("mcp_github_server._get", return_value=mock_resp):
        res = mcp_github_server.github_search_repos("requests", max_results=5)
        assert "psf/requests" in res
        assert "50,000" in res

def test_github_get_repo_validation():
    assert "[error]" in mcp_github_server.github_get_repo("", "")
    assert "[error]" in mcp_github_server.github_search_repos("")

def test_github_get_readme_validation():
    assert "[error]" in mcp_github_server.github_get_readme("", "")

# ── Weather & News Server Tests ───────────────────────────────────────────────

def test_weather_empty_location():
    assert "[error]" in mcp_weather_news_server.get_weather("")
    assert "[error]" in mcp_weather_news_server.get_weather_forecast("")

def test_news_missing_key(monkeypatch):
    monkeypatch.setattr(mcp_weather_news_server, "_NEWS_API_KEY", "")
    res = mcp_weather_news_server.search_news("AI")
    assert "[error] NEWS_API_KEY" in res

# ── Codeforces Server Tests ───────────────────────────────────────────────────

def test_codeforces_user_info_validation():
    assert "[error]" in mcp_codeforces_server.cf_user_info("")
    assert "[error]" in mcp_codeforces_server.cf_user_submissions("")
    assert "[error]" in mcp_codeforces_server.cf_problem_search("")
    assert "[error]" in mcp_codeforces_server.cf_problem_by_id(1900, "")

def test_codeforces_user_info_mock():
    mock_data = [
        {
            "handle": "tourist",
            "rank": "legendary grandmaster",
            "rating": 3500,
            "maxRating": 4000,
            "maxRank": "tourist",
            "friendOfCount": 100,
            "contribution": 50,
            "organization": "ITMO",
            "country": "Belarus"
        }
    ]
    with patch("mcp_codeforces_server._cf_get", return_value=mock_data):
        res = mcp_codeforces_server.cf_user_info("tourist")
        assert "tourist" in res
        assert "3500" in res

# ── Semantic Scholar Server Tests ─────────────────────────────────────────────

def test_semantic_scholar_validation():
    assert "[error]" in mcp_semantic_scholar_server.scholar_search_papers("")
    assert "[error]" in mcp_semantic_scholar_server.scholar_paper_details("")
    assert "[error]" in mcp_semantic_scholar_server.scholar_paper_citations("")
    assert "[error]" in mcp_semantic_scholar_server.scholar_author_papers("")

def test_semantic_scholar_search_mock():
    mock_data = {
        "data": [
            {
                "title": "Attention Is All You Need",
                "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
                "year": 2017,
                "citationCount": 100000,
                "abstract": "The dominant sequence transduction models...",
                "url": "https://arxiv.org/abs/1706.03762"
            }
        ]
    }
    with patch("mcp_semantic_scholar_server._s2_get", return_value=mock_data):
        res = mcp_semantic_scholar_server.scholar_search_papers("attention")
        assert "Attention Is All You Need" in res
        assert "100,000" in res

# ── Playwright Server SSRF & Validation Tests ─────────────────────────────────

def test_playwright_ssrf():
    assert "[blocked]" in mcp_playwright_server.browser_navigate("http://169.254.169.254/latest/meta-data")
    assert "[blocked]" in mcp_playwright_server.browser_click("http://localhost:3000", selector="#btn")
    assert "[blocked]" in mcp_playwright_server.browser_type("ftp://example.com", selector="#input", text="hi")
    assert "[error]" in mcp_playwright_server.browser_click("http://example.com", selector="")
