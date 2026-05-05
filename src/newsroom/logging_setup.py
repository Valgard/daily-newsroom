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


_RESERVED_LOG_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


class JsonlFormatter(logging.Formatter):
    """One-JSON-per-line formatter; enables `grep`/`jq` analysis.

    Standard fields (ts, level, logger, message) are always emitted. Any
    `extra={...}` kwargs passed to logger.info/etc. are merged into the same
    JSON object — internal LogRecord attributes (filename, lineno, threadName,
    …) are filtered out so the output stays a clean event stream.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS:
                payload[key] = val
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _utc_time_format(dt: datetime) -> str:
    """Render Rich log timestamps in UTC so fetch.log matches DB / events.jsonl.

    Rich passes a timezone-aware datetime in the local zone; we coerce to UTC
    before formatting. Without this, fetch.log shows '08:09 CEST' while the same
    event lands as '06:09 UTC' in the DB — a 2 h gap that makes cross-source
    diagnostics misleading.
    """
    return dt.astimezone(UTC).strftime("[%Y-%m-%d %H:%M:%S]")


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
        log_time_format=_utc_time_format,
    )
    rich_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(rich_handler)

    jsonl_handler = RotatingFileHandler(
        JSONL_PATH, maxBytes=MAX_LOG_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
    )
    jsonl_handler.setFormatter(JsonlFormatter())
    jsonl_handler.setLevel(logging.INFO)
    root.addHandler(jsonl_handler)
