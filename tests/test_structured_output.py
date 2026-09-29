"""Schema-constrained, validated LLM replies (OpenSpec change: add-structured-output).

No network: requests.post / ollama.chat are mocked, or provider functions are
stubbed. tests/conftest.py fails any test that reaches a real model.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import llm_provider as lp
import llm_schemas
from classifier_tools import VALID_INTENT_NAMES

MSG = [{"role": "user", "content": "q"}]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    lp._reset_health_for_tests()
    lp._SCHEMA_UNSUPPORTED.clear()
    monkeypatch.setattr(lp, "cloud_enabled", lambda: True)
    yield
    lp._reset_health_for_tests()
    lp._SCHEMA_UNSUPPORTED.clear()


# ── 1.1 schemas ─────────────────────────────────────────────────────────────

def _walk_objects(node):
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


ALL_MODELS = [llm_schemas.FactList, llm_schemas.FactCorrection, llm_schemas.ResearchQueries,
              llm_schemas.AcademicIntent, llm_schemas.intent_choice_model()]


class TestSchemas:
    @pytest.mark.parametrize("model", ALL_MODELS, ids=lambda m: m.__name__)
    def test_strict_mode_compatible(self, model):
        schema = llm_schemas.json_schema_for(model)
        dumped = json.dumps(schema)
        assert "$ref" not in dumped and "$defs" not in dumped and '"title"' not in dumped
        objects = list(_walk_objects(schema))
        assert objects
        for obj in objects:
            assert obj["additionalProperties"] is False
            assert set(obj["required"]) == set(obj["properties"])

    def test_intent_enum_matches_router(self):
        schema = llm_schemas.json_schema_for(llm_schemas.intent_choice_model())
        assert set(schema["properties"]["function_name"]["enum"]) == set(VALID_INTENT_NAMES)
        assert list(schema["properties"]) == ["function_name"]

    def test_returns_a_copy(self):
        llm_schemas.json_schema_for(llm_schemas.FactList)["properties"].clear()
        assert llm_schemas.json_schema_for(llm_schemas.FactList)["properties"]


# ── 1.2 provider adapter ────────────────────────────────────────────────────

SCHEMA = llm_schemas.json_schema_for(llm_schemas.FactCorrection)


def _openai_ok(text='{"index": 0, "corrected_fact": null}'):
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": text}}]}
    response.raise_for_status.return_value = None
    return response


def _gemini_ok(text='{"index": 0, "corrected_fact": null}'):
    response = MagicMock()
    response.json.return_value = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    response.raise_for_status.return_value = None
    return response


def _http_status(status):
    response = MagicMock()
    response.status_code = status
    error = requests.exceptions.HTTPError(f"{status}", response=response)
    failing = MagicMock()
    failing.raise_for_status.side_effect = error
    return failing


def _payload(post_mock, call=-1):
    return post_mock.call_args_list[call].kwargs["json"]


def _is_hinted(messages):
    return "matching this JSON schema" in messages[-1]["content"]


@pytest.fixture
def keys(monkeypatch):
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.setenv(name, "k")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    monkeypatch.setenv("OPENROUTER_MODEL", "some/free:free")
    monkeypatch.setenv("CEREBRAS_MODEL", "llama-3.3-70b")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(lp, "resolve_nvidia_model", lambda: "nim-model")


class TestAdapter:
    def test_gemini_native(self, keys):
        with patch.object(lp.requests, "post", return_value=_gemini_ok()) as post:
            lp._gemini(MSG, True, schema=SCHEMA)
        config = _payload(post)["generationConfig"]
        assert config == {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA}

    def test_gemini_400_downgrades_and_remembers(self, keys):
        with patch.object(lp.requests, "post", side_effect=[_http_status(400), _gemini_ok()]) as post:
            lp._gemini(MSG, True, schema=SCHEMA)
        retry = _payload(post)
        assert "responseJsonSchema" not in retry["generationConfig"]
        assert "matching this JSON schema" in retry["contents"][-1]["parts"][0]["text"]
        assert ("gemini", "gemini-2.5-flash") in lp._SCHEMA_UNSUPPORTED
        with patch.object(lp.requests, "post", return_value=_gemini_ok()) as post:
            lp._gemini(MSG, True, schema=SCHEMA)
        assert post.call_count == 1 and "responseJsonSchema" not in _payload(post)["generationConfig"]

    def test_non_400_error_is_not_a_downgrade(self, keys):
        with patch.object(lp.requests, "post", return_value=_http_status(429)):
            with pytest.raises(requests.exceptions.HTTPError):
                lp._groq(MSG, True, schema=SCHEMA)
        assert not lp._SCHEMA_UNSUPPORTED

    def test_groq_gpt_oss_is_strict(self, keys, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._groq(MSG, True, schema=SCHEMA)
        body = _payload(post)
        assert body["response_format"] == {"type": "json_schema", "json_schema": {
            "name": "response", "schema": SCHEMA, "strict": True}}
        assert not _is_hinted(body["messages"])

    def test_groq_other_model_uses_json_object_and_hint(self, keys, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._groq(MSG, True, schema=SCHEMA)
        body = _payload(post)
        assert body["response_format"] == {"type": "json_object"} and _is_hinted(body["messages"])

    def test_cerebras_strict(self, keys):
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._cerebras(MSG, True, schema=SCHEMA)
        assert _payload(post)["response_format"]["json_schema"]["strict"] is True

    def test_nim_guided_json(self, keys):
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._nvidia_nim(MSG, True, schema=SCHEMA)
        body = _payload(post)
        assert body["nvext"] == {"guided_json": SCHEMA} and "response_format" not in body

    def test_openai_compatible_400_downgrade(self, keys):
        with patch.object(lp.requests, "post", side_effect=[_http_status(400), _openai_ok()]) as post:
            lp._cerebras(MSG, True, schema=SCHEMA)
        body = _payload(post)
        assert body["response_format"] == {"type": "json_object"} and _is_hinted(body["messages"])
        assert ("cerebras", "llama-3.3-70b") in lp._SCHEMA_UNSUPPORTED

    def test_openrouter_hint(self, keys):
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._openrouter(MSG, True, schema=SCHEMA)
        body = _payload(post)
        assert body["response_format"] == {"type": "json_object"} and _is_hinted(body["messages"])

    def test_no_schema_request_unchanged(self, keys, monkeypatch):
        monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
        with patch.object(lp.requests, "post", return_value=_openai_ok()) as post:
            lp._groq(MSG, False)
        body = _payload(post)
        assert "response_format" not in body and body["messages"] == MSG

    def test_local_schema_format(self):
        chat = MagicMock(return_value={"message": {"content": "{}"}})
        with patch.object(lp.ollama, "chat", chat):
            lp._local(MSG, True, schema=SCHEMA)
        assert chat.call_args.kwargs["format"] == SCHEMA

    def test_local_downgrade_on_response_error(self):
        chat = MagicMock(side_effect=[lp.ollama.ResponseError("bad format"), {"message": {"content": "{}"}}])
        with patch.object(lp.ollama, "chat", chat):
            lp._local(MSG, True, schema=SCHEMA)
        retry = chat.call_args_list[1].kwargs
        assert retry["format"] == "json" and _is_hinted(retry["messages"])
        assert ("local", lp.LOCAL_MODEL) in lp._SCHEMA_UNSUPPORTED


# ── 1.3 validation loop ─────────────────────────────────────────────────────

def _scripted(*texts):
    """Provider stub replying with each text in turn; records messages and schema."""
    replies = iter(texts)
    seen = []

    def fn(messages, json_mode, schema=None):
        seen.append({"messages": messages, "schema": schema})
        return lp.ProviderReply(text=next(replies), request_model="m", response_model="m")
    fn.seen = seen
    return fn


VALID = '{"index": 1, "corrected_fact": "User\'s college: PSG"}'
MISSING_FIELD = '{"index": 1}'


def _chain(monkeypatch, funcs, local=None):
    monkeypatch.setattr(lp, "_PROVIDER_FUNCS", funcs)
    monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": [*funcs, "local"]})
    monkeypatch.setattr(lp, "_local", local or _scripted(MISSING_FIELD, MISSING_FIELD))


class TestValidationLoop:
    def test_valid_first_reply(self, monkeypatch):
        gemini = _scripted(VALID)
        _chain(monkeypatch, {"gemini": gemini})
        result = lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")
        assert result["source"] == "gemini" and result["data"].index == 1
        assert gemini.seen[0]["schema"] == SCHEMA

    def test_corrected_on_reask(self, monkeypatch):
        gemini = _scripted(MISSING_FIELD, VALID)
        _chain(monkeypatch, {"gemini": gemini, "groq": _scripted(VALID)})
        result = lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")
        assert result["source"] == "gemini" and result["data"].corrected_fact == "User's college: PSG"
        reask = gemini.seen[1]["messages"]
        assert reask[-2] == {"role": "assistant", "content": MISSING_FIELD}
        assert "corrected_fact" in reask[-1]["content"] and "did not match" in reask[-1]["content"]
        assert lp.provider_stats()["gemini"]["calls"] == 2  # the re-ask is counted

    def test_still_wrong_moves_to_next_provider(self, monkeypatch):
        gemini = _scripted(MISSING_FIELD, "not json at all")
        groq = _scripted(VALID)
        _chain(monkeypatch, {"gemini": gemini, "groq": groq})
        result = lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")
        assert result["source"] == "groq" and len(gemini.seen) == 2

    def test_local_is_last_and_can_succeed(self, monkeypatch):
        local = _scripted(VALID)
        _chain(monkeypatch, {"gemini": _scripted("[]", "[]")}, local=local)
        result = lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")
        assert result["source"] == "local" and local.seen[0]["schema"] == SCHEMA

    def test_all_invalid_raises(self, monkeypatch):
        _chain(monkeypatch, {"gemini": _scripted("{}", "{}")})
        with pytest.raises(lp.StructuredOutputError):
            lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")

    def test_structured_error_is_an_unavailable_error(self):
        assert issubclass(lp.StructuredOutputError, lp.AllProvidersUnavailableError)

    def test_provider_error_during_reask_moves_on(self, monkeypatch):
        calls = {"n": 0}

        def flaky(messages, json_mode, schema=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("connection reset")
            return lp.ProviderReply(text=MISSING_FIELD)
        _chain(monkeypatch, {"gemini": flaky, "groq": _scripted(VALID)})
        assert lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")["source"] == "groq"

    def test_invalid_output_log_has_no_reply_text(self, monkeypatch):
        secret = '{"index": "SECRET-REPLY-TEXT"}'
        _chain(monkeypatch, {"gemini": _scripted(secret, secret), "groq": _scripted(VALID)})
        events = []
        monkeypatch.setattr(lp.log, "info", lambda msg, extra=None: events.append((msg, extra or {})))
        lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")
        invalid = [e for m, e in events if m == "structured_output_invalid"]
        assert [e["attempt"] for e in invalid] == [1, 2]
        assert "SECRET-REPLY-TEXT" not in str(invalid)
        assert invalid[0]["source"] == "gemini" and invalid[0]["schema"] == "FactCorrection"

    @pytest.mark.parametrize("wrapped", [
        f"<think>let me see</think>{VALID}",
        f"```json\n{VALID}\n```",
        f"```\n{VALID}\n```",
    ])
    def test_think_and_fences_parse(self, monkeypatch, wrapped):
        _chain(monkeypatch, {"gemini": _scripted(wrapped)})
        assert lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t")["data"].index == 1

    def test_force_local(self, monkeypatch):
        gemini = _scripted(VALID)
        local = _scripted(VALID)
        _chain(monkeypatch, {"gemini": gemini}, local=local)
        assert lp.generate_structured(MSG, llm_schemas.FactCorrection, task="t", force_local=True)["source"] == "local"
        assert gemini.seen == []

    def test_generate_chat_does_not_send_schema(self, monkeypatch):
        seen = []
        monkeypatch.setattr(lp, "_PROVIDER_FUNCS", {"gemini": lambda m, j: seen.append(j) or lp.ProviderReply("hi")})
        monkeypatch.setattr(lp, "TASK_PROVIDERS", {"t": ["gemini", "local"]})
        assert lp.generate_chat(MSG, task="t")["answer"] == "hi"
        assert "data" not in lp.generate_chat(MSG, task="t")


# ── 2.1 call sites ──────────────────────────────────────────────────────────

def _structured(data, source="gemini"):
    return {"data": data, "answer": data.model_dump_json(), "source": source}


class TestCanonicalization:
    def test_facts_render_in_storage_format(self):
        import orchestrator
        data = llm_schemas.FactList(facts=[
            llm_schemas.Fact(attribute="college", value="PSG College of Technology"),
            llm_schemas.Fact(attribute="department", value="AMCS"),
        ])
        with patch.object(orchestrator.llm_provider, "generate_structured", return_value=_structured(data)) as llm:
            facts = orchestrator.canonicalize_fact("i study at psg in the amcs department")
        assert facts == ["User's college: PSG College of Technology", "User's department: AMCS"]
        assert llm.call_args.args[1] is llm_schemas.FactList

    @pytest.mark.parametrize("value", ["unknown", "", "  ", "Not specified", "N/A"])
    def test_placeholder_values_dropped(self, value):
        import orchestrator
        data = llm_schemas.FactList(facts=[llm_schemas.Fact(attribute="college", value=value)])
        with patch.object(orchestrator.llm_provider, "generate_structured", return_value=_structured(data)):
            assert orchestrator.canonicalize_fact("x") == []

    def test_no_fact_is_empty_list(self):
        import orchestrator
        with patch.object(orchestrator.llm_provider, "generate_structured",
                          return_value=_structured(llm_schemas.FactList(facts=[]))):
            assert orchestrator.canonicalize_fact("hello there") == []

    def test_structured_failure_stores_nothing(self):
        import orchestrator
        with patch.object(orchestrator.llm_provider, "generate_structured",
                          side_effect=lp.StructuredOutputError("none valid")):
            assert orchestrator.canonicalize_fact("i study at psg") == []


class TestCorrection:
    def _run(self, data=None, error=None):
        import orchestrator
        candidates = [{"id": "a", "text": "User's college: MIT"}, {"id": "b", "text": "User's city: Chennai"}]
        llm = (patch.object(orchestrator.llm_provider, "generate_structured", side_effect=error) if error
               else patch.object(orchestrator.llm_provider, "generate_structured", return_value=_structured(data)))
        with llm, \
             patch.object(orchestrator, "LAST_ROUTING_DECISION", None), \
             patch.object(orchestrator, "_last_assistant_question", return_value=None), \
             patch.object(orchestrator.memory, "retrieve", return_value=candidates), \
             patch.object(orchestrator.memory, "delete_by_ids") as delete, \
             patch.object(orchestrator.memory, "store") as store:
            reply = orchestrator._handle_correction("actually my college is PSG", "personal")
        return reply, delete, store

    def test_update(self):
        reply, delete, store = self._run(llm_schemas.FactCorrection(index=0, corrected_fact="User's college: PSG"))
        delete.assert_called_once_with(["a"], domain="personal")
        store.assert_called_once_with("User's college: PSG", domain="personal", content_type="fact")
        assert "updated" in reply

    def test_retraction(self):
        reply, delete, store = self._run(llm_schemas.FactCorrection(index=1, corrected_fact=None))
        delete.assert_called_once_with(["b"], domain="personal")
        store.assert_not_called()

    @pytest.mark.parametrize("index", [None, 7, -1])
    def test_no_confident_match_changes_nothing(self, index):
        reply, delete, store = self._run(llm_schemas.FactCorrection(index=index, corrected_fact=None))
        delete.assert_not_called()
        store.assert_not_called()
        assert "couldn't confidently" in reply

    def test_structured_failure_changes_nothing(self):
        reply, delete, store = self._run(error=lp.StructuredOutputError("none valid"))
        delete.assert_not_called()
        store.assert_not_called()


class TestIntentFallback:
    def _run(self, text, name=None, error=None):
        import classifier
        model = llm_schemas.intent_choice_model()
        # classifier imports llm_provider inside the function, so patch the module itself.
        mock = (patch.object(lp, "generate_structured", side_effect=error) if error
                else patch.object(lp, "generate_structured", return_value=_structured(model(function_name=name))))
        with mock as llm:
            result = classifier.query_llm_with_tools(text)
        return result, llm

    def test_constrained_to_router_intents(self):
        result, llm = self._run("how big is my downloads folder", "directory_size")
        assert result["function"] == "directory_size" and result["via_llm"] is True
        schema_model = llm.call_args.args[1]
        assert set(llm_schemas.json_schema_for(schema_model)["properties"]["function_name"]["enum"]) \
            == set(VALID_INTENT_NAMES)

    def test_general_question_maps_to_none(self):
        result, _ = self._run("why is the sky blue", "general_question")
        assert result["function"] is None

    def test_build_request_not_opened_as_app(self):
        result, _ = self._run("build a website for my club", "open_application")
        assert result["function"] == "coding_task"

    def test_failure_is_low_confidence(self):
        result, _ = self._run("x", error=lp.StructuredOutputError("none valid"))
        assert result["function"] is None and result["confidence"] == "low"


# ── 2.2 MCP arguments ───────────────────────────────────────────────────────

class TestMcpArgs:
    def _spec(self, schema):
        from mcp_client import MCPToolSpec
        return MCPToolSpec(server_name="s", tool_name="weather", description="Gets weather.",
                           input_schema=schema, qualified_name="mcp_s_weather")

    def test_tool_schema_constrains_local_decoding(self):
        import orchestrator
        schema = {"type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
                  "required": ["city"]}
        chat = MagicMock(return_value={"message": {"content": '{"city": "Chennai", "days": 3}'}})
        with patch.object(orchestrator, "_select_mcp_tool", return_value=self._spec(schema)), \
             patch.object(orchestrator.ollama, "chat", chat):
            result = orchestrator._extract_mcp_args("weather in chennai for 3 days")
        assert chat.call_args.kwargs["format"] == schema
        assert result == {"qualified_name": "mcp_s_weather", "tool_args": {"city": "Chennai", "days": 3}}
