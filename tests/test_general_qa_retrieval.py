"""General Q&A must only see relevance-selected facts (OpenSpec change: add-memory-reranker)."""

from unittest.mock import patch

import orchestrator


def _capture_prompt(monkeypatch):
    captured = {}

    def fake_generate_chat(messages, **kwargs):
        captured["system"] = messages[0]["content"]
        return {"answer": "ok", "source": "stub"}

    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake_generate_chat)
    return captured


def test_uses_relevance_ranked_retrieval_not_raw(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    with patch("orchestrator.memory.retrieve_relevant", return_value=[]) as mock_rel, \
         patch("orchestrator.memory.retrieve") as mock_raw:
        orchestrator.answer_general_question("what is a binary search tree", "personal")
    mock_rel.assert_called_once()
    mock_raw.assert_not_called()
    assert "(no relevant long-term facts found)" in captured["system"]


def test_selected_facts_appear_in_context(monkeypatch):
    captured = _capture_prompt(monkeypatch)
    facts = [{"text": "User's college: PSG College of Technology", "score": 0.02}]
    with patch("orchestrator.memory.retrieve_relevant", return_value=facts):
        orchestrator.answer_general_question("what college do I study at", "personal")
    assert "User's college: PSG College of Technology" in captured["system"]
    assert "(no relevant long-term facts found)" not in captured["system"]
