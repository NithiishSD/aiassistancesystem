"""Relevance-selected research passages (OpenSpec change: chunk-research-sources, ROADMAP F4).

reranker.score is stubbed except in the real-model check, which is skipped when
the cross-encoder isn't installed.
"""

import os
import time
from unittest.mock import patch

import pytest

import reranker
import research_agent
from research_agent import Source, _chunk, select_passages

FILLER = ("The site navigation lists products, pricing, careers, press, and a newsletter "
          "signup form with many links and cookie notices. ")
ANSWER = "The Transformer architecture was introduced in 2017 by Vaswani and colleagues."


def _doc(content, origin="url:https://example.com", query="q"):
    return Source(label="S?", origin=origin, content=content, query=query)


def _score_by_keyword(keyword):
    """Stub reranker: passages containing `keyword` score high, others low."""
    def score(query, texts):
        return [0.9 if keyword in t else 0.01 for t in texts]
    return score


# ── 1.1 chunking ────────────────────────────────────────────────────────────

class TestChunk:
    def test_small_paragraphs_packed(self):
        chunks = _chunk("one two three\n\nfour five\n\nsix", max_words=5)
        assert chunks == ["one two three four five", "six"]

    def test_oversize_paragraph_split_by_sentence(self):
        text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota."
        assert _chunk(text, max_words=4) == ["Alpha beta gamma.", "Delta epsilon zeta.", "Eta theta iota."]

    def test_oversize_sentence_split_by_words(self):
        chunks = _chunk(" ".join(f"w{i}" for i in range(25)), max_words=10)
        assert [len(c.split()) for c in chunks] == [10, 10, 5]

    def test_limit_and_text_preserved(self):
        text = (FILLER * 40) + "\n\n" + ANSWER + "\n\n" + (FILLER * 10)
        chunks = _chunk(text, max_words=180)
        assert all(len(c.split()) <= 180 for c in chunks)
        assert " ".join(chunks).split() == text.split()

    @pytest.mark.parametrize("empty", ["", "   \n\n  ", None])
    def test_empty(self, empty):
        assert _chunk(empty) == []


# ── 1.2 selection ───────────────────────────────────────────────────────────

class TestSelectPassages:
    def test_answer_past_4000_chars_is_selected(self):
        page = (FILLER * 60) + "\n\n" + ANSWER + "\n\n" + (FILLER * 5)
        assert page.index(ANSWER) > research_agent.MAX_SOURCE_CHARS
        with patch("reranker.score", side_effect=_score_by_keyword("Vaswani")):
            [selected] = select_passages("who introduced the transformer", [_doc(page)])
        assert ANSWER in selected.content
        assert len(selected.content) <= research_agent.PASSAGE_CHAR_BUDGET

    def test_budget_and_cap_across_six_long_documents(self):
        docs = [_doc((FILLER + f"doc{i} fact. ") * 60, origin=f"url:https://e{i}.com") for i in range(6)]
        assert all(len(d.content) >= 8000 for d in docs)
        with patch("reranker.score", side_effect=lambda q, texts: [1.0 - i / 1000 for i in range(len(texts))]):
            selected = select_passages("q", docs)
        total = sum(len(s.content) for s in selected)
        passages = sum(s.content.count("\n…\n") + 1 for s in selected)
        assert total <= research_agent.PASSAGE_CHAR_BUDGET
        assert passages <= research_agent.MAX_PASSAGES

    def test_passages_regrouped_in_page_order(self):
        page = "\n\n".join([f"Part {i}. " + ("word " * 170) for i in range(4)])
        # Rank part 3 above part 1; both from the same page.
        with patch("reranker.score", side_effect=lambda q, texts: [
                {"Part 3": 0.9, "Part 1": 0.8}.get(next((k for k in ("Part 3", "Part 1") if k in t), ""), 0.0)
                for t in texts]), \
             patch.object(research_agent, "MAX_PASSAGES", 2):
            [selected] = select_passages("q", [_doc(page)])
        assert selected.content.index("Part 1") < selected.content.index("Part 3")
        assert "\n…\n" in selected.content

    def test_irrelevant_documents_dropped_and_labels_contiguous(self):
        docs = [_doc("Cooking pasta needs salted water.", origin="wikipedia", query="pasta"),
                _doc(ANSWER, origin="wikipedia", query="transformer"),
                _doc("Football is played with eleven players.", origin="wikipedia", query="football")]
        with patch("reranker.score", side_effect=_score_by_keyword("Vaswani")), \
             patch.object(research_agent, "MAX_PASSAGES", 1):
            selected = select_passages("who introduced the transformer", docs)
        assert [(s.label, s.query) for s in selected] == [("S1", "transformer")]

    def test_memory_first_and_untouched(self):
        fact = Source(label="S9", origin="memory", content="User's college: PSG College of Technology")
        with patch("reranker.score", side_effect=_score_by_keyword("Vaswani")):
            selected = select_passages("q", [_doc(ANSWER), fact])
        assert selected[0].origin == "memory" and selected[0].content == fact.content
        assert [s.label for s in selected] == ["S1", "S2"]

    def test_memory_not_sent_to_reranker(self):
        fact = Source(label="S1", origin="memory", content="User's college: PSG")
        with patch("reranker.score") as score:
            assert select_passages("q", [fact])[0].content == "User's college: PSG"
        score.assert_not_called()

    def test_reranker_unavailable_falls_back_to_truncation(self):
        page = "x " * 5000
        with patch("reranker.score", return_value=None), \
             patch.object(research_agent.log, "info") as info:
            [selected] = select_passages("q", [_doc(page)])
        assert selected.content == research_agent._truncate(page)
        assert "research_passage_selection_degraded" in [c.args[0] for c in info.call_args_list]

    def test_gather_keeps_raw_text_bounded(self):
        agent = research_agent.ResearchAgent()
        long_page = "y " * 20000
        with patch("research_agent.memory.retrieve_relevant", return_value=[]), \
             patch.object(agent, "_call_tool", return_value=long_page):
            [doc] = agent.gather("see https://example.com/page", [])
        assert len(doc.content) == research_agent.RAW_DOC_CHAR_CAP

    def test_research_pipeline_uses_selection(self):
        agent = research_agent.ResearchAgent()
        page = (FILLER * 60) + "\n\n" + ANSWER
        captured = {}

        def synth(question, sources):
            captured["sources"] = sources
            return "answer [S1]"

        with patch.object(agent, "plan_queries", return_value=["transformer"]), \
             patch.object(agent, "gather", return_value=[_doc(page)]), \
             patch.object(agent, "synthesize", side_effect=synth), \
             patch("reranker.score", side_effect=_score_by_keyword("Vaswani")):
            report = agent.research("who introduced the transformer")
        assert ANSWER in captured["sources"][0].content
        assert report.sources == captured["sources"]


# ── 1.3 real model ──────────────────────────────────────────────────────────

@pytest.mark.skipif(not os.path.isfile(os.path.join(reranker.MODEL_DIR, "config.json")),
                    reason="reranker model not installed")
def test_real_cross_encoder_finds_deep_answer_quickly():
    page = (FILLER * 60) + "\n\n" + ANSWER + "\n\n" + (FILLER * 5)
    others = [_doc((FILLER * 60)[:8000], origin=f"url:https://e{i}.com") for i in range(5)]
    reranker.score("warm up", ["a"])
    started = time.perf_counter()
    selected = select_passages("who introduced the transformer architecture and when",
                               [_doc(page, query="transformer paper")] + others)
    elapsed = time.perf_counter() - started
    print(f"\nselect_passages over 6 docs (~48k chars): {elapsed * 1000:.0f} ms")
    assert any(ANSWER in s.content for s in selected)
    assert elapsed < 5.0
