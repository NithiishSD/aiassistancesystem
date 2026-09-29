"""Shared test fixtures.

Tests must never write the owner's real data files. The provider usage counter
(llm_provider, data/provider_usage.json) resolves its path from
ZEDEK_PROVIDER_USAGE_PATH at call time, so point it at a temp file for the
whole session. The same applies to the MCP tool lock (mcp_client,
data/mcp_tool_lock.json) via ZEDEK_MCP_LOCK_PATH.
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
