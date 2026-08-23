"""Cloud-first chat provider adapter with a local Ollama fallback.

Providers are tried in order until one succeeds. NVIDIA and OpenRouter
resolve their model dynamically against each provider's live catalog,
since free-tier model availability rotates frequently.
"""

from __future__ import annotations

import os
import time
from typing import Any

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

_model_cache: dict[str, tuple[float, list[str]]] = {}


def cloud_enabled() -> bool:
    """Whether cloud providers should be tried at all.

    Controlled by the ALLOW_CLOUD env var (defaults to enabled).
    Set ALLOW_CLOUD=false / 0 / no in your .env, or export it in the
    shell before running, to force local-only mode without touching code.
    """
    return os.getenv("ALLOW_CLOUD", "true").strip().lower() not in ("false", "0", "no")


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


def _local(messages: list[dict[str, str]], json_mode: bool) -> str:
    options: dict[str, Any] = {}
    if json_mode:
        options["format"] = "json"
    response = ollama.chat(model=LOCAL_MODEL, messages=messages, **options)
    return response["message"]["content"]


def generate_chat(
    messages: list[dict[str, str]],
    json_mode: bool = False,
    force_local: bool = False,
) -> dict[str, str]:
    """Generate a response using the first configured provider that succeeds.

    Cloud providers are skipped entirely (going straight to local Ollama)
    when force_local=True, or when ALLOW_CLOUD is turned off via env var —
    see cloud_enabled(). Use this to go fully offline/private on demand,
    e.g. for sensitive personal-agent tasks, without editing this file.
    """
    if force_local or not cloud_enabled():
        answer = _local(messages, json_mode)
        log.info("provider_response", extra={"source": "local", "cloud_disabled": True})
        return {"answer": answer, "source": "local"}

    providers = [
        (
            "gemini",
            lambda: _gemini(messages, json_mode),
        ),
        (
            "groq",
            lambda: _openai_compatible(
                "groq",
                "https://api.groq.com/openai/v1/chat/completions",
                os.getenv("GROQ_API_KEY", ""),
                os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                messages,
                json_mode,
            ),
        ),
        (
            "nvidia_nim",
            lambda: _openai_compatible(
                "nvidia_nim",
                "https://integrate.api.nvidia.com/v1/chat/completions",
                os.getenv("NVIDIA_API_KEY", ""),
                resolve_nvidia_model(),
                messages,
                json_mode,
            ),
        ),
        (
            "openrouter",
            lambda: _openai_compatible(
                "openrouter",
                "https://openrouter.ai/api/v1/chat/completions",
                os.getenv("OPENROUTER_API_KEY", ""),
                resolve_openrouter_model(),
                messages,
                json_mode,
            ),
        ),
        (
            "cerebras",
            lambda: _openai_compatible(
                "cerebras",
                "https://api.cerebras.ai/v1/chat/completions",
                os.getenv("CEREBRAS_API_KEY", ""),
                os.getenv("CEREBRAS_MODEL", "llama3.1-8b"),
                messages,
                json_mode,
            ),
        ),
    ]

    for source, provider in providers:
        try:
            answer = provider()
            log.info("provider_response", extra={"source": source})
            return {"answer": answer, "source": source}
        except Exception as error:
            log.info("provider_failed", extra={"source": source, "error": str(error)})

    answer = _local(messages, json_mode)
    log.info("provider_response", extra={"source": "local"})
    return {"answer": answer, "source": "local"}


if __name__ == "__main__":
    result = generate_chat([{"role": "user", "content": "Reply with a short hello."}])
    print(f"source={result['source']}\n{result['answer']}")