"""Quota-aware provider chain (OpenSpec change: add-provider-quota-awareness).

Provider functions are stubbed, so these tests make no network calls. The
usage file is redirected by tests/conftest.py; tests that inspect it point it
at their own tmp_path.
"""

import json
import os
from unittest.mock import MagicMock

import pytest
import requests

import llm_provider as lp


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    lp._reset_health_for_tests()
    monkeypatch.setenv("ZEDEK_PROVIDER_USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(lp, "_now", lambda: clock["t"])
    monkeypatch.setattr(lp, "_today", lambda: "2026-09-29")
    yield clock
    lp._reset_health_for_tests()


def _http_error(status, headers=None):
    response = MagicMock()
    response.status_code = status
    response.headers = headers or {}
    return requests.exceptions.HTTPError(f"{status} error", response=response)


def _chain(monkeypatch, funcs, chain=("gemini", "groq", "local")):
    monkeypatch.setattr(lp, "_PROVIDER_FUNCS", funcs)
    monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": list(chain)})
    monkeypatch.setattr(lp, "_run_local_or_raise",
                        lambda messages, json_mode, task=None: {"answer": "local answer", "source": "local"})


def _counting(answer=None, error=None):
    calls = {"n": 0}

    def fn(messages, json_mode):
        calls["n"] += 1
        if error is not None:
            raise error
        return lp.ProviderReply(text=answer, request_model="m", response_model="m")
    fn.calls = calls
    return fn


MSG = [{"role": "user", "content": "hi"}]


# ── 1.1 isolation ───────────────────────────────────────────────────────────

def test_conftest_redirects_usage_file_away_from_user_data():
    assert os.path.realpath(lp._usage_path()) != os.path.realpath(lp._DEFAULT_USAGE_PATH)


# ── 1.2 usage persistence ──────────────────────────────────────────────────

class TestUsagePersistence:
    def test_counts_survive_restart(self, monkeypatch):
        lp._increment_usage("openrouter")
        lp._increment_usage("openrouter")
        lp._reset_health_for_tests()  # simulate a new process: only the file remains
        assert lp._load_usage() == {"openrouter": 2}

    def test_new_day_starts_from_zero(self, monkeypatch):
        lp._increment_usage("openrouter")
        monkeypatch.setattr(lp, "_today", lambda: "2026-09-30")
        assert lp._load_usage() == {}

    def test_file_keeps_only_today(self, monkeypatch):
        lp._increment_usage("gemini")
        monkeypatch.setattr(lp, "_today", lambda: "2026-09-30")
        lp._increment_usage("gemini")
        with open(lp._usage_path()) as handle:
            assert list(json.load(handle)) == ["2026-09-30"]

    def test_corrupt_file_loads_empty(self):
        with open(lp._usage_path(), "w") as handle:
            handle.write("{not json")
        assert lp._load_usage() == {}


# ── 2.1 cooldown rules ──────────────────────────────────────────────────────

class TestCooldownRules:
    @pytest.mark.parametrize("headers,expected", [
        ({"Retry-After": "30"}, 30.0),
        ({"x-ratelimit-reset-requests": "2m59.56s"}, 179.56),
        ({"x-ratelimit-reset-tokens": "450ms"}, lp.MIN_COOLDOWN),
        ({}, lp.DEFAULT_RATE_LIMIT_COOLDOWN),
        ({"Retry-After": "soon-ish"}, lp.DEFAULT_RATE_LIMIT_COOLDOWN),
        ({"Retry-After": "99999"}, lp.MAX_COOLDOWN),
    ])
    def test_429(self, headers, expected):
        assert lp._cooldown_for(429, headers) == pytest.approx(expected)

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures(self, status):
        assert lp._cooldown_for(status, {}) == lp.AUTH_FAILURE_COOLDOWN

    @pytest.mark.parametrize("status", [500, 502, None])
    def test_transient_errors_have_no_cooldown(self, status):
        assert lp._cooldown_for(status, {}) == 0.0


# ── 2.2 skipping and counting ───────────────────────────────────────────────

class TestSkipping:
    def test_rate_limited_provider_skipped_until_retry_time(self, monkeypatch, _fresh):
        gemini = _counting(error=_http_error(429, {"Retry-After": "30"}))
        groq = _counting(answer="groq answer")
        _chain(monkeypatch, {"gemini": gemini, "groq": groq})

        assert lp.generate_chat(MSG, task="t")["source"] == "groq"
        assert gemini.calls["n"] == 1

        _fresh["t"] += 29
        assert lp.generate_chat(MSG, task="t")["source"] == "groq"
        assert gemini.calls["n"] == 1  # still cooling: not contacted

        _fresh["t"] += 2
        lp.generate_chat(MSG, task="t")
        assert gemini.calls["n"] == 2  # cooldown over: tried again

    def test_auth_failure_skipped_for_an_hour(self, monkeypatch, _fresh):
        gemini = _counting(error=_http_error(401))
        _chain(monkeypatch, {"gemini": gemini, "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        _fresh["t"] += 3599
        lp.generate_chat(MSG, task="t")
        assert gemini.calls["n"] == 1

    def test_provider_at_daily_budget_not_called(self, monkeypatch):
        monkeypatch.setattr(lp, "DAILY_REQUEST_BUDGETS", {"gemini": 2})
        for _ in range(2):
            lp._increment_usage("gemini")
        gemini = _counting(answer="gemini answer")
        _chain(monkeypatch, {"gemini": gemini, "groq": _counting(answer="groq answer")})
        assert lp.generate_chat(MSG, task="t")["source"] == "groq"
        assert gemini.calls["n"] == 0

    def test_budget_counts_through_generate_chat(self, monkeypatch):
        monkeypatch.setattr(lp, "DAILY_REQUEST_BUDGETS", {"gemini": 2})
        gemini = _counting(answer="gemini answer")
        _chain(monkeypatch, {"gemini": gemini, "groq": _counting(answer="groq answer")})
        sources = [lp.generate_chat(MSG, task="t")["source"] for _ in range(3)]
        assert sources == ["gemini", "gemini", "groq"]

    def test_missing_key_is_not_counted(self, monkeypatch):
        not_configured = _counting(error=RuntimeError("gemini API key is not configured"))
        _chain(monkeypatch, {"gemini": not_configured, "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        assert lp._load_usage().get("gemini", 0) == 0
        assert lp.provider_stats()["gemini"]["calls"] == 0

    def test_failed_request_is_counted(self, monkeypatch):
        _chain(monkeypatch, {"gemini": _counting(error=_http_error(500)), "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        assert lp._load_usage()["gemini"] == 1

    def test_all_cooling_falls_back_to_local_without_contact(self, monkeypatch):
        gemini = _counting(error=_http_error(429))
        groq = _counting(error=_http_error(429))
        _chain(monkeypatch, {"gemini": gemini, "groq": groq})
        lp.generate_chat(MSG, task="t")
        result = lp.generate_chat(MSG, task="t")
        assert result["source"] == "local"
        assert gemini.calls["n"] == 1 and groq.calls["n"] == 1

    def test_skip_is_logged_with_reason(self, monkeypatch):
        _chain(monkeypatch, {"gemini": _counting(error=_http_error(429)), "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        events = []
        monkeypatch.setattr(lp.log, "info", lambda msg, extra=None: events.append((msg, extra or {})))
        lp.generate_chat(MSG, task="t")
        skipped = [e for m, e in events if m == "provider_skipped"]
        assert skipped and skipped[0]["source"] == "gemini" and skipped[0]["reason"] == "cooldown"


# ── 2.3 chain order and budgets ─────────────────────────────────────────────

class TestChainConfig:
    def test_openrouter_never_first(self):
        for task, chain in lp.TASK_PROVIDERS.items():
            assert chain[0] != "openrouter", task
        assert lp.DEFAULT_CHAIN[0] != "openrouter"

    def test_every_chain_ends_with_local(self):
        for task, chain in lp.TASK_PROVIDERS.items():
            assert chain[-1] == "local", task

    def test_openrouter_free_tier_budget(self):
        assert lp.DAILY_REQUEST_BUDGETS["openrouter"] == 50


# ── 3.1 reporting ───────────────────────────────────────────────────────────

class TestReporting:
    def test_stats_after_rate_limit_and_success(self, monkeypatch, _fresh):
        _chain(monkeypatch, {"gemini": _counting(error=_http_error(429, {"Retry-After": "30"})),
                             "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        _fresh["t"] += 10
        stats = lp.provider_stats()
        assert stats["gemini"]["rate_limited"] == 1
        assert stats["gemini"]["cooldown_remaining_s"] == pytest.approx(20.0)
        assert stats["groq"]["successes"] == 1
        assert stats["groq"]["today"] == 1

    def test_format_is_one_line_per_provider(self, monkeypatch):
        _chain(monkeypatch, {"gemini": _counting(answer="ok"), "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        text = lp.format_provider_stats()
        assert len(text.splitlines()) == 2
        assert text.startswith("gemini: today 1")


# ── Allowance headers on successful replies (OpenSpec change: provider-headroom) ──

def _ok_response(headers):
    response = MagicMock()
    response.headers = headers
    response.raise_for_status.return_value = None
    response.json.return_value = {"choices": [{"message": {"content": "ok"}}], "model": "m"}
    return response


class TestHeadroom:
    @pytest.mark.parametrize("headers, expected", [
        ({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "1m26.4s"}, 86.4),
        ({"X-RateLimit-Remaining-Tokens": "412", "X-RateLimit-Reset-Tokens": "4.364s"}, 4.364),
        ({"x-ratelimit-remaining-requests": "0"}, lp.DEFAULT_RATE_LIMIT_COOLDOWN),
        ({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "48h"}, lp.MAX_COOLDOWN),
        ({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m",
          "x-ratelimit-remaining-tokens": "10", "x-ratelimit-reset-tokens": "5s"}, 120.0),
    ])
    def test_used_up_allowance_starts_a_cooldown(self, headers, expected):
        lp._note_headroom("groq", headers)
        assert lp.provider_stats()["groq"]["cooldown_remaining_s"] == pytest.approx(expected, abs=0.1)

    @pytest.mark.parametrize("headers", [
        {}, None, {"x-ratelimit-remaining-requests": "999", "x-ratelimit-remaining-tokens": "7418"},
        {"x-ratelimit-remaining-requests": "many"}, {"x-ratelimit-remaining-tokens": str(lp.MIN_TOKEN_HEADROOM)},
    ])
    def test_room_left_or_no_headers_changes_nothing(self, headers):
        lp._note_headroom("groq", headers)
        assert lp.provider_stats()["groq"]["cooldown_remaining_s"] == 0

    def test_headroom_never_shortens_a_longer_cooldown(self, _fresh):
        lp._record_failure("groq", _http_error(401))
        lp._note_headroom("groq", {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "2s"})
        assert lp.provider_stats()["groq"]["cooldown_remaining_s"] == pytest.approx(lp.AUTH_FAILURE_COOLDOWN)

    def test_provider_is_skipped_after_a_reply_that_used_the_last_request(self, monkeypatch, _fresh):
        posts = {"n": 0}

        def fake_post(url, **kwargs):
            posts["n"] += 1
            return _ok_response({"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "30s"})

        monkeypatch.setattr(lp.requests, "post", fake_post)

        def groq(messages, json_mode):
            return lp._openai_compatible("groq", "https://example.invalid/v1", "key", "m", messages, json_mode)

        _chain(monkeypatch, {"groq": groq}, chain=("groq", "local"))
        assert lp.generate_chat(MSG, task="t")["source"] == "groq"      # answered, and reported 0 left
        assert lp.generate_chat(MSG, task="t")["source"] == "local"     # skipped without a request
        assert posts["n"] == 1
        _fresh["t"] += 31
        assert lp.generate_chat(MSG, task="t")["source"] == "groq"
        assert posts["n"] == 2


class TestLastAnswerSource:
    def test_reports_the_provider_that_answered(self, monkeypatch):
        assert lp.format_last_answer_source().startswith("No answer")
        _chain(monkeypatch, {"gemini": _counting(error=_http_error(500)), "groq": _counting(answer="ok")})
        lp.generate_chat(MSG, task="t")
        assert lp.last_answer_source()["source"] == "groq"
        assert lp.format_last_answer_source().startswith("Last answer: groq")

    def test_providers_command(self, monkeypatch):
        import orchestrator

        _chain(monkeypatch, {"gemini": _counting(answer="ok")}, chain=("gemini", "local"))
        lp.generate_chat(MSG, task="t")
        reply = orchestrator._repl_command("providers")
        assert reply.splitlines()[0].startswith("Last answer: gemini") and "gemini: today 1" in reply
