"""Which MCP tool a request is handed to (OpenSpec change: roadmap-leftovers).

The shortcuts for the bundled tools used to match substrings, so "will it
snow" (now), "today's headlines" (today) and "update me" (date) all ran the
clock, and a request the model could not place ran whichever tool was first in
the registry.
"""

import pytest

import orchestrator
from mcp_client import MCPToolSpec


def _tool(server, name):
    return MCPToolSpec(server_name=server, tool_name=name, description=f"{name} tool",
                       input_schema={}, qualified_name=f"mcp_{server}_{name}")


REGISTRY = {spec.qualified_name: spec for spec in [
    _tool("codeforces_tools", "cf_contest_list"),          # first on purpose: the old fallback ran it
    _tool("zedek_tools", "current_time"),
    _tool("zedek_tools", "word_count"),
    _tool("zedek_tools", "summarize_text"),
    _tool("weather_news_tools", "get_weather"),
    _tool("weather_news_tools", "search_news"),
    _tool("other_tools", "time_tracker_update"),           # another server's tool with "time" in its name
]}


@pytest.fixture
def model(monkeypatch):
    """Stub the tool-picking model; records whether it was asked."""
    state = {"answer": "", "asked": 0}

    def fake_chat(messages, **kwargs):
        state["asked"] += 1
        if isinstance(state["answer"], Exception):
            raise state["answer"]
        return {"answer": state["answer"], "source": "stub"}

    monkeypatch.setattr(orchestrator.mcp_client, "get_tool_registry", lambda: dict(REGISTRY))
    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake_chat)
    return state


@pytest.mark.parametrize("request_text, tool", [
    ("what time is it", "current_time"),
    ("what's today's date", "current_time"),
    ("what day of the week is it", "current_time"),
    ("what's the current time in utc", "current_time"),
    ("count the words in this: the quick brown fox", "word_count"),
    ("how many characters and words in this sentence", "word_count"),
    ("word count for my essay intro", "word_count"),
    ("summarize this text for me: lorem ipsum", "summarize_text"),
    ("tl;dr this paragraph", "summarize_text"),
    ("search news about isro", "search_news"),                     # the tool's own name
])
def test_shortcut_picks_the_bundled_tool_without_a_model(model, request_text, tool):
    assert orchestrator._select_mcp_tool(request_text).tool_name == tool
    assert model["asked"] == 0


@pytest.mark.parametrize("request_text", [
    "will it snow in shimla", "do you know the weather in pune", "what are today's top news stories",
    "today's headlines please", "update me on codeforces contests", "news about my country",
    "sometimes i wonder about the forecast", "is my password strong",
])
def test_words_that_merely_contain_a_keyword_go_to_the_model(model, request_text):
    model["answer"] = "mcp_weather_news_tools_get_weather"
    assert orchestrator._select_mcp_tool(request_text).tool_name == "get_weather"
    assert model["asked"] == 1


@pytest.mark.parametrize("answer", ["", "I am not sure which tool fits.", RuntimeError("all providers down")])
def test_no_tool_is_chosen_when_the_model_names_none(model, answer):
    model["answer"] = answer
    assert orchestrator._select_mcp_tool("zzz qqq") is None


def test_unplaced_request_runs_nothing(model, monkeypatch):
    from routing_decision import RoutingDecision

    monkeypatch.setattr(orchestrator.mcp_client, "call_mcp_tool", lambda *a, **k: pytest.fail("a tool ran"))
    reply = orchestrator._execute_mcp_tool(RoutingDecision(function="mcp_tool", user_input="zzz qqq"))
    assert reply == "No matching MCP tool found for this request."


def test_explicit_target_still_wins(model):
    chosen = orchestrator._select_mcp_tool("anything", target_tool_qname="mcp_weather_news_tools_get_weather")
    assert chosen.tool_name == "get_weather" and model["asked"] == 0
