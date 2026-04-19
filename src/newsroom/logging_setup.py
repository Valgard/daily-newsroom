"""Logging configuration — Rich for stdout, JSONL for structured events."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler

LOG_DIR = Path.home() / "Library" / "Logs" / "newsroom"
JSONL_PATH = LOG_DIR / "events.jsonl"

MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB before rotation
LOG_BACKUP_COUNT = 4


class JsonlFormatter(logging.Formatter):
    """One-JSON-per-line formatter; enables `grep`/`jq` analysis."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, verbose: bool = False) -> None:
    """Set up Rich stdout + JSONL file handlers on the root logger. Idempotent."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Avoid duplicating our own handlers on repeat calls
    already_configured = any(
        isinstance(h, (RichHandler, RotatingFileHandler)) for h in root.handlers
    )
    if already_configured:
        return

    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        show_time=True,
    )
    rich_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(rich_handler)

    jsonl_handler = RotatingFileHandler(
        JSONL_PATH, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    jsonl_handler.setFormatter(JsonlFormatter())
    jsonl_handler.setLevel(logging.INFO)
    root.addHandler(jsonl_handler)
