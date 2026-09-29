"""Provider model resolution (OpenSpec change: fix-provider-model-resolution, ROADMAP C3).

Catalog fetches and HTTP calls are stubbed; conftest blocks real provider hosts.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

import llm_provider as lp

MSG = [{"role": "user", "content": "q"}]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    lp._reset_health_for_tests()
    lp._model_cache.clear()
    for var in ("GROQ_MODEL", "GEMINI_MODEL", "NVIDIA_MODEL", "OPENROUTER_MODEL", "CEREBRAS_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)
    yield
    lp._reset_health_for_tests()
    lp._model_cache.clear()


def _catalog(monkeypatch, cache_key, models):
    lp._model_cache[cache_key] = (lp.time.time(), list(models))


# ── 1.1 choosing ────────────────────────────────────────────────────────────

class TestChooseModel:
    def test_first_live_candidate_wins(self):
        assert lp._choose_model("groq", ["a", "b", "c"], ["x", "c", "b"]) == "b"

    def test_rotted_catalog_chooses_nothing(self):
        with patch.object(lp.log, "warning") as warn:
            assert lp._choose_model("groq", ["a", "b"], ["whisper-large-v3", "guard-86m"]) is None
        assert warn.call_args.args[0] == "model_candidates_stale"

    def test_unreachable_catalog_tries_candidates(self):
        assert lp._choose_model("groq", ["a", "b"], []) == "a"

    def test_dead_model_skipped(self):
        lp._DEAD_MODELS.add(("groq", "a"))
        assert lp._choose_model("groq", ["a", "b"], ["a", "b"]) == "b"
        assert lp._choose_model("groq", ["a", "b"], []) == "b"

    def test_dead_is_per_provider(self):
        lp._DEAD_MODELS.add(("nvidia_nim", "openai/gpt-oss-20b"))
        assert lp._choose_model("groq", ["openai/gpt-oss-20b"], ["openai/gpt-oss-20b"]) == "openai/gpt-oss-20b"

    def test_resolver_raises_not_configured(self, monkeypatch):
        _catalog(monkeypatch, "groq", ["whisper-large-v3"])
        with pytest.raises(RuntimeError) as err:
            lp.resolve_groq_model()
        assert lp._is_not_configured(err.value)

    def test_env_override_until_dead(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "my-model")
        _catalog(monkeypatch, "groq", lp.GROQ_CANDIDATES)
        assert lp._model_for("groq", "GROQ_MODEL", lp.resolve_groq_model) == "my-model"
        lp._DEAD_MODELS.add(("groq", "my-model"))
        assert lp._model_for("groq", "GROQ_MODEL", lp.resolve_groq_model) == lp.GROQ_CANDIDATES[0]

    def test_rotted_provider_skipped_and_not_counted(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        _catalog(monkeypatch, "groq", ["whisper-large-v3", "meta-llama/llama-prompt-guard-2-86m"])
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["groq", "local"]})
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"groq": lp._groq})
        monkeypatch.setattr(lp, "_local", lambda messages, json_mode: lp.ProviderReply("local answer"))
        with patch.object(lp.requests, "post") as post:
            result = lp.generate_chat(MSG, task="t")
        post.assert_not_called()
        assert result["source"] == "local"
        assert lp.provider_stats()["groq"]["calls"] == 0
        assert lp._load_usage().get("groq", 0) == 0

    def test_candidate_lists_have_no_known_non_chat_models(self):
        for pool in (lp.GEMINI_CANDIDATES, lp.GROQ_CANDIDATES, lp.NVIDIA_CODING_CANDIDATES,
                     lp.OPENROUTER_CODING_CANDIDATES, lp.CEREBRAS_CANDIDATES):
            assert pool
            for model in pool:
                assert not any(k in model for k in ("whisper", "guard", "safety", "embed", "orpheus"))


# ── 1.2 404 retirement ──────────────────────────────────────────────────────

def _ok(text="hi", model="m"):
    response = MagicMock()
    response.json.return_value = {"model": model, "choices": [{"message": {"content": text}}]}
    response.raise_for_status.return_value = None
    return response


def _status(code):
    response = MagicMock()
    response.status_code = code
    failing = MagicMock()
    failing.raise_for_status.side_effect = requests.exceptions.HTTPError(str(code), response=response)
    return failing


class TestRetirement:
    @pytest.fixture
    def groq(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "k")
        _catalog(monkeypatch, "groq", lp.GROQ_CANDIDATES)

    def test_404_retires_and_retries_next(self, groq):
        first, second = lp.GROQ_CANDIDATES[:2]
        with patch.object(lp.requests, "post", side_effect=[_status(404), _ok(model=second)]) as post:
            reply = lp._groq(MSG, False)
        assert [c.kwargs["json"]["model"] for c in post.call_args_list] == [first, second]
        assert reply.text == "hi" and ("groq", first) in lp._DEAD_MODELS
        with patch.object(lp.requests, "post", return_value=_ok()) as post:
            lp._groq(MSG, False)
        assert post.call_args.kwargs["json"]["model"] == second

    def test_non_404_not_retried(self, groq):
        with patch.object(lp.requests, "post", return_value=_status(500)) as post:
            with pytest.raises(requests.exceptions.HTTPError):
                lp._groq(MSG, False)
        assert post.call_count == 1 and not lp._DEAD_MODELS

    def test_only_one_retry(self, groq):
        with patch.object(lp.requests, "post", side_effect=[_status(404), _status(404)]) as post:
            with pytest.raises(requests.exceptions.HTTPError):
                lp._groq(MSG, False)
        assert post.call_count == 2 and len(lp._DEAD_MODELS) == 2

    def test_all_dead_becomes_not_configured(self, groq):
        for model in lp.GROQ_CANDIDATES:
            lp._DEAD_MODELS.add(("groq", model))
        with pytest.raises(RuntimeError) as err:
            lp._groq(MSG, False)
        assert lp._is_not_configured(err.value)

    def test_gemini_404_retires(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "k")
        _catalog(monkeypatch, "gemini", lp.GEMINI_CANDIDATES)
        ok = MagicMock()
        ok.json.return_value = {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}
        ok.raise_for_status.return_value = None
        with patch.object(lp.requests, "post", side_effect=[_status(404), ok]) as post:
            assert lp._gemini(MSG, False).text == "hi"
        urls = [c.args[0] for c in post.call_args_list]
        assert lp.GEMINI_CANDIDATES[0] in urls[0] and lp.GEMINI_CANDIDATES[1] in urls[1]

    @pytest.mark.parametrize("func,key,cache,pool", [
        ("_nvidia_nim", "NVIDIA_API_KEY", "nvidia", "NVIDIA_CODING_CANDIDATES"),
        ("_openrouter", "OPENROUTER_API_KEY", "openrouter", "OPENROUTER_CODING_CANDIDATES"),
        ("_cerebras", "CEREBRAS_API_KEY", "cerebras", "CEREBRAS_CANDIDATES"),
    ])
    def test_every_cloud_provider_retires(self, monkeypatch, func, key, cache, pool):
        monkeypatch.setenv(key, "k")
        candidates = getattr(lp, pool)
        _catalog(monkeypatch, cache, candidates)
        with patch.object(lp.requests, "post", side_effect=[_status(404), _ok()]) as post:
            getattr(lp, func)(MSG, False)
        assert [c.kwargs["json"]["model"] for c in post.call_args_list] == candidates[:2]


class TestEmbeddedErrors:
    def _body(self, payload):
        response = MagicMock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_200_with_embedded_429_cools_down(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("OPENROUTER_MODEL", "m:free")
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["openrouter", "local"]})
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"openrouter": lp._openrouter})
        monkeypatch.setattr(lp, "_local", lambda messages, json_mode: lp.ProviderReply("local"))
        body = {"error": {"code": 429, "message": "Provider returned error"}}
        with patch.object(lp.requests, "post", return_value=self._body(body)):
            assert lp.generate_chat(MSG, task="t")["source"] == "local"
        stats = lp.provider_stats()["openrouter"]
        assert stats["rate_limited"] == 1 and stats["cooldown_remaining_s"] > 0

    def test_200_with_embedded_404_retires_model(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        _catalog(monkeypatch, "openrouter", lp.OPENROUTER_CODING_CANDIDATES)
        with patch.object(lp.requests, "post", side_effect=[self._body({"error": {"code": 404}}), _ok()]):
            lp._openrouter(MSG, False)
        assert ("openrouter", lp.OPENROUTER_CODING_CANDIDATES[0]) in lp._DEAD_MODELS

    @pytest.mark.parametrize("payload", [{}, {"choices": []}, {"error": "text"}, {"error": {"code": "429"}}])
    def test_malformed_bodies_raise_http_error(self, monkeypatch, payload):
        with patch.object(lp.requests, "post", return_value=self._body(payload)):
            with pytest.raises(requests.exceptions.HTTPError):
                lp._openai_compatible("groq", "https://x", "k", "m", MSG, False)


# ── 1.3 payment required ────────────────────────────────────────────────────

def test_402_gets_auth_cooldown():
    assert lp._cooldown_for(402, {}) == lp.AUTH_FAILURE_COOLDOWN
