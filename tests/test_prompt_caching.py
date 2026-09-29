"""Static-first prompts and the exact-match LLM cache (OpenSpec change: static-first-prompts).

No network: provider functions are stubbed, and tests/conftest.py fails any test
that reaches a real model. conftest turns the cache off by default; the `cache`
fixture turns it on against a per-test file.
"""

import sqlite3
from unittest.mock import patch

import pytest

import llm_cache
import llm_provider as lp
import llm_schemas
import orchestrator

MSG = [{"role": "system", "content": "fixed"}, {"role": "user", "content": "Statement: i study at XYZ"}]
VALID = '{"facts": [{"attribute": "college", "value": "XYZ"}]}'


@pytest.fixture
def cache(monkeypatch, tmp_path):
    path = tmp_path / "llm_cache.sqlite3"
    monkeypatch.setenv("LLM_CACHE", "on")
    monkeypatch.setenv("ZEDEK_LLM_CACHE_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    lp._reset_health_for_tests()
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)
    yield
    lp._reset_health_for_tests()


def _counting(text=VALID):
    calls = []

    def fn(messages, json_mode, schema=None):
        calls.append(messages)
        return lp.ProviderReply(text=text, request_model="m", response_model="m")
    fn.calls = calls
    return fn


def _chain(monkeypatch, cloud=None, local=None):
    funcs = {"gemini": cloud} if cloud else {}
    monkeypatch.setattr(lp, "_PROVIDER_FUNCS", funcs)
    monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": [*funcs, "local"]})
    monkeypatch.setattr(lp, "_local", local or _counting("{}"))


# ── 1.1 llm_cache ───────────────────────────────────────────────────────────

class TestCacheStore:
    def test_round_trip(self, cache):
        k = llm_cache.key("t", llm_schemas.FactList, MSG)
        assert llm_cache.get(k) is None
        llm_cache.put(k, VALID, "gemini", "m")
        assert llm_cache.get(k) == {"data": VALID, "source": "gemini", "model": "m"}

    @pytest.mark.parametrize("change", ["task", "schema", "messages"])
    def test_key_is_exact(self, change):
        base = llm_cache.key("t", llm_schemas.FactList, MSG)
        other = {
            "task": lambda: llm_cache.key("u", llm_schemas.FactList, MSG),
            "schema": lambda: llm_cache.key("t", llm_schemas.AcademicIntent, MSG),
            "messages": lambda: llm_cache.key("t", llm_schemas.FactList,
                                              [MSG[0], {"role": "user", "content": "Statement: i study at XYZ "}]),
        }[change]()
        assert other != base

    def test_expired_entry_is_a_miss_and_removed(self, cache, monkeypatch):
        k = llm_cache.key("t", llm_schemas.FactList, MSG)
        llm_cache.put(k, VALID, "gemini", "m")
        real_time = llm_cache.time.time
        monkeypatch.setattr(llm_cache.time, "time", lambda: real_time() + llm_cache.TTL_SECONDS + 1)
        assert llm_cache.get(k) is None
        with sqlite3.connect(cache) as conn:
            assert conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 0

    @pytest.mark.parametrize("value", ["off", "false", "0", "no", "OFF"])
    def test_disabled_by_env(self, monkeypatch, value):
        monkeypatch.setenv("LLM_CACHE", value)
        assert not llm_cache.enabled()

    def test_corrupt_file_is_a_miss(self, cache):
        cache.write_bytes(b"this is not a sqlite database" * 100)
        k = llm_cache.key("t", llm_schemas.FactList, MSG)
        assert llm_cache.get(k) is None
        llm_cache.put(k, VALID, "gemini", "m")  # must not raise


# ── 1.2 generate_structured(cache=True) ─────────────────────────────────────

class TestStructuredCache:
    def test_second_identical_call_skips_providers(self, cache, monkeypatch):
        gemini = _counting()
        _chain(monkeypatch, gemini)
        first = lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)
        second = lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)
        assert first["source"] == "gemini" and len(gemini.calls) == 1
        assert second["source"] == "cache" and second["data"] == first["data"]
        assert second["usage"] == {"input_tokens": 0, "output_tokens": 0}

    def test_off_by_default(self, cache, monkeypatch):
        gemini = _counting()
        _chain(monkeypatch, gemini)
        for _ in range(2):
            lp.generate_structured(MSG, llm_schemas.FactList, task="t")
        assert len(gemini.calls) == 2

    def test_disabled_env_bypasses(self, cache, monkeypatch):
        monkeypatch.setenv("LLM_CACHE", "off")
        gemini = _counting()
        _chain(monkeypatch, gemini)
        for _ in range(2):
            lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)
        assert len(gemini.calls) == 2

    def test_local_result_is_not_stored(self, cache, monkeypatch):
        local = _counting()
        _chain(monkeypatch, local=local)
        for _ in range(2):
            assert lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)["source"] == "local"
        assert len(local.calls) == 2

    def test_failed_extraction_is_not_stored(self, cache, monkeypatch):
        _chain(monkeypatch, _counting("{}"))
        with pytest.raises(lp.StructuredOutputError):
            lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)
        assert llm_cache.get(llm_cache.key("t", llm_schemas.FactList, MSG)) is None

    def test_stale_shape_is_a_miss(self, cache, monkeypatch):
        llm_cache.put(llm_cache.key("t", llm_schemas.FactList, MSG), '{"old": "shape"}', "gemini", "m")
        gemini = _counting()
        _chain(monkeypatch, gemini)
        result = lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)
        assert result["source"] == "gemini" and len(gemini.calls) == 1

    def test_corrupt_cache_still_answers(self, cache, monkeypatch):
        cache.write_bytes(b"garbage" * 500)
        _chain(monkeypatch, _counting())
        assert lp.generate_structured(MSG, llm_schemas.FactList, task="t", cache=True)["source"] == "gemini"


# ── 1.3 static-first orchestrator prompts ───────────────────────────────────

def _capture_structured(data):
    calls = []

    def fake(messages, response_model, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return {"answer": "", "source": "stub", "data": data}
    return fake, calls


def _capture_chat(answer="ok"):
    calls = []

    def fake(messages, **kwargs):
        calls.append({"messages": messages, **kwargs})
        return {"answer": answer, "source": "stub"}
    return fake, calls


def _assert_static_first(calls, inputs):
    systems = {c["messages"][0]["content"] for c in calls}
    assert len(systems) == 1, "system message must be byte-identical across inputs"
    assert calls[0]["messages"][0]["role"] == "system"
    for call, text in zip(calls, inputs):
        assert text not in call["messages"][0]["content"]
        assert text in call["messages"][-1]["content"]


class TestStaticFirst:
    INPUTS = ["i study at Northwind Institute", "my favourite editor is helix"]

    def test_canonicalize(self, monkeypatch):
        fake, calls = _capture_structured(llm_schemas.FactList(facts=[]))
        monkeypatch.setattr(orchestrator.llm_provider, "generate_structured", fake)
        for text in self.INPUTS:
            orchestrator.canonicalize_fact(text)
        _assert_static_first(calls, self.INPUTS)
        assert all(c["cache"] is True for c in calls)

    def test_academic_intent(self, monkeypatch):
        fake, calls = _capture_structured(llm_schemas.AcademicIntent(
            action="review", topic="", result="", problem="", difficulty="", minutes=0))
        monkeypatch.setattr(orchestrator.llm_provider, "generate_structured", fake)
        inputs = ["solved two graph problems", "what should I practice next"]
        for text in inputs:
            orchestrator._extract_academic_intent(text)
        _assert_static_first(calls, inputs)
        assert all(c["cache"] is True for c in calls)

    def test_correction_is_not_cached(self, monkeypatch):
        fake, calls = _capture_structured(llm_schemas.FactCorrection(index=None, corrected_fact=None))
        monkeypatch.setattr(orchestrator.llm_provider, "generate_structured", fake)
        monkeypatch.setattr(orchestrator, "LAST_ROUTING_DECISION", None)
        inputs = ["no, my college is Northwind", "actually I use vim"]
        with patch("orchestrator.memory.retrieve", return_value=[{"id": "1", "text": "User's college: A"}]):
            for text in inputs:
                orchestrator._handle_correction(text, "personal")
        _assert_static_first(calls, inputs)
        assert not any(c.get("cache") for c in calls)

    def test_acknowledgement(self, monkeypatch):
        fake, calls = _capture_chat()
        monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake)
        for text in self.INPUTS:
            orchestrator._acknowledge_fact(text)
        _assert_static_first(calls, self.INPUTS)

    def test_process_reasoning(self, monkeypatch):
        fake, calls = _capture_chat()
        monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake)
        inputs = ["which process uses most memory", "is firefox running"]
        for text in inputs:
            orchestrator._reason_over_process_data([{"name": "firefox", "rss_mb": 900}], text)
        _assert_static_first(calls, inputs)

    def test_general_qa_order(self, monkeypatch):
        fake, calls = _capture_chat()
        monkeypatch.setattr(orchestrator.llm_provider, "generate_chat", fake)
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "Hello! Need anything?"}]
        monkeypatch.setattr(orchestrator, "SESSION_HISTORY", list(history))
        facts = [{"text": "User's college: Northwind Institute", "score": 0.1}]
        inputs = ["what college do I study at", "what is a heap"]
        with patch("orchestrator.memory.retrieve_relevant", return_value=facts):
            for text in inputs:
                orchestrator.answer_general_question(text, "personal")
        _assert_static_first(calls, inputs)
        messages = calls[0]["messages"]
        assert messages[1:-1] == history
        last = messages[-1]
        assert last["role"] == "user"
        assert "User's college: Northwind Institute" in last["content"]
        assert "Need anything?" in last["content"]  # turn note travels with the message
        assert "Northwind" not in messages[0]["content"]
