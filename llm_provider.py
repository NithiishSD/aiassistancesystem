"""Cloud-first chat provider adapter with a local Ollama fallback.

Providers are tried in order until one succeeds. Gemini, Groq, NVIDIA, and OpenRouter
resolve their models dynamically against each provider's live catalog.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Iterator, Protocol

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

import llm_cache
import ollama
from llm_schemas import json_schema_for
from zedek_logger import get_logger

load_dotenv()

log = get_logger("llm_provider")
LOCAL_MODEL = "llama3.1:8b"
REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", "12"))
MODEL_CACHE_TTL = 3600  # re-check live catalogs at most once an hour

log.info("provider_mode_configured", extra={
    "cloud_enabled": os.getenv("ALLOW_CLOUD", "true").strip().lower() not in ("false", "0", "no"),
    "cloud_coding_allowed": os.getenv("ALLOW_CLOUD_CODING", "true").strip().lower() not in ("false", "0", "no"),
})

# Candidate pools, in preference order. Only these models are ever requested
# (ROADMAP C3): a catalog with none of them skips the provider rather than
# using an arbitrary entry. Probed live 2026-09-29 unless noted.
GEMINI_CANDIDATES = [  # Google's current free-tier Flash line (not probed: unreachable here)
    "gemini-3-flash",
    "gemini-3-flash-preview",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]
GROQ_CANDIDATES = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
]
NVIDIA_CODING_CANDIDATES = [
    "nvidia/nemotron-3-super-120b-a12b",
    "openai/gpt-oss-20b",
    "deepseek-ai/deepseek-v4.1-flash",
    "z-ai/glm-5.3",
]
OPENROUTER_CODING_CANDIDATES = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "qwen/qwen3.8-27b:free",
    "google/gemma-4-31b-it:free",
]
CEREBRAS_CANDIDATES = [
    "gpt-oss-120b",
    "qwen-3.8-27b",
]
# Ordered by quota headroom: OpenRouter's :free tier allows only 50 requests/day
# (verified against its docs), so it is never first in a chain.
TASK_PROVIDERS: dict[str, list[str]] = {
    "coding": ["nvidia_nim", "groq", "openrouter", "local"],
    "evaluation": ["gemini", "groq", "cerebras", "openrouter", "local"],
    "general_qa": ["gemini", "groq", "cerebras", "local"],
    "fact_handling": ["gemini", "groq", "local"],
    "process_reasoning": ["gemini", "groq", "cerebras", "openrouter", "local"],
    "planning": ["groq", "gemini", "cerebras", "local"],
    "research": ["gemini", "groq", "cerebras", "local"],
}
DEFAULT_CHAIN = ["gemini", "groq", "nvidia_nim", "openrouter", "cerebras", "local"]

_model_cache: dict[str, tuple[float, list[str]]] = {}


class AllProvidersUnavailableError(RuntimeError):
    """Raised when all configured providers, including local Ollama, fail."""


class StructuredOutputError(AllProvidersUnavailableError):
    """No provider, including local Ollama, produced a reply matching the schema.

    Subclasses AllProvidersUnavailableError so existing handlers still apply."""


def _safe_error_text(error: Exception) -> str:
    """Remove credentials from provider errors before they reach logs."""
    message = str(error)
    message = re.sub(
        r"([?&](?:key|api[_-]?key|token|access[_-]?token|secret[_-]?key)=)[^&\s]+",
        r"\1[REDACTED]",
        message,
        flags=re.IGNORECASE,
    )
    for variable in (
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "NVIDIA_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENROUTER_KEY",
        "CEREBRAS_API_KEY",
    ):
        secret = os.getenv(variable, "")
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message


def sanitize_messages_for_cloud(
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str], bool]:
    """Placeholder for the future LLM-backed security scanner."""
    return [dict(message) for message in messages], [], False


def cloud_enabled() -> bool:
    """Whether cloud providers should be tried at all."""
    return os.getenv("ALLOW_CLOUD", "true").strip().lower() not in ("false", "0", "no")


def cloud_coding_allowed() -> bool:
    """Return whether coding prompts may use cloud providers after sanitization."""
    return os.getenv("ALLOW_CLOUD_CODING", "true").strip().lower() not in ("false", "0", "no")


def _cached_fetch(key: str, fetch_fn) -> list[str]:
    """Fetch a provider's live model list, cached for MODEL_CACHE_TTL seconds."""
    now = time.time()
    cached = _model_cache.get(key)
    if cached and (now - cached[0]) < MODEL_CACHE_TTL:
        return cached[1]

    try:
        ids = fetch_fn()
        _model_cache[key] = (now, ids)
        return ids
    except Exception as error:
        log.info("model_list_fetch_failed", extra={"provider": key, "error": _safe_error_text(error)})
        return cached[1] if cached else []


def _fetch_gemini_models() -> list[str]:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        return []
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": api_key},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    models = response.json().get("models", [])
    return [
        m["name"].replace("models/", "")
        for m in models
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


def _fetch_groq_models() -> list[str]:
    api_key = os.getenv("GROQ_API_KEY", "")
    response = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [m["id"] for m in response.json().get("data", [])]


def _fetch_nvidia_models() -> list[str]:
    api_key = os.getenv("NVIDIA_API_KEY", "")
    response = requests.get(
        "https://integrate.api.nvidia.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [m["id"] for m in response.json().get("data", [])]


def _fetch_openrouter_free_models() -> list[str]:
    response = requests.get("https://openrouter.ai/api/v1/models", timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    models = response.json().get("data", [])
    return [
        m["id"]
        for m in models
        if m["id"].endswith(":free") and m.get("pricing", {}).get("prompt") == "0"
    ]


def _fetch_cerebras_models() -> list[str]:
    api_key = os.getenv("CEREBRAS_API_KEY", "")
    if not api_key:
        return []
    response = requests.get(
        "https://api.cerebras.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [m["id"] for m in response.json().get("data", [])]


# Models that answered 404 this session: listed in a catalog is not proof of
# being served (NIM lists several it no longer serves).
_DEAD_MODELS: set[tuple[str, str]] = set()


def _choose_model(source: str, candidates: list[str], live: list[str]) -> str | None:
    """First usable candidate present in the live catalog; None if the catalog
    is readable but holds none of them. If the catalog is unreachable, try the
    candidates in order."""
    usable = [c for c in candidates if (source, c) not in _DEAD_MODELS]
    if not live:
        return usable[0] if usable else None
    for candidate in usable:
        if candidate in live:
            return candidate
    log.warning("model_candidates_stale", extra={"source": source, "candidates": candidates})
    return None


def _resolve(source: str, cache_key: str, fetch_fn, candidates: list[str]) -> str:
    model = _choose_model(source, candidates, _cached_fetch(cache_key, fetch_fn))
    if model is None:
        # "not configured": the chain skips it without counting quota.
        raise RuntimeError(f"{source}: no usable chat model (not configured)")
    return model


def resolve_gemini_model() -> str:
    return _resolve("gemini", "gemini", _fetch_gemini_models, GEMINI_CANDIDATES)


def resolve_groq_model() -> str:
    return _resolve("groq", "groq", _fetch_groq_models, GROQ_CANDIDATES)


def resolve_nvidia_model() -> str:
    return _resolve("nvidia_nim", "nvidia", _fetch_nvidia_models, NVIDIA_CODING_CANDIDATES)


def resolve_openrouter_model() -> str:
    return _resolve("openrouter", "openrouter", _fetch_openrouter_free_models, OPENROUTER_CODING_CANDIDATES)


def resolve_cerebras_model() -> str:
    return _resolve("cerebras", "cerebras", _fetch_cerebras_models, CEREBRAS_CANDIDATES)


def _model_for(source: str, env_var: str, resolver: Callable[[], str]) -> str:
    """The env override unless it has been retired this session, else the resolver's pick."""
    override = os.getenv(env_var)
    if override and (source, override) not in _DEAD_MODELS:
        return override
    return resolver()


def _with_model_fallback(source: str, pick: Callable[[], str],
                         send: Callable[[str], "ProviderReply"]) -> "ProviderReply":
    """send(pick()); on HTTP 404 retire that model and retry once with the next pick."""
    def send_or_retire(model: str) -> "ProviderReply":
        try:
            return send(model)
        except requests.exceptions.HTTPError as err:
            if getattr(getattr(err, "response", None), "status_code", None) == 404:
                _DEAD_MODELS.add((source, model))
                log.warning("model_unavailable", extra={"source": source, "model": model})
            raise

    model = pick()
    try:
        return send_or_retire(model)
    except requests.exceptions.HTTPError:
        if (source, model) not in _DEAD_MODELS:
            raise  # not a 404
        replacement = pick()
        if replacement == model:
            raise
        return send_or_retire(replacement)


@dataclass(frozen=True)
class ProviderReply:
    """What a provider returned: the text plus what it reported about the call.
    Token counts are None when the provider didn't report them."""

    text: str
    request_model: str | None = None
    response_model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


def _int_or_none(value: Any) -> int | None:
    # bool is a subclass of int; a usage count is never a bool.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ── Reply streaming (ROADMAP D2) ────────────────────────────────────────────

class StreamSink(Protocol):
    """Receives a chat reply as it is generated. All calls happen on the caller's thread."""

    def delta(self, text: str) -> None:
        """The next piece of visible answer text, in order."""

    def restart(self) -> None:
        """Discard what was shown so far; a different provider's answer follows."""


class _ThinkFilter:
    """Streaming counterpart of strip_thinking_tags: drops <think>...</think> and
    stray closing tags even when a tag is split across chunks."""

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._inside = False
        self._held = ""

    @staticmethod
    def _partial_tag_len(text: str, tags: tuple[str, ...]) -> int:
        """Length of the longest suffix of text that could start one of tags."""
        lowered = text.lower()
        for size in range(min(len(lowered), max(map(len, tags)) - 1), 0, -1):
            if any(tag.startswith(lowered[-size:]) for tag in tags):
                return size
        return 0

    def feed(self, chunk: str) -> str:
        text, out = self._held + chunk, []
        self._held = ""
        while text:
            lowered = text.lower()
            if self._inside:
                end = lowered.find(self._CLOSE)
                if end < 0:
                    self._held = text[len(text) - self._partial_tag_len(text, (self._CLOSE,)):]
                    break
                text, self._inside = text[end + len(self._CLOSE):], False
                continue
            hits = [(i, tag) for tag in (self._OPEN, self._CLOSE) if (i := lowered.find(tag)) >= 0]
            if not hits:
                keep = self._partial_tag_len(text, (self._OPEN, self._CLOSE))
                out.append(text[:len(text) - keep])
                self._held = text[len(text) - keep:]
                break
            index, tag = min(hits)
            out.append(text[:index])
            text = text[index + len(tag):]
            self._inside = tag == self._OPEN
        return "".join(out)

    def flush(self) -> str:
        """End of reply: a held fragment that never became a tag is text."""
        held, self._held = self._held, ""
        return "" if self._inside else held


class _StreamRelay:
    """One chat call's link to a StreamSink: filters think blocks, trims leading
    whitespace, restarts the sink between providers, and never lets a sink
    error reach the provider chain."""

    def __init__(self, sink: StreamSink) -> None:
        self._sink: StreamSink | None = sink
        self._filter = _ThinkFilter()
        self.emitted = False
        self.first_token_at: float | None = None

    def begin_attempt(self) -> None:
        if self.emitted:
            self._call("restart")
        self.emitted = False
        self.first_token_at = None
        self._filter = _ThinkFilter()

    def feed(self, chunk: str) -> None:
        self._emit(self._filter.feed(chunk or ""))

    def finish(self) -> None:
        self._emit(self._filter.flush())

    def _emit(self, visible: str) -> None:
        if not self.emitted:
            visible = visible.lstrip()
        if not visible:
            return
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()
        self.emitted = True
        self._call("delta", visible)

    def _call(self, method: str, *args: str) -> None:
        if self._sink is None:
            return
        try:
            getattr(self._sink, method)(*args)
        except Exception as error:  # a broken display must not fail the answer
            log.info("stream_sink_failed", extra={"method": method, "error": type(error).__name__})
            self._sink = None


def _sse_data(response: Any) -> Iterator[Any]:
    """Parsed JSON payloads of a server-sent-events response, until [DONE]."""
    for raw in response.iter_lines(decode_unicode=True):
        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        if not line or not line.startswith("data:"):
            continue  # blank separators, comments (": keep-alive"), event: lines
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        yield json.loads(payload)


# ── Schema-constrained output (ROADMAP F2) ──────────────────────────────────
# Native constrained-output form per provider. A provider/model that rejects
# its native form with HTTP 400 is recorded here and gets JSON mode plus the
# schema in the prompt for the rest of the session.
_SCHEMA_UNSUPPORTED: set[tuple[str, str]] = set()
# Groq honours strict json_schema only on these models; others ignore strict.
GROQ_STRICT_SCHEMA_PREFIXES = ("openai/gpt-oss",)


def _schema_hint(messages: list[dict[str, str]], schema: dict[str, Any]) -> list[dict[str, str]]:
    """Messages plus the schema as text, for requests that are not natively constrained."""
    compact = json.dumps(schema, separators=(",", ":"))
    return [*messages, {"role": "user",
                        "content": f"Respond with only a JSON object matching this JSON schema: {compact}"}]


def _is_bad_request(error: Exception) -> bool:
    response = getattr(error, "response", None)
    return isinstance(error, requests.exceptions.HTTPError) and getattr(response, "status_code", None) == 400


def _mark_schema_unsupported(source: str, model: str) -> None:
    _SCHEMA_UNSUPPORTED.add((source, model))
    log.info("structured_mode_rejected", extra={"source": source, "model": model})


class _EmbeddedStatus:
    """Stands in for a response so an error body's status drives cooldowns."""

    def __init__(self, status_code: int | None) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}


def _raise_embedded_error(source: str, data: Any) -> None:
    """OpenRouter can answer HTTP 200 with {"error": {"code": 429, ...}} when an
    upstream fails. Raise it as the HTTP error it is, so a 429 cools the provider
    down and a 404 retires the model."""
    error = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
    code = error.get("code") if isinstance(error.get("code"), int) and not isinstance(error.get("code"), bool) else None
    raise requests.exceptions.HTTPError(
        f"{source} returned no choices (embedded error code {code})", response=_EmbeddedStatus(code))


def _post_openai_compatible(
    source: str,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    extra_payload: dict[str, Any],
    extra_headers: dict[str, str] | None,
) -> ProviderReply:
    payload: dict[str, Any] = {"model": model, "messages": messages, **extra_payload}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("choices"):
        _raise_embedded_error(source, data)
    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError(f"{source} returned a non-text response")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    reported = data.get("model")
    return ProviderReply(
        text=content,
        request_model=model,
        response_model=reported if isinstance(reported, str) and reported else model,
        input_tokens=_int_or_none(usage.get("prompt_tokens")),
        output_tokens=_int_or_none(usage.get("completion_tokens")),
    )


def _stream_openai_compatible(
    source: str,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    extra_headers: dict[str, str] | None,
    on_delta: Callable[[str], None],
) -> ProviderReply:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    response = requests.post(
        endpoint,
        headers=headers,
        json={"model": model, "messages": messages, "stream": True},
        timeout=REQUEST_TIMEOUT,  # per read, so it bounds silence, not total length
        stream=True,
    )
    try:
        response.raise_for_status()
        parts: list[str] = []
        reported: Any = None
        usage: dict[str, Any] = {}
        for data in _sse_data(response):
            if not isinstance(data, dict):
                continue
            if isinstance(data.get("error"), dict) and not data.get("choices"):
                _raise_embedded_error(source, data)
            reported = data.get("model") or reported
            groq_usage = data["x_groq"].get("usage") if isinstance(data.get("x_groq"), dict) else None
            chunk_usage = data.get("usage") or groq_usage
            if isinstance(chunk_usage, dict):
                usage = chunk_usage
            for choice in data.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
                    on_delta(content)
    finally:
        response.close()
    return ProviderReply(
        text="".join(parts),
        request_model=model,
        response_model=reported if isinstance(reported, str) and reported else model,
        input_tokens=_int_or_none(usage.get("prompt_tokens")),
        output_tokens=_int_or_none(usage.get("completion_tokens")),
    )


def _openai_compatible(
    source: str,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
    extra_headers: dict[str, str] | None = None,
    schema: dict[str, Any] | None = None,
    schema_style: str = "hint",
    on_delta: Callable[[str], None] | None = None,
) -> ProviderReply:
    """schema_style: "strict" (json_schema, strict), "nvext" (NIM guided_json),
    or "hint" (json_object with the schema in the prompt). on_delta streams
    plain-text replies only."""
    if not api_key:
        raise RuntimeError(f"{source} API key is not configured")
    if on_delta is not None and schema is None and not json_mode:
        return _stream_openai_compatible(source, endpoint, api_key, model, messages, extra_headers, on_delta)

    if schema is not None and schema_style != "hint" and (source, model) not in _SCHEMA_UNSUPPORTED:
        if schema_style == "strict":
            native: dict[str, Any] = {"response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }}
        else:  # nvext
            native = {"nvext": {"guided_json": schema}}
        try:
            return _post_openai_compatible(source, endpoint, api_key, model, messages, native, extra_headers)
        except requests.exceptions.HTTPError as err:
            if not _is_bad_request(err):
                raise
            _mark_schema_unsupported(source, model)

    if schema is not None:
        messages = _schema_hint(messages, schema)
    extra: dict[str, Any] = {"response_format": {"type": "json_object"}} if (json_mode or schema is not None) else {}
    return _post_openai_compatible(source, endpoint, api_key, model, messages, extra, extra_headers)

def _gemini_stream(response: Any, model_name: str, on_delta: Callable[[str], None]) -> ProviderReply:
    parts: list[str] = []
    meta: dict[str, Any] = {}
    reported: Any = None
    for data in _sse_data(response):
        if not isinstance(data, dict):
            continue
        if isinstance(data.get("usageMetadata"), dict):
            meta = data["usageMetadata"]
        reported = data.get("modelVersion") or reported
        for candidate in data.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                text = part.get("text")
                if isinstance(text, str) and text and not part.get("thought"):
                    parts.append(text)
                    on_delta(text)
    return ProviderReply(
        text="".join(parts),
        request_model=model_name,
        response_model=reported if isinstance(reported, str) and reported else model_name,
        input_tokens=_int_or_none(meta.get("promptTokenCount")),
        output_tokens=_int_or_none(meta.get("candidatesTokenCount")),
    )


def _gemini_request(api_key: str, model_name: str, contents: list[dict],
                    generation_config: dict[str, Any],
                    on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    method = "streamGenerateContent" if on_delta is not None else "generateContent"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:{method}"
    params = {"key": api_key, "alt": "sse"} if on_delta is not None else {"key": api_key}

    # Try up to 2 times to handle transient 503/500 errors
    for attempt in range(2):
        try:
            response = requests.post(
                url,
                params=params,
                json={"contents": contents, "generationConfig": generation_config},
                timeout=REQUEST_TIMEOUT,
                **({"stream": True} if on_delta is not None else {}),
            )
            response.raise_for_status()
            if on_delta is not None:
                # The 5xx retry around this fires before any chunk is read.
                try:
                    return _gemini_stream(response, model_name, on_delta)
                finally:
                    response.close()
            data = response.json()
            parts = data["candidates"][0]["content"]["parts"]
            meta = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), dict) else {}
            reported = data.get("modelVersion")
            return ProviderReply(
                text="".join(part["text"] for part in parts),
                request_model=model_name,
                response_model=reported if isinstance(reported, str) and reported else model_name,
                input_tokens=_int_or_none(meta.get("promptTokenCount")),
                output_tokens=_int_or_none(meta.get("candidatesTokenCount")),
            )
        except requests.exceptions.HTTPError as err:
            if err.response.status_code >= 500 and attempt == 0:
                time.sleep(1)
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def _gemini_contents(messages: list[dict[str, str]]) -> list[dict]:
    contents = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})
    return contents


def _gemini(messages: list[dict[str, str]], json_mode: bool,
            schema: dict[str, Any] | None = None,
            on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    generation_config: dict[str, Any] = {}
    if json_mode or schema is not None:
        generation_config["responseMimeType"] = "application/json"

    def send(model_name: str) -> ProviderReply:
        msgs = messages
        if schema is not None and ("gemini", model_name) not in _SCHEMA_UNSUPPORTED:
            try:
                return _gemini_request(api_key, model_name, _gemini_contents(msgs),
                                       {**generation_config, "responseJsonSchema": schema})
            except requests.exceptions.HTTPError as err:
                if not _is_bad_request(err):
                    raise
                _mark_schema_unsupported("gemini", model_name)
        if schema is not None:
            msgs = _schema_hint(msgs, schema)
        stream = on_delta if schema is None and not json_mode else None
        return _gemini_request(api_key, model_name, _gemini_contents(msgs), generation_config, stream)

    return _with_model_fallback("gemini", lambda: _model_for("gemini", "GEMINI_MODEL", resolve_gemini_model), send)


def _groq(messages: list[dict[str, str]], json_mode: bool,
          schema: dict[str, Any] | None = None,
          on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise RuntimeError("groq API key is not configured")

    def send(model: str) -> ProviderReply:
        return _openai_compatible(
            "groq",
            "https://api.groq.com/openai/v1/chat/completions",
            api_key,
            model,
            messages,
            json_mode,
            schema=schema,
            schema_style="strict" if model.startswith(GROQ_STRICT_SCHEMA_PREFIXES) else "hint",
            on_delta=on_delta,
        )

    return _with_model_fallback("groq", lambda: _model_for("groq", "GROQ_MODEL", resolve_groq_model), send)


def _nvidia_nim(messages: list[dict[str, str]], json_mode: bool,
                schema: dict[str, Any] | None = None,
                on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key:
        raise RuntimeError("nvidia_nim API key is not configured")

    def send(model: str) -> ProviderReply:
        return _openai_compatible(
            "nvidia_nim",
            "https://integrate.api.nvidia.com/v1/chat/completions",
            api_key,
            model,
            messages,
            json_mode,
            schema=schema,
            schema_style="nvext",
            on_delta=on_delta,
        )

    return _with_model_fallback("nvidia_nim", lambda: _model_for("nvidia_nim", "NVIDIA_MODEL", resolve_nvidia_model), send)


def _openrouter(messages: list[dict[str, str]], json_mode: bool,
                schema: dict[str, Any] | None = None,
                on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY", "")
    if not api_key:
        raise RuntimeError("openrouter API key is not configured")

    def send(model: str) -> ProviderReply:
        return _openai_compatible(
            "openrouter",
            "https://openrouter.ai/api/v1/chat/completions",
            api_key,
            model,
            messages,
            json_mode,
            extra_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost"),
                "X-OpenRouter-Title": os.getenv("OPENROUTER_SITE_NAME", "Zedek"),
            },
            schema=schema,
            schema_style="hint",  # free-model support varies
            on_delta=on_delta,
        )

    return _with_model_fallback(
        "openrouter", lambda: _model_for("openrouter", "OPENROUTER_MODEL", resolve_openrouter_model), send)


def _cerebras(messages: list[dict[str, str]], json_mode: bool,
              schema: dict[str, Any] | None = None,
              on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    api_key = os.getenv("CEREBRAS_API_KEY", "")
    if not api_key:
        raise RuntimeError("cerebras API key is not configured")

    def send(model: str) -> ProviderReply:
        return _openai_compatible(
            "cerebras",
            "https://api.cerebras.ai/v1/chat/completions",
            api_key,
            model,
            messages,
            json_mode,
            schema=schema,
            schema_style="strict",
            on_delta=on_delta,
        )

    return _with_model_fallback(
        "cerebras", lambda: _model_for("cerebras", "CEREBRAS_MODEL", resolve_cerebras_model), send)


def _field(obj: Any, key: str) -> Any:
    # ollama 0.3 returns a dict; newer clients return an object that also
    # supports item access. Missing fields are None, never an error.
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return getattr(obj, key, None)


def _local_stream(messages: list[dict[str, str]], on_delta: Callable[[str], None]) -> ProviderReply:
    parts: list[str] = []
    last: Any = None
    for chunk in ollama.chat(model=LOCAL_MODEL, messages=messages, stream=True):
        last = chunk
        message = _field(chunk, "message")
        content = _field(message, "content") if message is not None else None
        if isinstance(content, str) and content:
            parts.append(content)
            on_delta(content)
    reported = _field(last, "model") if last is not None else None
    return ProviderReply(
        text="".join(parts),
        request_model=LOCAL_MODEL,
        response_model=reported if isinstance(reported, str) and reported else LOCAL_MODEL,
        input_tokens=_int_or_none(_field(last, "prompt_eval_count")) if last is not None else None,
        output_tokens=_int_or_none(_field(last, "eval_count")) if last is not None else None,
    )


def _local(messages: list[dict[str, str]], json_mode: bool,
           schema: dict[str, Any] | None = None,
           on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    if on_delta is not None and schema is None and not json_mode:
        return _local_stream(messages, on_delta)
    options: dict[str, Any] = {}
    if json_mode or schema is not None:
        options["format"] = "json"
    response = None
    if schema is not None and ("local", LOCAL_MODEL) not in _SCHEMA_UNSUPPORTED:
        try:
            response = ollama.chat(model=LOCAL_MODEL, messages=messages, format=schema)
        except ollama.ResponseError:
            _mark_schema_unsupported("local", LOCAL_MODEL)
    if response is None:
        if schema is not None:
            messages = _schema_hint(messages, schema)
        response = ollama.chat(model=LOCAL_MODEL, messages=messages, **options)
    reported = _field(response, "model")
    return ProviderReply(
        text=response["message"]["content"],
        request_model=LOCAL_MODEL,
        response_model=reported if isinstance(reported, str) and reported else LOCAL_MODEL,
        input_tokens=_int_or_none(_field(response, "prompt_eval_count")),
        output_tokens=_int_or_none(_field(response, "eval_count")),
    )


_PROVIDER_FUNCS: dict[str, Callable[[list[dict[str, str]], bool], "ProviderReply"]] = {
    "gemini": _gemini,
    "groq": _groq,
    "nvidia_nim": _nvidia_nim,
    "openrouter": _openrouter,
    "cerebras": _cerebras,
}


# ── Quota awareness ──────────────────────────────────────────────────────────
# Free-tier quota is the scarce resource. Without this, a provider that just
# returned 429 is called again on the very next request, and nothing stops
# OpenRouter's 50/day cap from being exceeded.

# Only caps verified against provider docs. Others rely on 429 cooldowns.
DAILY_REQUEST_BUDGETS: dict[str, int] = {"openrouter": 50}
DEFAULT_RATE_LIMIT_COOLDOWN = 60.0
MIN_COOLDOWN = 1.0
MAX_COOLDOWN = 3600.0
AUTH_FAILURE_COOLDOWN = 3600.0
_DEFAULT_USAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "data", "provider_usage.json")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


@dataclass
class _ProviderHealth:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    rate_limited: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


_health: dict[str, _ProviderHealth] = {}
_health_lock = threading.Lock()


def _now() -> float:
    return time.time()


def _today() -> str:
    return date.today().isoformat()


def _reset_health_for_tests() -> None:
    with _health_lock:
        _health.clear()
    _DEAD_MODELS.clear()


def _health_for(source: str) -> _ProviderHealth:
    return _health.setdefault(source, _ProviderHealth())


def _usage_path() -> str:
    # Resolved per call so tests can redirect it (tests/conftest.py).
    return os.getenv("ZEDEK_PROVIDER_USAGE_PATH") or _DEFAULT_USAGE_PATH


def _load_usage() -> dict[str, int]:
    """Today's request counts per provider. A missing or corrupt file is empty."""
    try:
        with open(_usage_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        today = data.get(_today(), {}) if isinstance(data, dict) else {}
        return {k: int(v) for k, v in today.items() if isinstance(v, (int, float))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _increment_usage(source: str) -> None:
    """Count one request that actually left the machine. Keeps only today."""
    path = _usage_path()
    with _health_lock:
        counts = _load_usage()
        counts[source] = counts.get(source, 0) + 1
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            temp = f"{path}.tmp"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump({_today(): counts}, handle, indent=2)
            os.replace(temp, path)
        except OSError as error:
            log.info("provider_usage_save_failed", extra={"error": str(error)})


def _parse_duration(value: Any) -> float | None:
    """Seconds from "30", "2m59.56s", "450ms", "1h". None if unparseable."""
    if value is None:
        return None
    text = str(value).strip().lower()
    try:
        return float(text)
    except ValueError:
        pass
    total, matched = 0.0, False
    for amount, unit in _DURATION_RE.findall(text):
        matched = True
        total += float(amount) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total if matched else None


def _cooldown_for(status: int | None, headers: Any) -> float:
    """How long to skip a provider after an HTTP error. 0 means don't skip."""
    if status in (401, 402, 403):  # 402: plan/billing ended; retrying won't help
        return AUTH_FAILURE_COOLDOWN
    if status != 429:
        return 0.0
    lowered = {str(k).lower(): v for k, v in dict(headers or {}).items()}
    for header in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        seconds = _parse_duration(lowered.get(header))
        if seconds is not None:
            return min(max(seconds, MIN_COOLDOWN), MAX_COOLDOWN)
    return DEFAULT_RATE_LIMIT_COOLDOWN


def _is_not_configured(error: Exception) -> bool:
    # Provider functions raise this before any network I/O when a key is missing.
    return isinstance(error, RuntimeError) and "not configured" in str(error)


def _skip_reason(source: str) -> tuple[str, dict[str, Any]] | None:
    with _health_lock:
        remaining = _health_for(source).cooldown_until - _now()
    if remaining > 0:
        return "cooldown", {"seconds_remaining": round(remaining, 1)}
    budget = DAILY_REQUEST_BUDGETS.get(source)
    if budget is not None:
        used = _load_usage().get(source, 0)
        if used >= budget:
            return "daily_budget", {"used": used, "budget": budget}
    return None


def _record_success(source: str, reply: "ProviderReply | None" = None) -> None:
    _increment_usage(source)
    with _health_lock:
        health = _health_for(source)
        health.calls += 1
        health.successes += 1
        if reply is not None:
            health.input_tokens += reply.input_tokens or 0
            health.output_tokens += reply.output_tokens or 0


def _record_failure(source: str, error: Exception) -> None:
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    cooldown = _cooldown_for(status, getattr(response, "headers", None))
    _increment_usage(source)
    with _health_lock:
        health = _health_for(source)
        health.calls += 1
        health.failures += 1
        health.last_error = _safe_error_text(error)[:200]
        if status == 429:
            health.rate_limited += 1
        if cooldown > 0:
            health.cooldown_until = max(health.cooldown_until, _now() + cooldown)
    if cooldown > 0:
        log.info("provider_cooldown_started", extra={
            "source": source, "status": status, "seconds": round(cooldown, 1)})


def provider_stats() -> dict[str, dict[str, Any]]:
    """Per provider: session calls/successes/failures/rate limits, today's
    requests, the daily budget, and any remaining cooldown."""
    today = _load_usage()
    now = _now()
    stats: dict[str, dict[str, Any]] = {}
    with _health_lock:
        for source in _PROVIDER_FUNCS:
            health = _health.get(source, _ProviderHealth())
            stats[source] = {
                "calls": health.calls,
                "successes": health.successes,
                "failures": health.failures,
                "rate_limited": health.rate_limited,
                "today": today.get(source, 0),
                "budget": DAILY_REQUEST_BUDGETS.get(source),
                "cooldown_remaining_s": round(max(0.0, health.cooldown_until - now), 1),
                "input_tokens": health.input_tokens,
                "output_tokens": health.output_tokens,
            }
    return stats


def format_provider_stats(stats: dict[str, dict[str, Any]] | None = None) -> str:
    stats = stats if stats is not None else provider_stats()
    lines = []
    for source, s in stats.items():
        budget = f"/{s['budget']}" if s["budget"] is not None else ""
        cooling = f", cooling {s['cooldown_remaining_s']:.0f}s" if s["cooldown_remaining_s"] else ""
        lines.append(f"{source}: today {s['today']}{budget}, session {s['successes']}/{s['calls']} ok, "
                     f"{s['rate_limited']} rate-limited{cooling}")
    return "\n".join(lines)


def strip_thinking_tags(text: str) -> str:
    """Remove reasoning/thinking traces (<think>...</think>) from LLM outputs."""
    if not isinstance(text, str):
        return text
    # Remove complete <think>...</think> blocks
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
    # Remove unclosed opening/closing tags if any
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


# OpenTelemetry GenAI semantic conventions (gen_ai.provider.name replaced the
# deprecated gen_ai.system). gcp.gemini and groq are well-known values; the
# rest are custom values, which the spec allows.
GEN_AI_PROVIDER_NAMES = {
    "gemini": "gcp.gemini", "groq": "groq", "nvidia_nim": "nvidia_nim",
    "openrouter": "openrouter", "cerebras": "cerebras", "local": "ollama",
}


def _log_first_token(source: str, relay: "_StreamRelay", started: float) -> None:
    if relay.first_token_at is not None:
        log.info("stream_first_token", extra={
            "source": source, "ms": round((relay.first_token_at - started) * 1000)})


def _log_gen_ai_call(source: str, reply: "ProviderReply", started: float, task: str | None) -> None:
    """One standard record per successful LLM call. Never includes prompt or
    response text, only names, counts, and timing."""
    log.info("gen_ai.client.operation", extra={
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": GEN_AI_PROVIDER_NAMES.get(source, source),
        "gen_ai.request.model": reply.request_model,
        "gen_ai.response.model": reply.response_model,
        "gen_ai.usage.input_tokens": reply.input_tokens,
        "gen_ai.usage.output_tokens": reply.output_tokens,
        "zedek.duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "zedek.task": task,
        "source": source,
    })


def _result(answer: str, source: str, reply: "ProviderReply") -> dict[str, Any]:
    return {
        "answer": answer,
        "source": source,
        "model": reply.response_model,
        "usage": {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens},
    }


def _run_local_or_raise(messages: list[dict[str, str]], json_mode: bool,
                        task: str | None = None, relay: "_StreamRelay | None" = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        if relay is None:
            reply = _local(messages, json_mode)
        else:
            relay.begin_attempt()
            reply = _local(messages, json_mode, on_delta=relay.feed)
            relay.finish()
            _log_first_token("local", relay, started)
    except Exception as error:
        log.info("local_fallback_failed", extra={"error": _safe_error_text(error)})
        raise AllProvidersUnavailableError(
            "All cloud providers failed and local Ollama is unavailable. "
            f"Check `ollama serve` and `ollama pull {LOCAL_MODEL}`."
        ) from error
    cleaned_answer = strip_thinking_tags(reply.text) if not json_mode else reply.text
    log.info("provider_response", extra={"source": "local"})
    _log_gen_ai_call("local", reply, started, task)
    return _result(cleaned_answer, "local", reply)


# ── Structured replies: validate, re-ask once, move on ──────────────────────

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_MAX_REASK_ECHO_CHARS = 2000


def _parse_validated(text: str, response_model: type[BaseModel]) -> BaseModel:
    """JSON text -> validated model. Raises ValidationError (a ValueError)."""
    cleaned = strip_thinking_tags(text or "").strip()
    fenced = _CODE_FENCE_RE.match(cleaned)
    if fenced:
        cleaned = fenced.group(1)
    return response_model.model_validate_json(cleaned)


def _validation_summary(error: Exception) -> str:
    """Where and why validation failed, never the reply's content."""
    if isinstance(error, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in error.errors()[:3]]
        return "; ".join(parts)
    return type(error).__name__


def _structured_from(source: str, call: Callable[[list[dict[str, str]]], ProviderReply],
                     messages: list[dict[str, str]],
                     response_model: type[BaseModel]) -> tuple[BaseModel, ProviderReply] | None:
    """One provider: first reply, then at most one correction. None = still invalid.
    Exceptions from `call` (network, quota) propagate to the chain loop."""
    reply = call(messages)
    for attempt in (1, 2):
        try:
            return _parse_validated(reply.text, response_model), reply
        except ValueError as error:
            summary = _validation_summary(error)
            log.info("structured_output_invalid", extra={
                "source": source, "attempt": attempt,
                "schema": response_model.__name__, "error": summary[:300],
            })
            if attempt == 2:
                return None
            reply = call([
                *messages,
                {"role": "assistant", "content": (reply.text or "")[:_MAX_REASK_ECHO_CHARS]},
                {"role": "user", "content": (
                    f"That reply did not match the required JSON schema: {summary}. "
                    "Reply again with only JSON that matches the schema."
                )},
            ])
    return None  # pragma: no cover


def _call_provider(provider: Callable[..., ProviderReply], messages: list[dict[str, str]],
                   json_mode: bool, schema: dict[str, Any] | None,
                   on_delta: Callable[[str], None] | None = None) -> ProviderReply:
    # Keywords only when set, so two-argument provider stubs keep working.
    kwargs: dict[str, Any] = {}
    if schema is not None:
        kwargs["schema"] = schema
    if on_delta is not None:
        kwargs["on_delta"] = on_delta
    return provider(messages, json_mode, **kwargs)


def _run_local_structured(messages: list[dict[str, str]], task: str | None,
                          response_model: type[BaseModel],
                          schema: dict[str, Any]) -> dict[str, Any]:
    def call(msgs: list[dict[str, str]]) -> ProviderReply:
        started = time.perf_counter()
        try:
            reply = _local(msgs, True, schema=schema)
        except Exception as error:
            log.info("local_fallback_failed", extra={"error": _safe_error_text(error)})
            raise AllProvidersUnavailableError(
                "All cloud providers failed and local Ollama is unavailable. "
                f"Check `ollama serve` and `ollama pull {LOCAL_MODEL}`."
            ) from error
        log.info("provider_response", extra={"source": "local"})
        _log_gen_ai_call("local", reply, started, task)
        return reply

    outcome = _structured_from("local", call, messages, response_model)
    if outcome is None:
        raise StructuredOutputError(
            f"No provider produced a reply matching {response_model.__name__}.")
    data, reply = outcome
    return {**_result(reply.text, "local", reply), "data": data}


def _dispatch(
    messages: list[dict[str, str]],
    json_mode: bool,
    force_local: bool,
    task: str | None,
    response_model: type[BaseModel] | None = None,
    stream: StreamSink | None = None,
) -> dict[str, Any]:
    """The provider chain shared by generate_chat and generate_structured."""
    schema = json_schema_for(response_model) if response_model is not None else None
    relay = _StreamRelay(stream) if stream is not None and response_model is None and not json_mode else None

    if task == "coding" and not force_local:
        if not cloud_coding_allowed():
            force_local = True
        else:
            sanitized, secret_kinds, hard_block = sanitize_messages_for_cloud(messages)
            if secret_kinds:
                log.info("secrets_redacted", extra={"kinds": secret_kinds})
            if hard_block:
                log.info("hard_block_forcing_local", extra={})
                force_local = True
            else:
                messages = sanitized

    def finish_local() -> dict[str, Any]:
        if response_model is None:
            if relay is None:  # positional stubs of the local path keep working
                return _run_local_or_raise(messages, json_mode, task)
            return _run_local_or_raise(messages, json_mode, task, relay)
        return _run_local_structured(messages, task, response_model, schema)

    if force_local or not cloud_enabled():
        log.info("local_only_mode", extra={
            "reason": "force_local" if force_local else "ALLOW_CLOUD_disabled",
        })
        return finish_local()

    chain = TASK_PROVIDERS.get(task, DEFAULT_CHAIN) if task else DEFAULT_CHAIN
    for source in chain:
        if source == "local":
            continue
        provider = _PROVIDER_FUNCS.get(source)
        if provider is None:
            log.info("unknown_provider_in_chain", extra={"source": source})
            continue
        skip = _skip_reason(source)
        if skip is not None:
            reason, detail = skip
            log.info("provider_skipped", extra={"source": source, "reason": reason, **detail})
            continue

        def call(msgs: list[dict[str, str]], source: str = source,
                 provider: Callable[..., ProviderReply] = provider) -> ProviderReply:
            """One counted, traced request (re-asks included)."""
            started = time.perf_counter()
            if relay is not None:
                relay.begin_attempt()
            try:
                reply = _call_provider(provider, msgs, json_mode, schema,
                                       relay.feed if relay is not None else None)
                if relay is not None:
                    relay.finish()
                    _log_first_token(source, relay, started)
            except Exception as error:
                if not _is_not_configured(error):
                    _record_failure(source, error)
                log.info("provider_failed", extra={"source": source, "error": _safe_error_text(error)})
                raise
            _record_success(source, reply)
            log.info("provider_response", extra={"source": source})
            _log_gen_ai_call(source, reply, started, task)
            return reply

        try:
            if response_model is None:
                reply = call(messages)
                cleaned_answer = strip_thinking_tags(reply.text) if not json_mode else reply.text
                return _result(cleaned_answer, source, reply)
            outcome = _structured_from(source, call, messages, response_model)
        except Exception:
            continue  # already recorded and logged by call()
        if outcome is not None:
            data, reply = outcome
            return {**_result(reply.text, source, reply), "data": data}

    return finish_local()


def generate_chat(
    messages: list[dict[str, str]],
    json_mode: bool = False,
    force_local: bool = False,
    task: str | None = None,
    stream: StreamSink | None = None,
) -> dict[str, Any]:
    """Generate a response using a task-aware provider chain.

    Returns {"answer", "source", "model", "usage": {"input_tokens", "output_tokens"}}.
    With `stream`, a plain-text reply is also delivered to the sink as it is
    generated (ROADMAP D2); JSON replies never stream.
    """
    return _dispatch(messages, json_mode, force_local, task, stream=stream)


def generate_structured(
    messages: list[dict[str, str]],
    response_model: type[BaseModel],
    task: str | None = None,
    force_local: bool = False,
    cache: bool = False,
) -> dict[str, Any]:
    """Like generate_chat, but the reply is constrained to and validated against
    `response_model` (ROADMAP F2). Adds "data": a validated instance.

    Each provider gets its native schema mode where it has one, and one re-ask
    with the validation error before the chain moves on. Raises
    StructuredOutputError if no provider, local included, produces valid data.

    cache=True reuses a stored result for byte-identical input (ROADMAP F5). Only
    for deterministic sub-tasks; see llm_cache.
    """
    use_cache = cache and not force_local and llm_cache.enabled()
    cache_key = llm_cache.key(task, response_model, messages) if use_cache else ""
    if use_cache:
        hit = llm_cache.get(cache_key)
        if hit is not None:
            try:
                data = response_model.model_validate_json(hit["data"])
            except ValueError:
                data = None  # stored under an older schema shape: a miss
            if data is not None:
                log.info("llm_cache_hit", extra={"task": task, "schema": response_model.__name__})
                return {"answer": hit["data"], "source": "cache", "model": hit["model"],
                        "usage": {"input_tokens": 0, "output_tokens": 0}, "data": data}

    result = _dispatch(messages, True, force_local, task, response_model)
    # A local-fallback answer is weaker; don't lock it in.
    if use_cache and result["source"] != "local":
        llm_cache.put(cache_key, result["data"].model_dump_json(), result["source"], result.get("model"))
    return result


if __name__ == "__main__":
    result = generate_chat([{"role": "user", "content": "Reply with a short hello."}])
    print(f"source={result['source']}\n{result['answer']}")