"""
Phase 2: Structured logging module.

Every module (orchestrator, agents, tier gate, watchdog, security module)
imports and uses this instead of print() or ad-hoc logging. Logs are
JSON lines — one JSON object per line — so they're easy to grep, parse,
and later feed into the eval set / debugging tools.

Usage:
    from jarvis_logger import get_logger

    log = get_logger("orchestrator")
    log.info("task_dispatched", extra={"task_id": "abc123", "agent": "coding"})
"""

import contextlib
import contextvars
import logging
import os
import uuid
from typing import Iterator

from pythonjsonlogger import jsonlogger

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ── Per-turn tracing ─────────────────────────────────────────────────────────
# One user message touches several modules, each logging to its own file. A
# trace_id set for the duration of the turn is stamped on every record by a
# filter, so the whole turn can be grepped across logs/*.log without passing
# an id through every function. asyncio.run() copies context, so MCP client
# calls inherit it; a new thread would need contextvars.copy_context().run().
SESSION_ID = uuid.uuid4().hex  # one per process; OTel gen_ai.conversation.id
_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)


def current_trace_id() -> str | None:
    return _trace_id.get()


@contextlib.contextmanager
def trace_context() -> Iterator[str]:
    """Mark the enclosed code as one user turn; yields its trace id."""
    trace_id = uuid.uuid4().hex[:16]
    token = _trace_id.set(trace_id)
    try:
        yield trace_id
    finally:
        _trace_id.reset(token)


class _TraceFilter(logging.Filter):
    """Adds trace_id and gen_ai.conversation.id, only while a turn is active."""

    def filter(self, record: logging.LogRecord) -> bool:
        trace_id = _trace_id.get()
        if trace_id is not None:
            record.trace_id = trace_id
            record.__dict__["gen_ai.conversation.id"] = SESSION_ID
        return True


def get_logger(module_name: str) -> logging.Logger:
    """
    Returns a logger that writes structured JSON lines to:
        logs/<module_name>.log

    Each log entry automatically includes: timestamp, level, module name,
    and any extra fields passed via `extra={...}`.
    """
    logger = logging.getLogger(module_name)

    if logger.handlers:
        # Already configured (avoids duplicate handlers if called twice)
        return logger

    logger.setLevel(logging.INFO)

    log_path = os.path.join(LOG_DIR, f"{module_name}.log")
    file_handler = logging.FileHandler(log_path)

    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "name": "module"},
    )
    # On the logger, not the handlers, so every handler (including ones
    # attached later) sees the trace fields.
    logger.addFilter(_TraceFilter())

    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Also print to console while developing — remove/reduce once stable
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


if __name__ == "__main__":
    # Quick self-test
    log = get_logger("test")
    log.info("logging_module_initialized", extra={"phase": 2, "status": "ok"})
    print(f"\nCheck {LOG_DIR}/test.log for the JSON line that was just written.")
