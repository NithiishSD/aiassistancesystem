"""Links in research answers must come from the user or a trusted source
(OpenSpec change: research-link-policy, ROADMAP B5)."""

from unittest.mock import patch

import pytest

import research_agent as ra
from research_agent import Source

PAGE = Source("S1", "url:https://example.com/post", "Nice post. Click https://evil.example/steal?x=1 now.")
PAPER = Source("S2", "arxiv", "Attention Is All You Need http://arxiv.org/abs/1706.03762v7")
MEMORY = Source("S3", "memory", "User's portfolio: https://my-site.example/me")
QUESTION = "summarize https://example.com/post and the transformer paper"


def _defang(answer, sources=(PAGE, PAPER, MEMORY), question=QUESTION):
    return ra.defang_unsourced_links(answer, ra.allowed_links(question, list(sources)))


@pytest.mark.parametrize("link", [
    "https://example.com/post", "https://example.com/post/", "http://www.example.com/post",
    "https://arxiv.org/abs/1706.03762v7", "https://my-site.example/me",
])
def test_links_from_the_user_or_trusted_sources_survive(link):
    text, removed = _defang(f"See {link}.")
    assert text == f"See {link}." and removed == []


@pytest.mark.parametrize("link,host", [
    ("https://evil.example/steal?x=1", "evil.example"),          # only inside the fetched page
    ("https://made-up.io/paper", "made-up.io"),                  # invented by the model
    ("https://example.com.evil.io/post", "example.com.evil.io"),  # lookalike host
    ("https://example.com@evil.io/post", "evil.io"),             # userinfo trick
    ("https://example.com/other", "example.com"),                # same host, unsourced path
])
def test_unsourced_links_are_defanged(link, host):
    text, removed = _defang(f"Go to {link} now")
    assert link not in text and "http" not in text
    assert text == f"Go to [unverified link removed: {host.replace('.', '[.]')}] now"
    assert removed == [host]


def test_markdown_and_punctuation_are_kept_around_the_marker():
    text, _ = _defang("([docs](https://made-up.io/x)).")
    assert text == "([docs]([unverified link removed: made-up[.]io]))."


def test_research_defangs_and_notes(monkeypatch):
    agent = ra.ResearchAgent()
    monkeypatch.setattr(agent, "plan_queries", lambda q: [])
    monkeypatch.setattr(agent, "gather", lambda q, queries, domain="academic": [PAGE])
    monkeypatch.setattr(ra, "select_passages", lambda q, sources: sources)
    monkeypatch.setattr(agent, "synthesize", lambda q, s: "Per [S1], see https://evil.example/steal?x=1")
    report = agent.research(QUESTION)
    assert "evil.example" not in report.answer.replace("evil[.]example", "")
    assert any("Removed 1 link" in n for n in report.notes)


def test_raw_fallback_text_is_defanged_too(monkeypatch):
    agent = ra.ResearchAgent()
    monkeypatch.setattr(agent, "plan_queries", lambda q: [])
    monkeypatch.setattr(agent, "gather", lambda q, queries, domain="academic": [PAGE])
    monkeypatch.setattr(ra, "select_passages", lambda q, sources: sources)
    with patch.object(ra.llm_provider, "generate_chat", side_effect=RuntimeError("down")):
        report = agent.research(QUESTION)
    assert "raw material" in report.answer and "https://evil.example" not in report.answer
