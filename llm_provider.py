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
from typing import Any, Callable

import requests
from dotenv import load_dotenv

import ollama
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

# Dynamic candidate pools — first live match in provider's catalog wins
GEMINI_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.5-flash-lite",
]
GROQ_CANDIDATES = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
]
NVIDIA_CODING_CANDIDATES = [
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "mistralai/mistral-nemotron",
]
OPENROUTER_CODING_CANDIDATES = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-2-9b-it:free",
    "qwen/qwen-2.5-72b-instruct:free",
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


def resolve_gemini_model() -> str:
    live = _cached_fetch("gemini", _fetch_gemini_models)
    for candidate in GEMINI_CANDIDATES:
        if candidate in live:
            return candidate
    return live[0] if live else GEMINI_CANDIDATES[0]


def resolve_groq_model() -> str:
    live = _cached_fetch("groq", _fetch_groq_models)
    for candidate in GROQ_CANDIDATES:
        if candidate in live:
            return candidate
    return live[0] if live else GROQ_CANDIDATES[0]


def resolve_nvidia_model() -> str:
    live = _cached_fetch("nvidia", _fetch_nvidia_models)
    for candidate in NVIDIA_CODING_CANDIDATES:
        if candidate in live:
            return candidate
    return live[0] if live else NVIDIA_CODING_CANDIDATES[0]


def resolve_openrouter_model() -> str:
    live = _cached_fetch("openrouter", _fetch_openrouter_free_models)
    for candidate in OPENROUTER_CODING_CANDIDATES:
        if candidate in live:
            return candidate
    return live[0] if live else OPENROUTER_CODING_CANDIDATES[0]


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


def _openai_compatible(
    source: str,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
    extra_headers: dict[str, str] | None = None,
) -> ProviderReply:
    if not api_key:
        raise RuntimeError(f"{source} API key is not configured")

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

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

def _gemini(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    contents = []
    for message in messages:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message["content"]}]})

    generation_config: dict[str, str] = {}
    if json_mode:
        generation_config["responseMimeType"] = "application/json"

    model_name = os.getenv("GEMINI_MODEL") or resolve_gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"

    # Try up to 2 times to handle transient 503/500 errors
    for attempt in range(2):
        try:
            response = requests.post(
                url,
                params={"key": api_key},
                json={"contents": contents, "generationConfig": generation_config},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
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

def _groq(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    model = os.getenv("GROQ_MODEL") or resolve_groq_model()
    return _openai_compatible(
        "groq",
        "https://api.groq.com/openai/v1/chat/completions",
        os.getenv("GROQ_API_KEY", ""),
        model,
        messages,
        json_mode,
    )


def _nvidia_nim(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    api_key = os.getenv("NVIDIA_API_KEY", "")
    if not api_key:
        raise RuntimeError("nvidia_nim API key is not configured")
    return _openai_compatible(
        "nvidia_nim",
        "https://integrate.api.nvidia.com/v1/chat/completions",
        api_key,
        resolve_nvidia_model(),
        messages,
        json_mode,
    )


def _openrouter(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_KEY", "")
    if not api_key:
        raise RuntimeError("openrouter API key is not configured")

    model = os.getenv("OPENROUTER_MODEL") or resolve_openrouter_model()

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
    )


def _cerebras(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    return _openai_compatible(
        "cerebras",
        "https://api.cerebras.ai/v1/chat/completions",
        os.getenv("CEREBRAS_API_KEY", ""),
        os.getenv("CEREBRAS_MODEL", "llama-3.3-70b"),
        messages,
        json_mode,
    )


def _field(obj: Any, key: str) -> Any:
    # ollama 0.3 returns a dict; newer clients return an object that also
    # supports item access. Missing fields are None, never an error.
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return getattr(obj, key, None)


def _local(messages: list[dict[str, str]], json_mode: bool) -> ProviderReply:
    options: dict[str, Any] = {}
    if json_mode:
        options["format"] = "json"
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
    if status in (401, 403):
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
                        task: str | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        reply = _local(messages, json_mode)
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


def generate_chat(
    messages: list[dict[str, str]],
    json_mode: bool = False,
    force_local: bool = False,
    task: str | None = None,
) -> dict[str, Any]:
    """Generate a response using a task-aware provider chain.

    Returns {"answer", "source", "model", "usage": {"input_tokens", "output_tokens"}}.
    """
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

    if force_local or not cloud_enabled():
        log.info("local_only_mode", extra={
            "reason": "force_local" if force_local else "ALLOW_CLOUD_disabled",
        })
        return _run_local_or_raise(messages, json_mode, task)

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
        started = time.perf_counter()
        try:
            reply = provider(messages, json_mode)
        except Exception as error:
            if not _is_not_configured(error):
                _record_failure(source, error)
            log.info("provider_failed", extra={"source": source, "error": _safe_error_text(error)})
            continue
        _record_success(source, reply)
        cleaned_answer = strip_thinking_tags(reply.text) if not json_mode else reply.text
        log.info("provider_response", extra={"source": source})
        _log_gen_ai_call(source, reply, started, task)
        return _result(cleaned_answer, source, reply)

    return _run_local_or_raise(messages, json_mode, task)


if __name__ == "__main__":
    result = generate_chat([{"role": "user", "content": "Reply with a short hello."}])
    print(f"source={result['source']}\n{result['answer']}")