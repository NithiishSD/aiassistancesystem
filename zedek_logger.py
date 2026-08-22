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

import logging
import os
from pythonjsonlogger import jsonlogger

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)


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
