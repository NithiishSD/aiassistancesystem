"""
Unit & integration tests for the research / RAG agent (Roadmap Item 10).

Covers:
- Question shape detection (academic vs. general) and URL extraction.
- Tool allowlisting and tier-gate compliance: a blocked or confirmation-gated
  tool must never be executed.
- Graceful degradation: unavailable tools, in-band [error] markers, provider
  failures, and the zero-source anti-hallucination path.
- Full research() pipeline producing a structured ResearchReport.
"""

import json
from unittest.mock import patch

import pytest

import research_agent
from research_agent import ResearchAgent, ResearchReport, Source


class _FakeToolSpec:
    def __init__(self, description="a read-only research tool"):
        self.description = description


def _registry(*names):
    return {name: _FakeToolSpec() for name in names}


class TestQuestionShape:
    def test_academic_question_detected(self):
        assert research_agent.is_academic_question("what do recent papers say about transformers") is True

    def test_general_question_not_academic(self):
        assert research_agent.is_academic_question("what is the capital of France") is False

    def test_extract_urls(self):
        urls = research_agent.extract_urls("summarize https://example.com/a and https://b.org/x")
        assert urls == ["https://example.com/a", "https://b.org/x"]

    def test_extract_urls_empty(self):
        assert research_agent.extract_urls("no links here") == []


class TestToolCalling:
    def setup_method(self):
        self.agent = ResearchAgent()

    def test_non_allowlisted_tool_is_never_called(self):
        with patch("research_agent.mcp_client.call_mcp_tool") as mock_call:
            result = self.agent._call_tool("mcp_playwright_tools_browser_click", {}, "q")
        assert result is None
        mock_call.assert_not_called()

    def test_unregistered_tool_returns_none(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value={}), \
             patch("research_agent.mcp_client.call_mcp_tool") as mock_call:
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "x"}, "q")
        assert result is None
        mock_call.assert_not_called()

    def test_blocked_tool_is_not_executed(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "blocked", "message": "no"}), \
             patch("research_agent.mcp_client.call_mcp_tool") as mock_call:
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "x"}, "q")
        assert result is None
        mock_call.assert_not_called()

    def test_confirmation_gated_tool_is_not_auto_approved(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "confirm", "message": "ok?"}), \
             patch("research_agent.mcp_client.call_mcp_tool") as mock_call:
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "x"}, "q")
        assert result is None
        mock_call.assert_not_called()

    def test_allowed_tool_returns_output(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": "Paris is the capital.", "error": None}):
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "france"}, "q")
        assert result == "Paris is the capital."

    def test_in_band_error_marker_treated_as_no_result(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": "[error] network down", "error": None}):
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "x"}, "q")
        assert result is None

    def test_tool_error_returns_none(self):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": None, "error": "timeout"}):
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "x"}, "q")
        assert result is None


class TestPlanQueries:
    def setup_method(self):
        self.agent = ResearchAgent()

    def test_parses_llm_queries(self):
        mock = {"answer": json.dumps({"queries": ["transformer attention", "self attention nlp"]}), "source": "gemini"}
        with patch("research_agent.llm_provider.generate_chat", return_value=mock):
            queries = self.agent.plan_queries("how does attention work in transformers")
        assert queries == ["transformer attention", "self attention nlp"]

    def test_falls_back_to_raw_question_on_failure(self):
        with patch("research_agent.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            queries = self.agent.plan_queries("how does attention work")
        assert queries == ["how does attention work"]

    def test_caps_at_three_queries(self):
        mock = {"answer": json.dumps({"queries": ["a", "b", "c", "d", "e"]}), "source": "groq"}
        with patch("research_agent.llm_provider.generate_chat", return_value=mock):
            queries = self.agent.plan_queries("something")
        assert len(queries) == 3


class TestSynthesize:
    def setup_method(self):
        self.agent = ResearchAgent()

    def test_no_sources_refuses_to_answer(self):
        answer = self.agent.synthesize("what is X", [])
        assert "grounded" in answer.lower() or "couldn't retrieve" in answer.lower()

    def test_no_sources_never_calls_llm(self):
        with patch("research_agent.llm_provider.generate_chat") as mock_llm:
            self.agent.synthesize("what is X", [])
        mock_llm.assert_not_called()

    def test_synthesizes_from_sources(self):
        sources = [Source(label="S1", origin="wikipedia", content="Paris is the capital of France.")]
        mock = {"answer": "The capital of France is Paris [S1].", "source": "gemini"}
        with patch("research_agent.llm_provider.generate_chat", return_value=mock):
            answer = self.agent.synthesize("capital of france", sources)
        assert "[S1]" in answer

    def test_provider_failure_returns_raw_evidence(self):
        sources = [Source(label="S1", origin="wikipedia", content="Paris is the capital.")]
        with patch("research_agent.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            answer = self.agent.synthesize("capital of france", sources)
        assert "Paris is the capital." in answer


class TestGatherAndPipeline:
    def setup_method(self):
        self.agent = ResearchAgent()

    def test_gather_includes_memory_facts(self):
        with patch("research_agent.memory.retrieve_relevant", return_value=[{"text": "User's college: PSG"}]), \
             patch.object(ResearchAgent, "_call_tool", return_value=None):
            sources = self.agent.gather("where do I study", ["college"], domain="academic")
        assert len(sources) == 1
        assert sources[0].origin == "memory"

    def test_gather_survives_memory_failure(self):
        with patch("research_agent.memory.retrieve_relevant", side_effect=RuntimeError("chroma down")), \
             patch.object(ResearchAgent, "_call_tool", return_value="wiki text"):
            sources = self.agent.gather("what is python", ["python"], domain="personal")
        assert all(s.origin != "memory" for s in sources)

    def test_gather_respects_max_sources(self):
        agent = ResearchAgent(max_sources=2)
        with patch("research_agent.memory.retrieve_relevant", return_value=[{"text": f"fact {i}"} for i in range(5)]), \
             patch.object(ResearchAgent, "_call_tool", return_value="more text"):
            sources = agent.gather("q", ["a", "b"], domain="personal")
        assert len(sources) == 2

    def test_research_returns_structured_report(self):
        mock_plan = {"answer": json.dumps({"queries": ["python language"]}), "source": "groq"}
        mock_synth = {"answer": "Python is a language [S1].", "source": "gemini"}
        with patch("research_agent.memory.retrieve_relevant", return_value=[]), \
             patch.object(ResearchAgent, "_call_tool", return_value="Python is a programming language."), \
             patch("research_agent.llm_provider.generate_chat", side_effect=[mock_plan, mock_synth]):
            report = self.agent.research("what is python")

        assert isinstance(report, ResearchReport)
        assert report.grounded is True
        assert len(report.sources) >= 1
        assert "[S1]" in report.answer

    def test_research_with_no_sources_is_marked_ungrounded(self):
        mock_plan = {"answer": json.dumps({"queries": ["x"]}), "source": "groq"}
        with patch("research_agent.memory.retrieve_relevant", return_value=[]), \
             patch.object(ResearchAgent, "_call_tool", return_value=None), \
             patch("research_agent.llm_provider.generate_chat", return_value=mock_plan):
            report = self.agent.research("some obscure question")

        assert report.grounded is False
        assert report.notes

    def test_empty_question_short_circuits(self):
        report = self.agent.research("   ")
        assert report.grounded is False
        assert "question" in report.answer.lower()

    def test_format_report_lists_sources(self):
        report = ResearchReport(
            question="q", answer="an answer [S1]",
            sources=[Source(label="S1", origin="wikipedia", content="c", query="query")],
        )
        rendered = research_agent.format_research_report(report)
        assert "[S1] wikipedia" in rendered
        assert "an answer [S1]" in rendered


class TestOrchestratorIntegration:
    def test_research_task_dispatches_to_agent(self):
        import orchestrator

        report = ResearchReport(
            question="q", answer="grounded answer [S1]",
            sources=[Source(label="S1", origin="wikipedia", content="c")],
        )
        with patch.object(orchestrator.RESEARCH_AGENT, "research", return_value=report) as mock_research:
            out = orchestrator.execute({
                "function": "research_task",
                "domain": "academic",
                "confidence": "high",
                "_original_input": "research transformers for me",
            })

        mock_research.assert_called_once()
        assert "grounded answer [S1]" in out

    def test_ungrounded_research_does_not_reinforce_router(self):
        import orchestrator

        report = ResearchReport(question="q", answer="no sources", sources=[], grounded=False)
        with patch.object(orchestrator.RESEARCH_AGENT, "research", return_value=report), \
             patch("orchestrator.classifier.add_utterance_dynamically") as mock_add:
            orchestrator.execute({
                "function": "research_task",
                "domain": "academic",
                "confidence": "high",
                "via_llm": True,
                "_original_input": "research something",
            })

        mock_add.assert_not_called()


class TestEmptyResultFiltering:
    """A tool saying "nothing found" is a negative result, not evidence."""

    def setup_method(self):
        self.agent = ResearchAgent()

    @pytest.mark.parametrize("empty_response", [
        "No Wikipedia articles found for: 'zzz'.",
        "No arXiv papers found for: 'zzz'.",
        "No papers found for: 'zzz'.",
        "No author found matching: 'zzz'.",
        "Could not resolve author ID for 'zzz'.",
        "No citing papers found for paper ID 'x'.",
    ])
    def test_empty_results_are_not_treated_as_sources(self, empty_response):
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": empty_response, "error": None}):
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "zzz"}, "q")
        assert result is None

    def test_real_content_starting_with_no_is_kept(self):
        """Don't over-match: real prose can legitimately begin with "No"."""
        content = "No-SQL databases are non-relational stores used for horizontal scaling."
        with patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(research_agent.WIKIPEDIA_TOOL)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": content, "error": None}):
            result = self.agent._call_tool(research_agent.WIKIPEDIA_TOOL, {"query": "nosql"}, "q")
        assert result == content

    def test_empty_results_leave_report_ungrounded(self):
        mock_plan = {"answer": json.dumps({"queries": ["zzz"]}), "source": "groq"}
        with patch("research_agent.memory.retrieve_relevant", return_value=[]), \
             patch("research_agent.mcp_client.get_tool_registry", return_value=_registry(*research_agent.RESEARCH_TOOL_ALLOWLIST)), \
             patch("research_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("research_agent.mcp_client.call_mcp_tool", return_value={"result": "No Wikipedia articles found for: 'zzz'.", "error": None}), \
             patch("research_agent.llm_provider.generate_chat", return_value=mock_plan):
            report = self.agent.research("some nonsense query")
        assert report.grounded is False
        assert report.sources == []


class TestMemoryRelevanceDelegation:
    """Relevance gating now lives in memory.retrieve_relevant() (see
    tests/test_memory_retrieval.py); the agent must delegate to it, not re-filter."""

    def setup_method(self):
        self.agent = ResearchAgent()

    def test_uses_relevance_ranked_retrieval_with_question(self):
        with patch("research_agent.memory.retrieve_relevant", return_value=[]) as mock_rel, \
             patch("research_agent.memory.retrieve") as mock_raw, \
             patch.object(ResearchAgent, "_call_tool", return_value=None):
            self.agent.gather("where do I study", ["college"], domain="academic")
        mock_rel.assert_called_once()
        assert mock_rel.call_args.args[0] == "where do I study"
        assert mock_rel.call_args.kwargs["domain"] == "academic"
        mock_raw.assert_not_called()

    def test_no_relevant_facts_means_no_memory_sources(self):
        with patch("research_agent.memory.retrieve_relevant", return_value=[]), \
             patch.object(ResearchAgent, "_call_tool", return_value=None):
            sources = self.agent.gather("what is a binary search tree", ["bst"], domain="academic")
        assert sources == []

    def test_every_relevance_selected_fact_is_used(self):
        remembered = [{"text": "User's college: PSG College of Technology", "score": 0.02, "distance": 1.06}]
        with patch("research_agent.memory.retrieve_relevant", return_value=remembered), \
             patch.object(ResearchAgent, "_call_tool", return_value=None):
            sources = self.agent.gather("what college do I study at", ["college"], domain="academic")
        assert len(sources) == 1
        assert sources[0].origin == "memory"


class TestWikipediaQueryFallback:
    """OpenSearch matches article TITLES, so descriptive queries must be shortened."""

    def setup_method(self):
        self.agent = ResearchAgent()

    def test_full_query_used_when_it_works(self):
        with patch.object(ResearchAgent, "_call_tool", return_value="content") as mock_call:
            found, used = self.agent._wikipedia_lookup("binary search tree", "q")
        assert found == "content"
        assert used == "binary search tree"
        assert mock_call.call_count == 1

    def test_long_query_retried_shortened(self):
        calls = []

        def fake_call(self_, tool, args, question):
            calls.append(args["query"])
            return "article content" if args["query"] == "binary search tree" else None

        with patch.object(ResearchAgent, "_call_tool", fake_call):
            found, used = self.agent._wikipedia_lookup("binary search tree data structure", "q")

        assert found == "article content"
        assert used == "binary search tree"
        assert calls == ["binary search tree data structure", "binary search tree"]

    def test_short_query_not_retried(self):
        with patch.object(ResearchAgent, "_call_tool", return_value=None) as mock_call:
            found, _ = self.agent._wikipedia_lookup("binary tree", "q")
        assert found is None
        assert mock_call.call_count == 1

    def test_both_attempts_failing_returns_none(self):
        with patch.object(ResearchAgent, "_call_tool", return_value=None) as mock_call:
            found, _ = self.agent._wikipedia_lookup("some very long nonsense query here", "q")
        assert found is None
        assert mock_call.call_count == 2


class TestWikipediaQueryFallback:
    """OpenSearch matches TITLES, so descriptive queries must be shortened."""

    def setup_method(self):
        self.agent = ResearchAgent()

    def test_full_query_used_when_it_works(self):
        with patch.object(ResearchAgent, "_call_tool", return_value="content") as mock_call:
            found, used = self.agent._wikipedia_lookup("binary search tree", "q")
        assert found == "content"
        assert used == "binary search tree"
        assert mock_call.call_count == 1

    def test_long_query_retried_shortened(self):
        calls = []

        def fake_call(self_, tool, args, question):
            calls.append(args["query"])
            return "article content" if args["query"] == "binary search tree" else None

        with patch.object(ResearchAgent, "_call_tool", fake_call):
            found, used = self.agent._wikipedia_lookup("binary search tree data structure", "q")

        assert found == "article content"
        assert used == "binary search tree"
        assert calls == ["binary search tree data structure", "binary search tree"]

    def test_short_query_not_retried(self):
        with patch.object(ResearchAgent, "_call_tool", return_value=None) as mock_call:
            found, _ = self.agent._wikipedia_lookup("binary tree", "q")
        assert found is None
        assert mock_call.call_count == 1

    def test_both_attempts_failing_returns_none(self):
        with patch.object(ResearchAgent, "_call_tool", return_value=None) as mock_call:
            found, _ = self.agent._wikipedia_lookup("some very long nonsense query here", "q")
        assert found is None
        assert mock_call.call_count == 2
