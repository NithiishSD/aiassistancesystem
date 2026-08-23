"""Cloud-first chat provider adapter with a local Ollama fallback."""

from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

import ollama
from zedek_logger import get_logger

load_dotenv()

log = get_logger("llm_provider")
LOCAL_MODEL = "llama3.1:8b"
REQUEST_TIMEOUT = 45


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

    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
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


def generate_chat(messages: list[dict[str, str]], json_mode: bool = False) -> dict[str, str]:
    """Generate a response using the first configured provider that succeeds."""
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
                os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
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
                os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
                messages,
                json_mode,
            ),
        ),
        (
            "github_models",
            lambda: _openai_compatible(
                "github_models",
                "https://models.inference.ai.azure.com/chat/completions",
                os.getenv("GITHUB_TOKEN", ""),
                os.getenv("GITHUB_MODEL", "Meta-Llama-3.1-8B-Instruct"),
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
