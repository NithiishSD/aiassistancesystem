"""Cross-encoder relevance scoring for memory retrieval (ROADMAP F1).

Dense embedding distance is a poor relevance gate: it shifts with query length
and phrasing. On the cleaned live store the correct answer to "what college do I
study at" sat at L2 distance 1.06, just past the old 1.0 cutoff. A cross-encoder
reads the query and each candidate together and scores relevance directly.

Loaded once per process, from the local models/ directory only, so no network
is needed at query time. If the model is missing or fails to load, score()
returns None and callers fall back to embedding ranking. It never raises.
"""

from __future__ import annotations

import math
import os
import threading
from typing import Any

from zedek_logger import get_logger

log = get_logger("reranker")

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(_PROJECT_ROOT, "models", "cross-encoder-ms-marco-MiniLM-L-6-v2")

_model: Any = None
_load_failed = False
_lock = threading.Lock()


def _sigmoid(logit: float) -> float:
    # MS MARCO cross-encoders emit unbounded logits; squash to [0, 1] for a
    # readable, loggable threshold.
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exp_logit = math.exp(logit)
    return exp_logit / (1.0 + exp_logit)


def _load_model() -> Any:
    """Load the cross-encoder once. Returns None (and remembers) on failure."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is not None or _load_failed:
            return _model
        if not os.path.isfile(os.path.join(MODEL_DIR, "config.json")):
            _load_failed = True
            log.info("reranker_unavailable", extra={"reason": "model_not_installed", "path": MODEL_DIR})
            return None
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from sentence_transformers import CrossEncoder
            _model = CrossEncoder(MODEL_DIR, device="cpu")
            log.info("reranker_loaded", extra={"path": MODEL_DIR})
        except Exception as err:
            _load_failed = True
            log.info("reranker_unavailable", extra={
                "reason": "load_failed", "error_type": type(err).__name__,
            })
            return None
    return _model


def is_available() -> bool:
    return _load_model() is not None


def score(query: str, texts: list[str]) -> list[float] | None:
    """Relevance of each text to `query`, in [0, 1], same order as `texts`.

    Returns None when the model is unavailable so the caller can degrade.
    An empty `texts` list returns an empty list.
    """
    if not texts:
        return []
    model = _load_model()
    if model is None:
        return None
    try:
        logits = model.predict([(query or "", text or "") for text in texts])
        return [_sigmoid(float(logit)) for logit in logits]
    except Exception as err:
        log.info("reranker_score_failed", extra={"error_type": type(err).__name__})
        return None


def _reset_for_tests() -> None:
    """Clear the cached model and failure flag."""
    global _model, _load_failed
    _model = None
    _load_failed = False
