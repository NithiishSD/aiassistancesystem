"""The confirmation channel and reject-with-feedback (OpenSpec change: confirmation-channel)."""

from unittest.mock import patch

import pytest

import confirmation
from confirmation import Answer, DenyChannel, TerminalChannel, parse_reply
from routing_decision import RoutingDecision
from web_agent import Step, WebAgent


class FakeChannel:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.asked, self.notices = [], []

    def ask(self, message):
        self.asked.append(message)
        return parse_reply(self.replies.pop(0))

    def notify(self, message):
        self.notices.append(message)


class TestParseReply:
    @pytest.mark.parametrize("raw", ["y", "Y", " yes ", "YES"])
    def test_only_explicit_yes_approves(self, raw):
        assert parse_reply(raw) == Answer(True) and parse_reply(raw)

    @pytest.mark.parametrize("raw", ["", None, "n", "no", "ok", "sure", "yes please do", "yep", "y n"])
    def test_everything_else_refuses(self, raw):
        assert not parse_reply(raw)

    @pytest.mark.parametrize("raw,reason", [
        ("no, use the staging file instead", "use the staging file instead"),
        ("n - wrong page", "wrong page"),
        ("not that page, open the docs", "not that page, open the docs"),
        ("nope", "nope"),
        ("n", ""), ("No.", ""), ("", ""),
    ])
    def test_reason(self, raw, reason):
        assert parse_reply(raw).reason == reason

    def test_reason_is_bounded(self):
        assert len(parse_reply("x" * 5000).reason) == confirmation.MAX_REASON_CHARS


class TestChannels:
    def test_terminal_reads_one_line(self, capsys):
        with patch("builtins.input", return_value="no, later") as read:
            answer = TerminalChannel().ask("Run it?")
        assert answer == Answer(False, "later") and read.call_count == 1
        assert "Run it?" in capsys.readouterr().out

    @pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
    def test_closed_input_refuses(self, error):
        with patch("builtins.input", side_effect=error):
            assert TerminalChannel().ask("Run it?") == Answer(False)

    def test_deny_channel_never_reads_the_terminal(self):
        with patch("builtins.input") as read, confirmation.use_channel(DenyChannel()):
            assert confirmation.ask("Run it?") == Answer(False)
            confirmation.notify("fyi")
        read.assert_not_called()

    def test_terminal_is_the_default(self):
        assert isinstance(confirmation.get_channel(), TerminalChannel)

    def test_channel_restored_after_error(self):
        before = confirmation.get_channel()
        with pytest.raises(RuntimeError):
            with confirmation.use_channel(FakeChannel()):
                raise RuntimeError("task failed")
        assert confirmation.get_channel() is before

    def test_failing_channel_refuses(self):
        class Broken:
            def ask(self, message):
                raise OSError("socket closed")

        with confirmation.use_channel(Broken()):
            assert confirmation.ask("Run it?") == Answer(False)

    def test_channel_returning_a_bare_true_is_honoured_but_nothing_else_is(self):
        class Loose:
            def __init__(self, value):
                self.value = value

            def ask(self, message):
                return self.value

        for value, approved in ((True, True), (False, False), ("yes", False), (1, False), (None, False)):
            with confirmation.use_channel(Loose(value)):
                assert confirmation.ask("Run it?").approved is approved


class TestOrchestratorUsesTheChannel:
    def _tier2(self, reply):
        import orchestrator

        channel = FakeChannel(reply)
        ran = []
        decision = RoutingDecision("open_application", "open calculator", args={"app_name": "calculator"})
        with confirmation.use_channel(channel), \
             patch("builtins.input") as read, \
             patch.object(orchestrator, "gate", return_value={"tier": 2, "action": "confirm", "message": "Open it?"}), \
             patch.dict(orchestrator.AVAILABLE_FUNCTIONS, {"open_application": lambda **kw: ran.append(kw) or "opened"}):
            reply_text = orchestrator.execute(decision)
        read.assert_not_called()
        return reply_text, channel, ran

    def test_refusal_cancels(self):
        reply, channel, ran = self._tier2("no, not now")
        assert channel.asked == ["Open it?"] and ran == [] and reply == "Cancelled."

    def test_approval_runs(self):
        _, channel, ran = self._tier2("y")
        assert ran == [{"app_name": "calculator"}]

    def test_no_direct_terminal_reads_left(self):
        import inspect
        import orchestrator

        assert 'input("> ")' not in inspect.getsource(orchestrator)


class TestCodingPlanRevision:
    def _run(self, *replies):
        import orchestrator

        channel = FakeChannel(*replies)
        plans = iter([{"goal": f"plan {n}", "steps": ["edit"], "constraints": []} for n in range(1, 9)])
        with confirmation.use_channel(channel), \
             patch.object(orchestrator, "gate", return_value={"tier": 1, "action": "auto", "message": ""}), \
             patch.object(orchestrator.CODING_SPECIALIST, "plan_task", side_effect=lambda req: next(plans)) as plan_task, \
             patch.object(orchestrator.CODING_SPECIALIST, "implement_and_verify",
                          return_value={"status": "failed", "attempts": 1}) as implement, \
             patch.object(orchestrator, "format_coding_result", return_value="summary"), \
             patch.object(orchestrator.WATCHDOG, "register_plan", return_value="p1"):
            reply = orchestrator._run_coding_task(RoutingDecision("coding_task", "add a parser"), "personal")
        return reply, channel, plan_task, implement

    def test_revised_plan_is_the_one_implemented(self):
        _, channel, plan_task, implement = self._run("no, don't touch config.py", "y")
        assert plan_task.call_count == 2
        assert "don't touch config.py" in plan_task.call_args_list[1].args[0]
        assert "plan 1" in channel.asked[0] and "plan 2" in channel.asked[1]
        assert implement.call_args.kwargs["plan"]["goal"] == "plan 2"

    def test_bare_refusal_ends(self):
        reply, _, plan_task, implement = self._run("n")
        assert plan_task.call_count == 1 and "Plan rejected" in reply
        implement.assert_not_called()

    def test_revision_limit(self):
        reply, channel, plan_task, implement = self._run("no, a", "no, b", "no, c")
        assert plan_task.call_count == 3 and len(channel.asked) == 3 and "Plan rejected" in reply
        implement.assert_not_called()


_NAV = {"tool": "navigate", "target": "T1", "selector": None, "text": None, "done": False, "reason": ""}
_DONE = {**_NAV, "done": True}


class _Spec:
    description = "browser tool"


class TestBrowserRejectWithFeedback:
    def _browse(self, replies, decisions):
        import web_agent

        channel = FakeChannel(*replies)
        queue = iter(decisions)
        histories = []

        def decide(self, goal, history, start_url=None, targets=None):
            histories.append("\n".join(WebAgent._describe_step(s) for s in history))
            return next(queue)

        with patch.object(WebAgent, "decide_next_action", decide), \
             patch.object(web_agent.mcp_client, "get_tool_registry",
                          return_value={name: _Spec() for name in web_agent.BROWSER_TOOL_ALLOWLIST}), \
             patch.object(web_agent.mcp_client, "call_mcp_tool", return_value={"result": "Example"}) as call, \
             patch.object(web_agent, "gate", return_value={"action": "confirm", "tier": 2, "message": "Run?"}), \
             patch.object(WebAgent, "extract", return_value=None), \
             patch.object(WebAgent, "summarize", return_value="s"):
            report = WebAgent(max_steps=4).browse("open example.com and example.org", confirm_fn=channel.ask)
        return report, channel, call, histories

    def test_reason_reaches_the_planner_and_next_action_asks_again(self):
        other = {**_NAV, "target": "T2"}
        report, channel, call, histories = self._browse(["not that page, open the other", "y"], [_NAV, other, _DONE])
        assert [s.status for s in report.steps] == ["denied", "ok"]
        assert 'The user said: "not that page, open the other"' in histories[1]
        assert len(channel.asked) == 2 and call.call_count == 1
        assert call.call_args.args[1]["url"] == "https://example.org"

    def test_bare_refusal_ends_the_run(self):
        report, channel, call, histories = self._browse(["n"], [_NAV, _NAV])
        assert len(report.steps) == 1 and len(histories) == 1
        call.assert_not_called()

    def test_same_action_again_ends_without_asking(self):
        report, channel, call, _ = self._browse(["no, wrong site"], [_NAV, _NAV, _NAV])
        assert len(channel.asked) == 1 and len(report.steps) == 1
        assert any("already declined" in note for note in report.notes)
        call.assert_not_called()

    def test_plain_bool_confirm_fn_still_works(self):
        import web_agent

        step = None
        with patch.object(web_agent.mcp_client, "get_tool_registry",
                          return_value={name: _Spec() for name in web_agent.BROWSER_TOOL_ALLOWLIST}), \
             patch.object(web_agent, "gate", return_value={"action": "confirm", "tier": 2, "message": "Run?"}):
            step = WebAgent().execute_action("navigate", {"url": "https://example.com"}, "open example.com", 1,
                                             lambda message: False)
        assert step.status == "denied" and step.feedback == ""
