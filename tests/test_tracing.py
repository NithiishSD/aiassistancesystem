"""Per-turn tracing and standard LLM call records (OpenSpec change: add-request-tracing).

No network: providers, requests.post and ollama.chat are stubbed.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

import llm_provider as lp
import zedek_logger


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def capture():
    handler = _Capture()
    # get_logger first: it skips setup (level, trace filter) on a logger that
    # already has handlers, so the capture handler must be added after it.
    loggers = [zedek_logger.get_logger(n) for n in ("orchestrator", "llm_provider", "trace_test")]
    for lg in loggers:
        lg.addHandler(handler)
    yield handler
    for lg in loggers:
        lg.removeHandler(handler)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    lp._reset_health_for_tests()
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)
    yield
    lp._reset_health_for_tests()


def _trace(record):
    return getattr(record, "trace_id", None)


# ── 1.1 trace context ───────────────────────────────────────────────────────

class TestTraceContext:
    def test_records_inside_carry_trace_and_conversation(self, capture):
        log = zedek_logger.get_logger("trace_test")
        with zedek_logger.trace_context() as trace_id:
            log.info("inside")
        record = capture.records[-1]
        assert _trace(record) == trace_id
        assert record.__dict__["gen_ai.conversation.id"] == zedek_logger.SESSION_ID

    def test_records_outside_carry_neither(self, capture):
        log = zedek_logger.get_logger("trace_test")
        log.info("outside")
        record = capture.records[-1]
        assert _trace(record) is None
        assert "gen_ai.conversation.id" not in record.__dict__

    def test_sequential_turns_differ_but_share_session(self, capture):
        log = zedek_logger.get_logger("trace_test")
        with zedek_logger.trace_context():
            log.info("one")
        with zedek_logger.trace_context():
            log.info("two")
        first, second = capture.records[-2:]
        assert _trace(first) != _trace(second)
        assert first.__dict__["gen_ai.conversation.id"] == second.__dict__["gen_ai.conversation.id"]

    def test_nested_context_restores_outer(self):
        with zedek_logger.trace_context() as outer:
            with zedek_logger.trace_context() as inner:
                assert zedek_logger.current_trace_id() == inner
            assert zedek_logger.current_trace_id() == outer
        assert zedek_logger.current_trace_id() is None


# ── 1.2 handle() is the trace boundary ──────────────────────────────────────

class TestHandleIsTraced:
    def test_one_turn_one_trace_across_modules(self, capture):
        import orchestrator

        def fake_single(text):
            lp.log.info("provider_side_event")
            return "ok"

        with patch.object(orchestrator, "should_ask_ambiguous_term_question", return_value=False), \
             patch.object(orchestrator, "should_treat_as_disambiguation", return_value=False), \
             patch.object(orchestrator.task_planner, "should_decompose", return_value=False), \
             patch.object(orchestrator, "_handle_single", side_effect=fake_single):
            orchestrator.handle("first message")
            first = [r for r in capture.records if r.getMessage() in ("turn_started", "provider_side_event")]
            capture.records.clear()
            orchestrator.handle("second message")
            second = [r for r in capture.records if r.getMessage() in ("turn_started", "provider_side_event")]

        assert {r.name for r in first} == {"orchestrator", "llm_provider"}
        assert len({_trace(r) for r in first}) == 1 and _trace(first[0]) is not None
        assert _trace(second[0]) != _trace(first[0])

    def test_turn_started_does_not_log_message_text(self, capture):
        import orchestrator
        with patch.object(orchestrator, "_handle_turn", return_value="ok"):
            orchestrator.handle("my secret message text")
        started = [r for r in capture.records if r.getMessage() == "turn_started"][-1]
        assert "my secret message text" not in str(started.__dict__)


# ── 2.1 ProviderReply parsing ───────────────────────────────────────────────

def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestReplyParsing:
    def test_openai_compatible_usage(self):
        payload = {"model": "llama-3.3-70b-versatile",
                   "choices": [{"message": {"content": "hi"}}],
                   "usage": {"prompt_tokens": 120, "completion_tokens": 40}}
        with patch.object(lp.requests, "post", return_value=_response(payload)):
            reply = lp._openai_compatible("groq", "https://x", "key", "req-model", [], False)
        assert reply == lp.ProviderReply("hi", "req-model", "llama-3.3-70b-versatile", 120, 40)

    def test_gemini_usage_metadata(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
        payload = {"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}],
                   "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
                   "modelVersion": "gemini-2.5-flash-001"}
        with patch.object(lp.requests, "post", return_value=_response(payload)):
            reply = lp._gemini([{"role": "user", "content": "q"}], False)
        assert (reply.text, reply.input_tokens, reply.output_tokens) == ("ab", 11, 7)
        assert reply.request_model == "gemini-2.5-flash"
        assert reply.response_model == "gemini-2.5-flash-001"

    def test_ollama_counts(self):
        fake = {"model": "llama3.1:8b", "message": {"content": "local"},
                "prompt_eval_count": 30, "eval_count": 9}
        with patch.object(lp.ollama, "chat", return_value=fake):
            reply = lp._local([], False)
        assert (reply.input_tokens, reply.output_tokens, reply.response_model) == (30, 9, "llama3.1:8b")

    def test_missing_usage_is_none_not_error(self):
        payload = {"choices": [{"message": {"content": "hi"}}]}
        with patch.object(lp.requests, "post", return_value=_response(payload)):
            reply = lp._openai_compatible("groq", "https://x", "key", "req-model", [], False)
        assert reply.input_tokens is None and reply.output_tokens is None
        assert reply.response_model == "req-model"

    @pytest.mark.parametrize("junk", ["120", 1.5, True, None, [1]])
    def test_non_int_usage_is_none(self, junk):
        payload = {"choices": [{"message": {"content": "hi"}}],
                   "usage": {"prompt_tokens": junk, "completion_tokens": junk}}
        with patch.object(lp.requests, "post", return_value=_response(payload)):
            reply = lp._openai_compatible("groq", "https://x", "key", "m", [], False)
        assert reply.input_tokens is None


# ── 2.2 standard call record ────────────────────────────────────────────────

def _events(capture):
    return [r for r in capture.records if r.getMessage() == "gen_ai.client.operation"]


class TestGenAiEvent:
    def _provider(self, reply):
        return lambda messages, json_mode: reply

    def test_cloud_call_record(self, monkeypatch, capture):
        reply = lp.ProviderReply("SECRET-ANSWER", "gemini-2.5-flash", "gemini-2.5-flash-001", 120, 40)
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"gemini": self._provider(reply)})
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["gemini", "local"]})

        result = lp.generate_chat([{"role": "user", "content": "SECRET-PROMPT"}], task="t")

        assert result["model"] == "gemini-2.5-flash-001"
        assert result["usage"] == {"input_tokens": 120, "output_tokens": 40}
        event = _events(capture)[-1].__dict__
        assert event["gen_ai.operation.name"] == "chat"
        assert event["gen_ai.provider.name"] == "gcp.gemini"
        assert event["gen_ai.request.model"] == "gemini-2.5-flash"
        assert event["gen_ai.response.model"] == "gemini-2.5-flash-001"
        assert event["gen_ai.usage.input_tokens"] == 120
        assert event["gen_ai.usage.output_tokens"] == 40
        assert event["zedek.task"] == "t"
        assert event["zedek.duration_ms"] >= 0

    def test_record_contains_no_prompt_or_answer_text(self, monkeypatch, capture):
        reply = lp.ProviderReply("SECRET-ANSWER", "m", "m", 1, 1)
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"groq": self._provider(reply)})
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["groq", "local"]})
        lp.generate_chat([{"role": "user", "content": "SECRET-PROMPT"}], task="t")
        dumped = str(_events(capture)[-1].__dict__)
        assert "SECRET-PROMPT" not in dumped and "SECRET-ANSWER" not in dumped

    def test_local_fallback_is_recorded_as_ollama(self, monkeypatch, capture):
        monkeypatch.setattr(lp, "_local",
                            lambda messages, json_mode: lp.ProviderReply("x", "llama3.1:8b", "llama3.1:8b", 5, 2))
        result = lp.generate_chat([{"role": "user", "content": "q"}], force_local=True)
        assert result["source"] == "local"
        assert _events(capture)[-1].__dict__["gen_ai.provider.name"] == "ollama"


# ── 3.1 token totals ────────────────────────────────────────────────────────

class TestTokenTotals:
    def test_totals_summed_and_unknown_adds_nothing(self, monkeypatch):
        replies = iter([lp.ProviderReply("a", "m", "m", 100, 20),
                        lp.ProviderReply("b", "m", "m", 50, 10),
                        lp.ProviderReply("c", "m", "m", None, None)])
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"groq": lambda messages, json_mode: next(replies)})
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["groq", "local"]})
        for _ in range(3):
            lp.generate_chat([{"role": "user", "content": "q"}], task="t")
        stats = lp.provider_stats()["groq"]
        assert (stats["input_tokens"], stats["output_tokens"]) == (150, 30)
        assert stats["successes"] == 3
