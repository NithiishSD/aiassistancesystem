"""One local-model setting, thinking always off (OpenSpec change: local-model-qwen3, ROADMAP D3)."""

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

import llm_provider as lp
import orchestrator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def chat(monkeypatch):
    fake = MagicMock(return_value={"model": lp.LOCAL_MODEL, "message": {"content": '{"a": 1}'}})
    monkeypatch.setattr(lp.ollama, "chat", fake)
    return fake


def _assert_no_thinking(fake):
    assert fake.call_count >= 1
    for call in fake.call_args_list:
        assert call.kwargs["think"] is False and call.kwargs["model"] == lp.LOCAL_MODEL


def test_plain_local_call(chat):
    lp._local([{"role": "user", "content": "hi"}], False)
    _assert_no_thinking(chat)


def test_json_and_schema_local_calls(chat):
    lp._local([{"role": "user", "content": "hi"}], True)
    lp._local([{"role": "user", "content": "hi"}], False, schema={"type": "object"})
    _assert_no_thinking(chat)
    assert chat.call_args_list[0].kwargs["format"] == "json"


def test_streamed_local_call(chat):
    chat.return_value = iter([{"message": {"content": "x"}}])
    lp._local([{"role": "user", "content": "hi"}], False, on_delta=lambda _: None)
    _assert_no_thinking(chat)
    assert chat.call_args.kwargs["stream"] is True


def test_orchestrator_argument_extraction(chat):
    assert orchestrator._extract_args("search_files", "list files in /tmp") == {"a": 1}
    _assert_no_thinking(chat)
    assert chat.call_args.kwargs["format"] == "json"


def test_routing_model_follows_local_model():
    assert orchestrator.ROUTING_MODEL == lp.LOCAL_MODEL


def test_env_override():
    # llm_provider only: importing orchestrator in a child would register MCP tools.
    env = {**os.environ, "ZEDEK_LOCAL_MODEL": "some-model:1b"}
    out = subprocess.run([sys.executable, "-c", "import llm_provider; print(llm_provider.LOCAL_MODEL)"],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.stdout.split()[-1:] == ["some-model:1b"], out.stderr[-500:]
