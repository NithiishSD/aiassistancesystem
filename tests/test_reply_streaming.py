"""Reply streaming (OpenSpec change: stream-chat-replies, ROADMAP D2).

Providers and HTTP are stubbed, so these tests make no network calls.
"""

import io
import json
from unittest.mock import MagicMock

import pytest
import requests

import llm_provider as lp
import orchestrator


class RecordingSink:
    def __init__(self, fail=False):
        self.events: list[tuple[str, str]] = []
        self.fail = fail

    def delta(self, text):
        if self.fail:
            raise OSError("terminal gone")
        self.events.append(("delta", text))

    def restart(self):
        if self.fail:
            raise OSError("terminal gone")
        self.events.append(("restart", ""))

    @property
    def text(self):
        return "".join(t for kind, t in self.events if kind == "delta")


def _filter(chunks):
    f = lp._ThinkFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


# ── 1.1 _ThinkFilter and _StreamRelay ─────────────────────────────────────────

class TestThinkFilter:
    def test_tag_split_across_chunks(self):
        assert _filter(["<thi", "nk>plan</th", "ink>Answer"]) == "Answer"

    def test_case_insensitive(self):
        assert _filter(["<THINK>x</Think>ok"]) == "ok"

    def test_stray_closing_tag_dropped(self):
        assert _filter(["leftover</think>Answer"]) == "leftoverAnswer"

    def test_unfinished_partial_tag_is_text(self):
        assert _filter(["a < b and x <th"]) == "a < b and x <th"

    def test_unclosed_think_block_hidden(self):
        assert _filter(["Hi <think>never closed"]) == "Hi "

    def test_matches_strip_thinking_tags(self):
        text = "<think>step 1\nstep 2</think>\nThe answer is 42."
        chunks = [text[i:i + 3] for i in range(0, len(text), 3)]
        assert _filter(chunks).strip() == lp.strip_thinking_tags(text)


class TestStreamRelay:
    def test_leading_whitespace_trimmed(self):
        sink = RecordingSink()
        relay = lp._StreamRelay(sink)
        relay.begin_attempt()
        for chunk in ["<think>x</think>", "\n\n", "  Hello", " world"]:
            relay.feed(chunk)
        relay.finish()
        assert sink.text == "Hello world"

    def test_restart_only_after_emission(self):
        sink = RecordingSink()
        relay = lp._StreamRelay(sink)
        relay.begin_attempt()
        relay.feed("<think>only reasoning")  # nothing visible
        relay.begin_attempt()
        assert ("restart", "") not in sink.events
        relay.feed("partial")
        relay.begin_attempt()
        relay.feed("full")
        relay.finish()
        assert sink.events == [("delta", "partial"), ("restart", ""), ("delta", "full")]

    def test_filter_reset_between_attempts(self):
        sink = RecordingSink()
        relay = lp._StreamRelay(sink)
        relay.begin_attempt()
        relay.feed("<think>unfinished")
        relay.begin_attempt()
        relay.feed("Answer")
        relay.finish()
        assert sink.text == "Answer"

    def test_sink_exception_swallowed(self):
        relay = lp._StreamRelay(RecordingSink(fail=True))
        relay.begin_attempt()
        relay.feed("a")
        relay.feed("b")  # sink already dropped, no second attempt
        relay.finish()
        assert relay.emitted and relay.first_token_at is not None


# ── 1.2 Streaming transport ───────────────────────────────────────────────────

class FakeSSE:
    def __init__(self, events, status=200, raw_lines=None):
        self.status_code = status
        self.closed = False
        lines = raw_lines if raw_lines is not None else []
        for event in events:
            lines += [f"data: {json.dumps(event) if not isinstance(event, str) else event}", ""]
        self._lines = lines

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def close(self):
        self.closed = True


def _oa_chunk(text=None, **extra):
    choices = [{"delta": {"content": text}}] if text is not None else []
    return {"model": "served-model", "choices": choices, **extra}


def _post_returning(monkeypatch, response):
    calls = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response
    monkeypatch.setattr(lp.requests, "post", fake_post)
    return calls


def _openai(on_delta, json_mode=False, schema=None):
    return lp._openai_compatible("groq", "https://x/v1/chat", "key", "m",
                                 [{"role": "user", "content": "hi"}], json_mode,
                                 schema=schema, on_delta=on_delta)


class TestOpenAICompatibleStream:
    def test_chunks_in_order_and_done_stops(self, monkeypatch):
        response = FakeSSE([_oa_chunk("Hel"), _oa_chunk("lo"), "[DONE]", _oa_chunk("ignored")],
                           raw_lines=[": keep-alive", ""])
        calls = _post_returning(monkeypatch, response)
        seen = []
        reply = _openai(seen.append)
        assert seen == ["Hel", "lo"]
        assert reply.text == "Hello" and reply.response_model == "served-model"
        assert calls[0]["json"]["stream"] is True and calls[0]["stream"] is True
        assert response.closed

    def test_usage_from_final_chunk(self, monkeypatch):
        _post_returning(monkeypatch, FakeSSE([
            _oa_chunk("a"), _oa_chunk(usage={"prompt_tokens": 7, "completion_tokens": 3})]))
        reply = _openai(lambda _: None)
        assert (reply.input_tokens, reply.output_tokens) == (7, 3)

    def test_groq_x_groq_usage(self, monkeypatch):
        _post_returning(monkeypatch, FakeSSE([
            _oa_chunk("a"), _oa_chunk(x_groq={"usage": {"prompt_tokens": 11, "completion_tokens": 2}})]))
        reply = _openai(lambda _: None)
        assert (reply.input_tokens, reply.output_tokens) == (11, 2)

    def test_in_stream_error_is_http_error(self, monkeypatch):
        _post_returning(monkeypatch, FakeSSE([_oa_chunk("a"), {"error": {"code": 429, "message": "busy"}}]))
        with pytest.raises(requests.exceptions.HTTPError) as caught:
            _openai(lambda _: None)
        assert caught.value.response.status_code == 429

    def test_http_status_raised_before_reading(self, monkeypatch):
        _post_returning(monkeypatch, FakeSSE([_oa_chunk("never")], status=503))
        seen = []
        with pytest.raises(requests.exceptions.HTTPError):
            _openai(seen.append)
        assert seen == []

    @pytest.mark.parametrize("kwargs", [{"json_mode": True}, {"schema": {"type": "object"}}])
    def test_json_and_schema_calls_never_stream(self, monkeypatch, kwargs):
        seen = []
        posted = {}

        def fake_post(source, endpoint, api_key, model, messages, extra, extra_headers):
            posted["extra"] = extra
            return lp.ProviderReply(text="{}", request_model=model, response_model=model)
        monkeypatch.setattr(lp, "_post_openai_compatible", fake_post)
        monkeypatch.setattr(lp.requests, "post", MagicMock(side_effect=AssertionError("streamed")))
        lp._openai_compatible("groq", "u", "k", "m", [], kwargs.get("json_mode", False),
                              schema=kwargs.get("schema"), schema_style="hint", on_delta=seen.append)
        assert seen == [] and "stream" not in posted["extra"]


class TestGeminiStream:
    def test_thought_parts_skipped_and_usage(self, monkeypatch):
        events = [
            {"candidates": [{"content": {"parts": [{"text": "musing", "thought": True}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "Hi "}]}}]},
            {"candidates": [{"content": {"parts": [{"text": "there"}]}}],
             "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
             "modelVersion": "gemini-x"},
        ]
        response = FakeSSE(events)
        calls = _post_returning(monkeypatch, response)
        seen = []
        reply = lp._gemini_request("key", "gemini-x", [], {}, seen.append)
        assert seen == ["Hi ", "there"] and reply.text == "Hi there"
        assert (reply.input_tokens, reply.output_tokens) == (5, 2)
        assert calls[0]["url"].endswith(":streamGenerateContent")
        assert calls[0]["params"]["alt"] == "sse" and response.closed

    def test_json_mode_does_not_stream(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        monkeypatch.setattr(lp, "_model_for", lambda *a: "gemini-x")
        captured = {}

        def fake_request(api_key, model_name, contents, config, on_delta=None):
            captured["on_delta"] = on_delta
            return lp.ProviderReply(text="{}", request_model=model_name, response_model=model_name)
        monkeypatch.setattr(lp, "_gemini_request", fake_request)
        lp._gemini([{"role": "user", "content": "hi"}], True, on_delta=lambda _: None)
        assert captured["on_delta"] is None


class TestLocalStream:
    def test_ollama_stream(self, monkeypatch):
        chunks = [{"message": {"content": "lo"}}, {"message": {"content": "cal"}},
                  {"message": {"content": ""}, "model": "llama", "prompt_eval_count": 9, "eval_count": 4}]
        chat = MagicMock(return_value=iter(chunks))
        monkeypatch.setattr(lp.ollama, "chat", chat)
        seen = []
        reply = lp._local([{"role": "user", "content": "hi"}], False, on_delta=seen.append)
        assert seen == ["lo", "cal"] and reply.text == "local"
        assert (reply.input_tokens, reply.output_tokens) == (9, 4)
        assert chat.call_args.kwargs["stream"] is True


# ── 1.3 Chain semantics ───────────────────────────────────────────────────────

MSG = [{"role": "user", "content": "hi"}]


@pytest.fixture
def chain(monkeypatch, tmp_path):
    lp._reset_health_for_tests()
    monkeypatch.setenv("ZEDEK_PROVIDER_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)

    def install(funcs, order=("gemini", "groq")):
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", funcs)
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": list(order)})
    yield install
    lp._reset_health_for_tests()


def _streaming(chunks, fail_after=None, error=None):
    def fn(messages, json_mode, on_delta=None):
        for i, chunk in enumerate(chunks):
            if fail_after is not None and i == fail_after:
                raise error or requests.exceptions.ConnectionError("dropped")
            if on_delta:
                on_delta(chunk)
        if fail_after is not None and fail_after >= len(chunks):
            raise error or requests.exceptions.ConnectionError("dropped")
        return lp.ProviderReply(text="".join(chunks), request_model="m", response_model="m",
                                input_tokens=3, output_tokens=len(chunks))
    return fn


class TestChain:
    def test_streamed_result_matches_unstreamed(self, chain):
        chain({"gemini": _streaming(["<think>p</think>", "Hel", "lo"])})
        plain = lp.generate_chat(MSG, task="t")
        usage_after_plain = lp._load_usage()
        sink = RecordingSink()
        streamed = lp.generate_chat(MSG, task="t", stream=sink)
        assert streamed == plain
        assert sink.text == streamed["answer"] == "Hello"
        assert lp._load_usage() == {k: v * 2 for k, v in usage_after_plain.items()}

    def test_failure_before_first_token_no_restart(self, chain):
        chain({"gemini": _streaming(["x"], fail_after=0), "groq": _streaming(["Groq ", "answer"])})
        sink = RecordingSink()
        result = lp.generate_chat(MSG, task="t", stream=sink)
        assert result["source"] == "groq" and result["answer"] == "Groq answer"
        assert sink.events == [("delta", "Groq "), ("delta", "answer")]

    def test_mid_stream_failure_restarts_once(self, chain):
        chain({"gemini": _streaming(["Gem", "ini", "never"], fail_after=2),
               "groq": _streaming(["Groq ", "answer"])})
        sink = RecordingSink()
        result = lp.generate_chat(MSG, task="t", stream=sink)
        assert sink.events == [("delta", "Gem"), ("delta", "ini"), ("restart", ""),
                               ("delta", "Groq "), ("delta", "answer")]
        assert result["source"] == "groq" and result["answer"] == "Groq answer"
        assert lp.provider_stats()["gemini"]["failures"] >= 1

    def test_mid_stream_failure_to_local(self, chain, monkeypatch):
        chain({"gemini": _streaming(["partial"], fail_after=1)}, order=("gemini", "local"))
        monkeypatch.setattr(lp, "_local", _streaming(["local ", "answer"]))
        sink = RecordingSink()
        result = lp.generate_chat(MSG, task="t", stream=sink)
        assert result["source"] == "local" and result["answer"] == "local answer"
        assert sink.events == [("delta", "partial"), ("restart", ""),
                               ("delta", "local "), ("delta", "answer")]

    def test_broken_sink_does_not_fail_or_fall_back(self, chain):
        groq = MagicMock(side_effect=AssertionError("fell back"))
        chain({"gemini": _streaming(["Full ", "answer"]), "groq": groq})
        result = lp.generate_chat(MSG, task="t", stream=RecordingSink(fail=True))
        assert result["source"] == "gemini" and result["answer"] == "Full answer"
        groq.assert_not_called()

    def test_json_mode_with_sink_does_not_stream(self, chain):
        seen = {}

        def provider(messages, json_mode, **kwargs):
            seen.update(kwargs)
            return lp.ProviderReply(text="{}", request_model="m", response_model="m")
        chain({"gemini": provider})
        sink = RecordingSink()
        lp.generate_chat(MSG, json_mode=True, task="t", stream=sink)
        assert "on_delta" not in seen and sink.events == []

    def test_first_token_logged(self, chain, monkeypatch):
        chain({"gemini": _streaming(["hi"])})
        logged = []
        real_info = lp.log.info
        monkeypatch.setattr(lp.log, "info", lambda msg, *a, **k: (logged.append((msg, k.get("extra"))),
                                                                    real_info(msg, *a, **k)))
        lp.generate_chat(MSG, task="t", stream=RecordingSink())
        entries = [extra for msg, extra in logged if msg == "stream_first_token"]
        assert len(entries) == 1 and entries[0]["source"] == "gemini" and entries[0]["ms"] >= 0


# ── 1.4 Orchestrator and REPL ─────────────────────────────────────────────────

@pytest.fixture
def qa(monkeypatch):
    calls = []

    def fake_generate_chat(messages, **kwargs):
        calls.append(kwargs)
        sink = kwargs.get("stream")
        if sink is not None:
            sink.delta("streamed answer")
        return {"answer": "streamed answer", "source": "stub"}
    monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake_generate_chat)
    monkeypatch.setattr(orchestrator.memory, "retrieve_relevant", lambda *a, **k: [])
    return calls


class TestOrchestrator:
    def test_general_qa_passes_the_sink(self, qa, monkeypatch):
        monkeypatch.setattr(orchestrator, "_handle_turn",
                            lambda text: orchestrator.answer_general_question(text, "personal"))
        sink = RecordingSink()
        assert orchestrator.handle("what is a heap", stream=sink) == "streamed answer"
        assert qa[0]["stream"] is sink and sink.text == "streamed answer"
        assert orchestrator._REPLY_STREAM.get() is None  # reset after the turn

    def test_no_sink_passes_no_stream_keyword(self, qa):
        orchestrator.answer_general_question("what is a heap", "personal")
        assert "stream" not in qa[0]

    def test_decomposed_request_does_not_stream(self, qa, monkeypatch):
        monkeypatch.setattr(orchestrator.task_planner, "should_decompose", lambda text: True)
        monkeypatch.setattr(orchestrator.task_planner, "decompose",
                            lambda text: [{"description": "part one"}, {"description": "part two"}])
        monkeypatch.setattr(orchestrator, "should_ask_ambiguous_term_question", lambda *a: False)
        monkeypatch.setattr(orchestrator, "should_treat_as_disambiguation", lambda *a: False)
        monkeypatch.setattr(orchestrator, "_handle_single",
                            lambda text: orchestrator.answer_general_question(text, "personal"))
        sink = RecordingSink()
        answer = orchestrator.handle("part one and part two", stream=sink)
        assert sink.events == [] and all("stream" not in call for call in qa)
        assert answer.startswith("I broke this into steps:")


class TestTerminal:
    def test_terminal_stream_output_and_restart(self):
        out = io.StringIO()
        sink = orchestrator._TerminalStream(out)
        sink.delta("Hel")
        sink.delta("lo")
        sink.restart()
        sink.delta("Again")
        text = out.getvalue()
        assert text.startswith("Zedek: Hello\n(connection dropped")
        assert text.endswith("Zedek: Again") and sink.shown == "Again"

    def test_repl_does_not_reprint_streamed_answer(self, monkeypatch):
        def fake_handle(text, stream=None):
            stream.delta("The answer.")
            return "The  answer.\n"
        monkeypatch.setattr(orchestrator, "handle", fake_handle)
        out = io.StringIO()
        orchestrator._repl_turn("q", out)
        assert out.getvalue() == "(thinking…)\nZedek: The answer.\n\n"

    def test_repl_prints_when_nothing_streamed(self, monkeypatch):
        monkeypatch.setattr(orchestrator, "handle", lambda text, stream=None: "Disk is 40% full.")
        out = io.StringIO()
        orchestrator._repl_turn("q", out)
        assert out.getvalue() == "(thinking…)\nZedek: Disk is 40% full.\n\n"

    def test_repl_reprints_when_answer_differs(self, monkeypatch):
        def fake_handle(text, stream=None):
            stream.delta("draft")
            return "final version"
        monkeypatch.setattr(orchestrator, "handle", fake_handle)
        out = io.StringIO()
        orchestrator._repl_turn("q", out)
        assert out.getvalue().endswith("(final answer)\nZedek: final version\n\n")
