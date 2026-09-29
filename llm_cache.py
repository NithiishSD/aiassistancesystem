"""Exact-match cache for deterministic structured LLM sub-tasks (ROADMAP F5).

Only byte-identical requests hit. Callers opt in per call, and only for tasks whose
answer depends on nothing but the prompt (fact canonicalization, argument extraction).
Never use it for anything touching memory retrieval, time, calendar, or the web.

Every failure is a miss: the cache can slow nothing down but itself and can never
fail a request.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

from pydantic import BaseModel

from llm_schemas import json_schema_for
from zedek_logger import get_logger

log = get_logger("llm_cache")

CACHE_VERSION = 1
TTL_SECONDS = 30 * 24 * 3600
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "llm_cache.sqlite3")


def enabled() -> bool:
    return os.getenv("LLM_CACHE", "on").strip().lower() not in ("off", "false", "0", "no")


def _path() -> str:
    return os.getenv("ZEDEK_LLM_CACHE_PATH") or DEFAULT_PATH


def _connect() -> sqlite3.Connection:
    path = _path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path, timeout=2)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entries ("
        "key TEXT PRIMARY KEY, data TEXT NOT NULL, source TEXT NOT NULL, "
        "model TEXT, created REAL NOT NULL)"
    )
    return conn


def key(task: str | None, response_model: type[BaseModel], messages: list[dict[str, str]]) -> str:
    payload = {
        "v": CACHE_VERSION,
        "task": task,
        "schema": response_model.__name__,
        "json_schema": json_schema_for(response_model),
        "messages": messages,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def get(cache_key: str) -> dict[str, Any] | None:
    """{"data", "source", "model"} for a live entry, else None."""
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT data, source, model, created FROM entries WHERE key = ?", (cache_key,)
            ).fetchone()
            if row is None:
                return None
            if time.time() - row[3] > TTL_SECONDS:
                with conn:
                    conn.execute("DELETE FROM entries WHERE key = ?", (cache_key,))
                return None
            return {"data": row[0], "source": row[1], "model": row[2]}
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        log.info("llm_cache_error", extra={"op": "get", "error": type(error).__name__})
        return None


def put(cache_key: str, data_json: str, source: str, model: str | None) -> None:
    try:
        conn = _connect()
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO entries (key, data, source, model, created) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (cache_key, data_json, source, model, time.time()),
                )
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as error:
        log.info("llm_cache_error", extra={"op": "put", "error": type(error).__name__})
