"""Research / RAG specialist for the Zedek assistant (Roadmap Item 10).

Answers research questions by grounding every claim in retrieved sources
rather than model recall. Follows the project's established specialist
pattern: plan → gather → synthesize → cite.

Sources, in priority order:
  1. Long-term memory (ChromaDB) — what the user has already told Zedek.
  2. Live read-only MCP tools — Wikipedia, arXiv, Semantic Scholar, direct URL fetch.

Safety boundaries:
  - Only READ-ONLY MCP tools are ever called; the allowlist is explicit.
  - Every MCP call passes through the tier gate, exactly like any other
    action. A tool that the gate blocks (or escalates to confirmation) is
    skipped, never auto-executed.
  - Synthesis is strictly extractive: if the gathered sources do not answer
    the question, the agent says so instead of inventing an answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import llm_provider
import llm_schemas
import mcp_client
import memory
from text_sanitizer import strip_invisible
from tier_gate import gate
from zedek_logger import get_logger

log = get_logger("research_agent")

# Read-only research tools this agent is permitted to call. Anything not on
# this list is never invoked, regardless of what the planner suggests.
WIKIPEDIA_TOOL = "mcp_online_tools_search_wikipedia"
ARXIV_TOOL = "mcp_online_tools_search_arxiv"
FETCH_URL_TOOL = "mcp_online_tools_fetch_url"
SCHOLAR_TOOL = "mcp_semantic_scholar_tools_scholar_search_papers"

RESEARCH_TOOL_ALLOWLIST = {
    WIKIPEDIA_TOOL,
    ARXIV_TOOL,
    FETCH_URL_TOOL,
    SCHOLAR_TOOL,
}

MAX_SOURCES = 6
MAX_SOURCE_CHARS = 4000
_URL_RE = re.compile(r"https?://[^\s<>\"']+")

# The research tools report "nothing found" in-band as ordinary text. That is a
# negative result, not evidence — counting it as a source would inflate the
# source count, mark a report "grounded", and pad the synthesis prompt with
# sentences saying nothing was found.
_EMPTY_RESULT_RE = re.compile(
    r"^(no|could not)\b[^.\n]{0,80}\b(found|results?|resolve)\b", re.IGNORECASE,
)

# Question shapes that call for academic sources rather than encyclopedic ones.
_ACADEMIC_MARKERS = [
    "paper", "papers", "research", "study", "studies", "publication",
    "arxiv", "journal", "citation", "cited", "author", "literature",
    "state of the art", "sota", "benchmark", "thesis", "survey",
]


@dataclass
class Source:
    """One retrieved piece of evidence, carried through to the citation list."""

    label: str
    origin: str
    content: str
    query: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "origin": self.origin,
            "query": self.query,
            "excerpt": self.content[:200],
        }


@dataclass
class ResearchReport:
    """Structured result of a research run."""

    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    grounded: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": [s.to_dict() for s in self.sources],
            "queries": self.queries,
            "grounded": self.grounded,
            "notes": self.notes,
        }


def _truncate(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def is_academic_question(question: str) -> bool:
    """Whether the question is asking about scholarly literature."""
    lowered = (question or "").lower()
    return any(marker in lowered for marker in _ACADEMIC_MARKERS)


def extract_urls(text: str) -> list[str]:
    """Pull any explicit URLs the user included — those get fetched directly."""
    return _URL_RE.findall(text or "")


class ResearchAgent:
    """Grounded question answering over memory plus read-only live sources."""

    def __init__(self, max_sources: int = MAX_SOURCES) -> None:
        self.max_sources = max_sources

    # ── Step 1: Plan which queries to run ────────────────────────────────

    def plan_queries(self, question: str) -> list[str]:
        """Turn a question into 1–3 concrete search queries.

        Falls back to the raw question if the LLM is unavailable — research
        should degrade to a plain search, never fail outright.
        """
        prompt = f"""Turn this research question into 1-3 short, high-signal search queries.

Question: {question}

Rules:
- Each query should be keyword-style, not a full sentence.
- Cover distinct angles if the question has several parts.
- Do not invent specifics the question does not contain.

Return ONLY valid JSON: {{"queries": ["query one", "query two"]}}"""

        try:
            result = llm_provider.generate_structured(
                [{"role": "user", "content": prompt}], llm_schemas.ResearchQueries, task="research",
            )
            queries = [q.strip() for q in result["data"].queries if q.strip()]
            if queries:
                log.info("research_queries_planned", extra={
                    "question": question[:200], "count": len(queries), "source": result.get("source"),
                })
                return queries[:3]
        except Exception as err:
            log.info("research_query_planning_failed", extra={"error": str(err)})
        return [question.strip()] if question and question.strip() else []

    # ── Step 2: Gather sources ───────────────────────────────────────────

    def _call_tool(self, qualified_name: str, args: dict[str, Any], question: str) -> str | None:
        """Call one read-only MCP tool through the tier gate.

        Returns the tool's text output, or None when the tool is not
        available, is disallowed, is gated behind confirmation, or errors.
        """
        if qualified_name not in RESEARCH_TOOL_ALLOWLIST:
            log.info("research_tool_not_allowlisted", extra={"tool": qualified_name})
            return None

        registry = mcp_client.get_tool_registry()
        tool_spec = registry.get(qualified_name)
        if tool_spec is None:
            log.info("research_tool_unavailable", extra={"tool": qualified_name})
            return None

        decision = gate(qualified_name, args, user_input=question,
                        tool_description=tool_spec.description)
        if decision["action"] in ("blocked", "confirm"):
            # Research runs unattended — never auto-approve a gated action.
            log.info("research_tool_gated_skip", extra={
                "tool": qualified_name, "action": decision["action"],
            })
            return None

        result = mcp_client.call_mcp_tool(qualified_name, args)
        if result.get("error"):
            log.info("research_tool_error", extra={"tool": qualified_name, "error": str(result["error"])})
            return None

        output = result.get("result")
        if output is None or str(output).strip() == "":
            return None

        text = str(output)
        # The online tools signal failure in-band rather than by raising.
        if text.startswith("[error]") or text.startswith("[blocked]"):
            log.info("research_tool_returned_error_marker", extra={
                "tool": qualified_name, "marker": text[:80],
            })
            return None
        if _EMPTY_RESULT_RE.match(text.strip()):
            log.info("research_tool_returned_no_results", extra={"tool": qualified_name})
            return None
        return text

    def _wikipedia_lookup(self, query: str, question: str) -> tuple[str | None, str]:
        """Look up Wikipedia, shortening the query if the full one finds nothing.

        Wikipedia's OpenSearch matches article TITLES, so a descriptive query
        like "binary search tree data structure" returns nothing while the
        article "Binary search tree" exists. The planner naturally writes the
        descriptive form, so retry once with the leading words only.

        Returns (content_or_None, the query that actually worked).
        """
        found = self._call_tool(WIKIPEDIA_TOOL, {"query": query, "limit": 2}, question)
        if found:
            return found, query

        words = query.split()
        if len(words) > 3:
            shortened = " ".join(words[:3])
            log.info("research_wikipedia_retry_shortened", extra={
                "original": query, "shortened": shortened,
            })
            found = self._call_tool(WIKIPEDIA_TOOL, {"query": shortened, "limit": 2}, question)
            if found:
                return found, shortened

        return None, query

    def gather(self, question: str, queries: list[str], domain: str = "academic") -> list[Source]:
        """Collect evidence from memory and the live read-only research tools."""
        sources: list[Source] = []
        counter = 1

        def add(origin: str, content: str, query: str = "") -> None:
            nonlocal counter
            if len(sources) >= self.max_sources:
                return
            sources.append(Source(
                label=f"S{counter}", origin=origin, content=_truncate(strip_invisible(content)), query=query,
            ))
            counter += 1

        # 1. What the user already told us (RAG over ChromaDB). Relevance is
        # gated by the cross-encoder in memory.retrieve_relevant(), not by raw
        # L2 distance, which shifts with query phrasing.
        try:
            remembered = memory.retrieve_relevant(question, domain=domain, content_type="fact", top_k=3)
            for item in remembered:
                add("memory", item["text"])
        except Exception as err:
            log.info("research_memory_retrieval_failed", extra={"error": str(err)})

        # 2. Any URL the user explicitly named gets fetched directly.
        for url in extract_urls(question)[:2]:
            fetched = self._call_tool(FETCH_URL_TOOL, {"url": url}, question)
            if fetched:
                add(f"url:{url}", fetched, query=url)

        # 3. Live search, routed by question shape.
        academic = is_academic_question(question)
        for query in queries:
            if len(sources) >= self.max_sources:
                break
            if academic:
                papers = self._call_tool(SCHOLAR_TOOL, {"query": query, "max_results": 3}, question)
                if papers:
                    add("semantic_scholar", papers, query=query)
                arxiv = self._call_tool(ARXIV_TOOL, {"query": query, "max_results": 3}, question)
                if arxiv:
                    add("arxiv", arxiv, query=query)
            wiki, used_query = self._wikipedia_lookup(query, question)
            if wiki:
                add("wikipedia", wiki, query=used_query)

        log.info("research_sources_gathered", extra={
            "question": question[:200],
            "source_count": len(sources),
            "origins": [s.origin for s in sources],
        })
        return sources

    # ── Step 3: Synthesize a grounded, cited answer ──────────────────────

    def synthesize(self, question: str, sources: list[Source]) -> str:
        """Write an answer that cites only the gathered sources."""
        if not sources:
            return (
                "I couldn't retrieve any sources for that, so I don't have a grounded "
                "answer. That's usually no internet access or the research tools being "
                "unavailable — I'd rather tell you that than guess."
            )

        source_block = "\n\n".join(
            f"[{s.label}] (from {s.origin})\n{s.content}" for s in sources
        )

        prompt = f"""Answer the question using ONLY the sources below.

Question: {question}

Sources:
{source_block}

Rules:
- Ground every factual claim in a source and cite it inline as [S1], [S2], etc.
- If the sources only partially answer the question, answer the part they cover
  and state plainly which part is not covered.
- If the sources do not answer the question at all, say exactly that — do NOT
  fall back on your own background knowledge, and do NOT invent names, numbers,
  dates, or citations.
- Be concise and direct. No preamble."""

        try:
            result = llm_provider.generate_chat(
                [{"role": "user", "content": prompt}], task="research",
            )
            answer = (result.get("answer") or "").strip()
            log.info("research_answer_synthesized", extra={
                "source_count": len(sources), "source": result.get("source"),
            })
            if answer:
                return answer
        except Exception as err:
            log.info("research_synthesis_failed", extra={"error": str(err)})

        # Synthesis failed — hand back the raw evidence rather than nothing.
        return (
            "I gathered sources but couldn't synthesize them (the language provider "
            "was unavailable). Here is the raw material:\n\n" + source_block[:2000]
        )

    # ── Full pipeline ────────────────────────────────────────────────────

    def research(self, question: str, domain: str = "academic") -> ResearchReport:
        """Run plan → gather → synthesize and return a structured report."""
        question = (question or "").strip()
        if not question:
            return ResearchReport(
                question=question,
                answer="I need an actual question to research.",
                grounded=False,
            )

        log.info("research_started", extra={"question": question[:200], "domain": domain})

        queries = self.plan_queries(question)
        sources = self.gather(question, queries, domain=domain)
        answer = self.synthesize(question, sources)

        notes: list[str] = []
        if not sources:
            notes.append("No sources retrieved — answer is not grounded.")

        report = ResearchReport(
            question=question,
            answer=answer,
            sources=sources,
            queries=queries,
            grounded=bool(sources),
            notes=notes,
        )
        log.info("research_finished", extra={
            "question": question[:200],
            "grounded": report.grounded,
            "source_count": len(sources),
        })
        return report


def format_research_report(report: ResearchReport) -> str:
    """Render a report for the terminal, with its citation list."""
    lines = [report.answer]
    if report.sources:
        lines.append("\nSources:")
        for source in report.sources:
            detail = f" — {source.query}" if source.query else ""
            lines.append(f"  [{source.label}] {source.origin}{detail}")
    if report.notes:
        lines.append("\n" + "\n".join(f"Note: {n}" for n in report.notes))
    return "\n".join(lines)
