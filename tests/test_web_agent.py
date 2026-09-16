"""
Unit & integration tests for the web / browser agent (Roadmap Item 10).

The security properties are the point of this suite:
- The agent NEVER self-approves a Tier 2 browser action — confirmation is
  injected, and the default callback denies.
- Only allowlisted browser tools and http(s) URLs are ever executed.
- A denied or blocked action stops the loop instead of routing around it.
"""

import json
from unittest.mock import patch

import pytest

import web_agent
from web_agent import WebAgent, BrowseReport, Step


class _FakeToolSpec:
    def __init__(self, description="browser automation tool"):
        self.description = description


def _registry(*names):
    return {name: _FakeToolSpec() for name in names}


_ALL_BROWSER_TOOLS = _registry(*web_agent.BROWSER_TOOL_ALLOWLIST)


class TestUrlSafety:
    def test_https_url_is_safe(self):
        assert web_agent.is_safe_url("https://example.com/page") is True

    def test_http_url_is_safe(self):
        assert web_agent.is_safe_url("http://example.com") is True

    def test_file_scheme_rejected(self):
        assert web_agent.is_safe_url("file:///etc/passwd") is False

    def test_javascript_scheme_rejected(self):
        assert web_agent.is_safe_url("javascript:alert(1)") is False

    def test_relative_path_rejected(self):
        assert web_agent.is_safe_url("/just/a/path") is False

    def test_empty_and_none_rejected(self):
        assert web_agent.is_safe_url("") is False
        assert web_agent.is_safe_url(None) is False


class TestDefaultConfirmation:
    def test_default_callback_denies(self):
        assert web_agent.deny_all("run this dangerous thing?") is False

    def test_browse_without_confirm_fn_never_executes(self):
        """The default (deny) callback must prevent any tool execution."""
        agent = WebAgent(max_steps=2)
        decision = {"tool": "navigate", "args": {"url": "https://example.com"}, "done": False, "reason": ""}

        with patch.object(WebAgent, "decide_next_action", return_value=decision), \
             patch("web_agent.mcp_client.get_tool_registry", return_value=_ALL_BROWSER_TOOLS), \
             patch("web_agent.gate", return_value={"action": "confirm", "message": "ok?"}), \
             patch("web_agent.mcp_client.call_mcp_tool") as mock_call, \
             patch("web_agent.llm_provider.generate_chat", return_value={"answer": "n/a", "source": "x"}):
            report = agent.browse("go to example.com")

        mock_call.assert_not_called()
        assert report.completed is False
        assert any(s.status == "denied" for s in report.steps)


class TestExecuteAction:
    def setup_method(self):
        self.agent = WebAgent()

    def _confirm_yes(self, _msg):
        return True

    def test_unknown_tool_rejected(self):
        with patch("web_agent.mcp_client.call_mcp_tool") as mock_call:
            step = self.agent.execute_action("delete_everything", {"url": "https://a.com"}, "goal", 1, self._confirm_yes)
        assert step.status == "rejected"
        mock_call.assert_not_called()

    def test_unsafe_url_rejected_before_gate(self):
        with patch("web_agent.gate") as mock_gate, \
             patch("web_agent.mcp_client.call_mcp_tool") as mock_call:
            step = self.agent.execute_action("navigate", {"url": "file:///etc/passwd"}, "goal", 1, self._confirm_yes)
        assert step.status == "rejected"
        mock_gate.assert_not_called()
        mock_call.assert_not_called()

    def test_missing_server_reports_error(self):
        with patch("web_agent.mcp_client.get_tool_registry", return_value={}), \
             patch("web_agent.mcp_client.call_mcp_tool") as mock_call:
            step = self.agent.execute_action("navigate", {"url": "https://a.com"}, "goal", 1, self._confirm_yes)
        assert step.status == "error"
        mock_call.assert_not_called()

    def test_blocked_by_gate_not_executed(self):
        with patch("web_agent.mcp_client.get_tool_registry", return_value=_ALL_BROWSER_TOOLS), \
             patch("web_agent.gate", return_value={"action": "blocked", "message": "tier 3"}), \
             patch("web_agent.mcp_client.call_mcp_tool") as mock_call:
            step = self.agent.execute_action("click", {"url": "https://a.com", "selector": "#b"}, "goal", 1, self._confirm_yes)
        assert step.status == "blocked"
        mock_call.assert_not_called()

    def test_denied_confirmation_not_executed(self):
        with patch("web_agent.mcp_client.get_tool_registry", return_value=_ALL_BROWSER_TOOLS), \
             patch("web_agent.gate", return_value={"action": "confirm", "message": "ok?"}), \
             patch("web_agent.mcp_client.call_mcp_tool") as mock_call:
            step = self.agent.execute_action("click", {"url": "https://a.com", "selector": "#b"}, "goal", 1, lambda _m: False)
        assert step.status == "denied"
        mock_call.assert_not_called()

    def test_confirmed_action_executes(self):
        with patch("web_agent.mcp_client.get_tool_registry", return_value=_ALL_BROWSER_TOOLS), \
             patch("web_agent.gate", return_value={"action": "confirm", "message": "ok?"}), \
             patch("web_agent.mcp_client.call_mcp_tool", return_value={"result": "Page title: Example", "error": None}):
            step = self.agent.execute_action("navigate", {"url": "https://a.com"}, "goal", 1, self._confirm_yes)
        assert step.status == "ok"
        assert "Example" in step.observation

    def test_tool_error_recorded(self):
        with patch("web_agent.mcp_client.get_tool_registry", return_value=_ALL_BROWSER_TOOLS), \
             patch("web_agent.gate", return_value={"action": "allow", "message": ""}), \
             patch("web_agent.mcp_client.call_mcp_tool", return_value={"result": None, "error": "timeout"}):
            step = self.agent.execute_action("navigate", {"url": "https://a.com"}, "goal", 1, self._confirm_yes)
        assert step.status == "error"
        assert "timeout" in step.reason


class TestDecideNextAction:
    def setup_method(self):
        self.agent = WebAgent()

    def test_parses_decision(self):
        payload = {"tool": "navigate", "args": {"url": "https://a.com"}, "done": False, "reason": "load page"}
        with patch("web_agent.llm_provider.generate_chat", return_value={"answer": json.dumps(payload), "source": "groq"}):
            decision = self.agent.decide_next_action("go to a.com", [])
        assert decision["tool"] == "navigate"
        assert decision["done"] is False

    def test_planner_failure_terminates_loop(self):
        with patch("web_agent.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            decision = self.agent.decide_next_action("goal", [])
        assert decision["done"] is True

    def test_malformed_args_coerced_to_empty_dict(self):
        payload = {"tool": "navigate", "args": "not-an-object", "done": False}
        with patch("web_agent.llm_provider.generate_chat", return_value={"answer": json.dumps(payload), "source": "x"}):
            decision = self.agent.decide_next_action("goal", [])
        assert decision["args"] == {}


class TestBrowseLoop:
    def test_empty_goal_short_circuits(self):
        report = WebAgent().browse("  ")
        assert report.completed is False

    def test_loop_stops_when_planner_says_done(self):
        agent = WebAgent(max_steps=3)
        with patch.object(WebAgent, "decide_next_action", return_value={"tool": "", "args": {}, "done": True, "reason": "already satisfied"}), \
             patch.object(WebAgent, "execute_action") as mock_exec:
            report = agent.browse("some goal")
        mock_exec.assert_not_called()
        assert "already satisfied" in report.notes

    def test_denied_step_stops_the_loop(self):
        agent = WebAgent(max_steps=4)
        decision = {"tool": "click", "args": {"url": "https://a.com", "selector": "#x"}, "done": False, "reason": ""}
        denied = Step(index=1, tool="click", args={}, status="denied", reason="You declined this browser action.")

        with patch.object(WebAgent, "decide_next_action", return_value=decision), \
             patch.object(WebAgent, "execute_action", return_value=denied) as mock_exec, \
             patch.object(WebAgent, "summarize", return_value="stopped"):
            report = agent.browse("click something")

        assert mock_exec.call_count == 1
        assert report.completed is False

    def test_step_budget_is_enforced(self):
        agent = WebAgent(max_steps=2)
        decision = {"tool": "navigate", "args": {"url": "https://a.com"}, "done": False, "reason": ""}
        ok_step = Step(index=1, tool="navigate", args={}, status="ok", observation="loaded")

        with patch.object(WebAgent, "decide_next_action", return_value=decision), \
             patch.object(WebAgent, "execute_action", return_value=ok_step) as mock_exec, \
             patch.object(WebAgent, "summarize", return_value="done"):
            report = agent.browse("keep going")

        assert mock_exec.call_count == 2
        assert any("limit" in n for n in report.notes)


class TestSummarize:
    def test_no_successful_steps_says_so(self):
        summary = WebAgent().summarize("goal", [Step(index=1, tool="navigate", args={}, status="denied")])
        assert "wasn't able" in summary.lower() or "nothing was submitted" in summary.lower()

    def test_summary_uses_observations(self):
        steps = [Step(index=1, tool="get_text", args={"url": "https://a.com"}, status="ok", observation="Hello world")]
        with patch("web_agent.llm_provider.generate_chat", return_value={"answer": "The page says hello world.", "source": "gemini"}):
            summary = WebAgent().summarize("what does the page say", steps)
        assert "hello world" in summary.lower()

    def test_provider_failure_returns_raw_observations(self):
        steps = [Step(index=1, tool="get_text", args={"url": "https://a.com"}, status="ok", observation="Raw page text")]
        with patch("web_agent.llm_provider.generate_chat", side_effect=RuntimeError("down")):
            summary = WebAgent().summarize("goal", steps)
        assert "Raw page text" in summary


class TestFormatting:
    def test_format_report_includes_steps_and_notes(self):
        report = BrowseReport(
            goal="g", summary="a summary",
            steps=[Step(index=1, tool="navigate", args={"url": "https://a.com"}, status="ok")],
            notes=["a note"],
        )
        rendered = web_agent.format_browse_report(report)
        assert "a summary" in rendered
        assert "navigate" in rendered
        assert "a note" in rendered


class TestOrchestratorIntegration:
    def test_web_task_dispatches_to_agent(self):
        import orchestrator

        report = BrowseReport(goal="g", summary="browsed it", completed=True)
        with patch.object(orchestrator.WEB_AGENT, "browse", return_value=report) as mock_browse, \
             patch("orchestrator.classifier.add_utterance_dynamically"):
            out = orchestrator.execute({
                "function": "web_task",
                "domain": "personal",
                "confidence": "high",
                "_original_input": "open https://example.com and click login",
            })

        mock_browse.assert_called_once()
        # The URL in the request should be handed to the agent as the start URL.
        assert mock_browse.call_args.kwargs["start_url"] == "https://example.com"
        assert "browsed it" in out

    def test_orchestrator_injects_confirmation_callback(self):
        import orchestrator

        report = BrowseReport(goal="g", summary="s", completed=False)
        with patch.object(orchestrator.WEB_AGENT, "browse", return_value=report) as mock_browse:
            orchestrator.execute({
                "function": "web_task",
                "domain": "personal",
                "confidence": "high",
                "_original_input": "click the button on example.com",
            })

        # The agent must never be left to self-approve.
        assert mock_browse.call_args.kwargs["confirm_fn"] is orchestrator._interactive_confirm
