"""Shared test fixtures.

Tests must never write the owner's real data files. The provider usage counter
(llm_provider, data/provider_usage.json) resolves its path from
ZEDEK_PROVIDER_USAGE_PATH at call time, so point it at a temp file for the
whole session. The same applies to the MCP tool lock (mcp_client,
data/mcp_tool_lock.json) via ZEDEK_MCP_LOCK_PATH and the LLM cache
(llm_cache, data/llm_cache.sqlite3) via ZEDEK_LLM_CACHE_PATH.
"""

import os
import tempfile

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_provider_usage_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("provider_usage") / "provider_usage.json"
    previous = os.environ.get("ZEDEK_PROVIDER_USAGE_PATH")
    os.environ["ZEDEK_PROVIDER_USAGE_PATH"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("ZEDEK_PROVIDER_USAGE_PATH", None)
    else:
        os.environ["ZEDEK_PROVIDER_USAGE_PATH"] = previous


# The MCP lock must be redirected before collection: importing orchestrator
# runs MCP discovery (and pinning) at module load, before any fixture exists.
_previous_lock_path = None


def pytest_configure(config):
    global _previous_lock_path
    _previous_lock_path = os.environ.get("ZEDEK_MCP_LOCK_PATH")
    lock_dir = tempfile.mkdtemp(prefix="zedek_mcp_lock_")
    os.environ["ZEDEK_MCP_LOCK_PATH"] = os.path.join(lock_dir, "mcp_tool_lock.json")
    # The exact-match LLM cache (llm_cache) is off by default in tests, so one
    # test's stored result can never answer another's; cache tests turn it on.
    os.environ["ZEDEK_LLM_CACHE_PATH"] = os.path.join(lock_dir, "llm_cache.sqlite3")
    os.environ["LLM_CACHE"] = "off"
    # Learned router phrases and corrected misroutes are the owner's data;
    # classifier reads the path at import, so it is set before collection.
    os.environ["ZEDEK_DYNAMIC_UTTERANCES_PATH"] = os.path.join(lock_dir, "dynamic_utterances.json")
    os.environ["ZEDEK_MISROUTES_PATH"] = os.path.join(lock_dir, "misroutes.jsonl")
    # Long-term memory is the owner's data too. memory.py reads the path at
    # import, so tests get an empty throwaway store and can never read or
    # write the live one.
    os.environ["ZEDEK_CHROMA_PATH"] = os.path.join(lock_dir, "chroma_db")
    for name in ("SCHEDULE", "SCHEDULE_STATE", "INBOX"):
        os.environ[f"ZEDEK_{name}_PATH"] = os.path.join(lock_dir, f"{name.lower()}.json")


def pytest_unconfigure(config):
    if _previous_lock_path is None:
        os.environ.pop("ZEDEK_MCP_LOCK_PATH", None)
    else:
        os.environ["ZEDEK_MCP_LOCK_PATH"] = _previous_lock_path


# Tests must never reach a real language model: that spends free-tier quota and
# makes results depend on the network. Unit tests mock these calls; a test that
# forgets fails here instead of silently calling out.
_LLM_HOSTS = ("generativelanguage.googleapis.com", "api.groq.com", "integrate.api.nvidia.com",
              "openrouter.ai", "api.cerebras.ai")


class RealLLMCallInTest(RuntimeError):
    pass


@pytest.fixture(autouse=True)
def _block_real_llm_calls(monkeypatch):
    import ollama
    import requests

    real_post, real_get = requests.post, requests.get

    def guarded_post(url, *args, **kwargs):
        if any(host in str(url) for host in _LLM_HOSTS):
            raise RealLLMCallInTest(f"test tried to call a real LLM provider: {url}")
        return real_post(url, *args, **kwargs)

    def guarded_get(url, *args, **kwargs):  # model catalogs live on the same hosts
        if any(host in str(url) for host in _LLM_HOSTS):
            raise RealLLMCallInTest(f"test tried to fetch a real LLM catalog: {url}")
        return real_get(url, *args, **kwargs)

    def blocked_chat(*args, **kwargs):
        raise RealLLMCallInTest("test tried to call the real local model (ollama.chat)")

    monkeypatch.setattr(requests, "post", guarded_post)
    monkeypatch.setattr(requests, "get", guarded_get)
    monkeypatch.setattr(ollama, "chat", blocked_chat)
