"""Cloud-first chat provider adapter with a local Ollama fallback.

Providers are tried in order until one succeeds. NVIDIA and OpenRouter
resolve their model dynamically against each provider's live catalog,
since free-tier model availability rotates frequently.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import requests
from dotenv import load_dotenv

import ollama
from zedek_logger import get_logger

load_dotenv()

log = get_logger("llm_provider")
LOCAL_MODEL = "llama3.1:8b"
REQUEST_TIMEOUT = 45
MODEL_CACHE_TTL = 3600  # re-check live catalogs at most once an hour

# Preferred model order per provider — first one found live in the
# provider's current catalog wins. Update these lists as the free-tier
# landscape shifts; the resolver does the rest.
NVIDIA_CODING_CANDIDATES = [
    "deepseek-ai/deepseek-v4-flash-0731",
    "mistralai/mistral-nemotron",
]
OPENROUTER_CODING_CANDIDATES = [
    "z-ai/glm-5.2:free",
    "deepseek/deepseek-v4-flash:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "z-ai/glm-4.5-air:free",
]

TASK_PROVIDERS: dict[str, list[str]] = {
    "coding": ["nvidia_nim", "openrouter", "groq", "local"],
    "general_qa": ["gemini", "groq", "cerebras", "local"],
    "fact_handling": ["gemini", "groq", "local"],
    "process_reasoning": ["openrouter", "gemini", "cerebras", "local"],
}
DEFAULT_CHAIN = ["gemini", "groq", "nvidia_nim", "openrouter", "cerebras", "local"]

_model_cache: dict[str, tuple[float, list[str]]] = {}


class AllProvidersUnavailableError(RuntimeError):
    """Raised when all configured providers, including local Ollama, fail."""


def sanitize_messages_for_cloud(
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[str], bool]:
    """Placeholder for the future LLM-backed security scanner.

    The security model is not implemented yet, so this intentionally grants
    cloud coding providers full access to the original messages. Keep this
    stable boundary so the future scanner can add findings, redaction, and
    local-only decisions without changing provider routing.
    """
    return [dict(message) for message in messages], [], False


def cloud_enabled() -> bool:
    """Whether cloud providers should be tried at all.

    Controlled by the ALLOW_CLOUD env var (defaults to enabled).
    Set ALLOW_CLOUD=false / 0 / no in your .env, or export it in the
    shell before running, to force local-only mode without touching code.
    """
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
        log.info("model_list_fetch_failed", extra={"provider": key, "error": str(error)})
        # Serve stale cache if we have one rather than failing outright
        return cached[1] if cached else []


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


def resolve_nvidia_model() -> str:
    live = _cached_fetch("nvidia", _fetch_nvidia_models)
    for candidate in NVIDIA_CODING_CANDIDATES:
        if candidate in live:
            return candidate
    # Nothing on our shortlist is live — fall back to whatever NVIDIA has,
    # or the first candidate anyway and let the call fail loudly.
    return live[0] if live else NVIDIA_CODING_CANDIDATES[0]


def resolve_openrouter_model() -> str:
    live = _cached_fetch("openrouter", _fetch_openrouter_free_models)
    for candidate in OPENROUTER_CODING_CANDIDATES:
        if candidate in live:
            return candidate
    return live[0] if live else OPENROUTER_CODING_CANDIDATES[0]


def _openai_compatible(
    source: str,
    endpoint: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    json_mode: bool,
) -> str:
    if not api_key:
        raise RuntimeError(f"{source} API key is not configured")

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError(f"{source} returned a non-text response")
    return content


def _gemini(messages: list[dict[str, str]], json_mode: bool) -> str:
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

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
        params={"key": api_key},
        json={"contents": contents, "generationConfig": generation_config},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return "".join(part["text"] for part in parts)


def _groq(messages: list[dict[str, str]], json_mode: bool) -> str:
    return _openai_compatible(
        "groq",
        "https://api.groq.com/openai/v1/chat/completions",
        os.getenv("GROQ_API_KEY", ""),
        os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        messages,
        json_mode,
    )


def _nvidia_nim(messages: list[dict[str, str]], json_mode: bool) -> str:
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


def _openrouter(messages: list[dict[str, str]], json_mode: bool) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("openrouter API key is not configured")
    return _openai_compatible(
        "openrouter",
        "https://openrouter.ai/api/v1/chat/completions",
        api_key,
        resolve_openrouter_model(),
        messages,
        json_mode,
    )


def _cerebras(messages: list[dict[str, str]], json_mode: bool) -> str:
    return _openai_compatible(
        "cerebras",
        "https://api.cerebras.ai/v1/chat/completions",
        os.getenv("CEREBRAS_API_KEY", ""),
        os.getenv("CEREBRAS_MODEL", "llama3.1-8b"),
        messages,
        json_mode,
    )


def _local(messages: list[dict[str, str]], json_mode: bool) -> str:
    options: dict[str, Any] = {}
    if json_mode:
        options["format"] = "json"
    response = ollama.chat(model=LOCAL_MODEL, messages=messages, **options)
    return response["message"]["content"]


_PROVIDER_FUNCS: dict[str, Callable[[list[dict[str, str]], bool], str]] = {
    "gemini": _gemini,
    "groq": _groq,
    "nvidia_nim": _nvidia_nim,
    "openrouter": _openrouter,
    "cerebras": _cerebras,
}


def _run_local_or_raise(messages: list[dict[str, str]], json_mode: bool) -> dict[str, str]:
    try:
        answer = _local(messages, json_mode)
    except Exception as error:
        log.info("local_fallback_failed", extra={"error": str(error)})
        raise AllProvidersUnavailableError(
            "All cloud providers failed and local Ollama is unavailable. "
            f"Check `ollama serve` and `ollama pull {LOCAL_MODEL}`."
        ) from error
    log.info("provider_response", extra={"source": "local"})
    return {"answer": answer, "source": "local"}


def generate_chat(
    messages: list[dict[str, str]],
    json_mode: bool = False,
    force_local: bool = False,
    task: str | None = None,
) -> dict[str, str]:
    """Generate a response using a task-aware provider chain.

    Coding prompts pass through the security-scanner placeholder before cloud
    use. The placeholder currently leaves content unchanged; cloud coding can
    be disabled with ALLOW_CLOUD_CODING=false until the security model exists.
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
        return _run_local_or_raise(messages, json_mode)

    chain = TASK_PROVIDERS.get(task, DEFAULT_CHAIN) if task else DEFAULT_CHAIN
    for source in chain:
        if source == "local":
            continue
        provider = _PROVIDER_FUNCS.get(source)
        if provider is None:
            log.info("unknown_provider_in_chain", extra={"source": source})
            continue
        try:
            answer = provider(messages, json_mode)
            log.info("provider_response", extra={"source": source})
            return {"answer": answer, "source": source}
        except Exception as error:
            log.info("provider_failed", extra={"source": source, "error": str(error)})

    return _run_local_or_raise(messages, json_mode)


if __name__ == "__main__":
    result = generate_chat([{"role": "user", "content": "Reply with a short hello."}])
    print(f"source={result['source']}\n{result['answer']}")