"""Tests for logging setup."""

import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from rich.logging import RichHandler

from newsroom import logging_setup


@pytest.fixture
def temp_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(logging_setup, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logging_setup, "JSONL_PATH", tmp_path / "events.jsonl")
    # Clear any existing root handlers from prior tests
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    yield tmp_path
    # Teardown: remove handlers added during test
    for h in list(root.handlers):
        root.removeHandler(h)


def test_configure_logging_creates_jsonl_file(temp_log_dir: Path) -> None:
    logging_setup.configure_logging()
    logger = logging.getLogger("test.newsroom")
    logger.info("test message")

    jsonl_file = temp_log_dir / "events.jsonl"
    assert jsonl_file.exists()
    line = jsonl_file.read_text().strip().split("\n")[-1]
    record = json.loads(line)
    assert record["message"] == "test message"
    assert record["level"] == "INFO"
    assert record["logger"] == "test.newsroom"


def test_configure_logging_is_idempotent(temp_log_dir: Path) -> None:
    logging_setup.configure_logging()
    handlers_after_first = len(logging.getLogger().handlers)
    logging_setup.configure_logging()
    handlers_after_second = len(logging.getLogger().handlers)
    assert handlers_after_first == handlers_after_second


def test_utc_time_format_converts_local_to_utc() -> None:
    """RichHandler's time-format helper renders UTC, not local-time, so fetch.log
    matches DB and events.jsonl. Without this, a CEST 08:09 entry was stored as
    UTC 06:09 in DB — making cross-source diagnostics misleading.
    """
    cest = ZoneInfo("Europe/Berlin")
    sample = datetime(2026, 5, 5, 8, 9, 42, tzinfo=cest)  # CEST = UTC+02:00
    rendered = logging_setup._utc_time_format(sample)
    assert "06:09:42" in str(rendered)
    assert "08:09" not in str(rendered)


def test_configure_logging_attaches_utc_time_format_to_rich_handler(
    temp_log_dir: Path,
) -> None:
    """The configured RichHandler must use _utc_time_format, not the default
    locale-aware '[%x %X]' which yields local time."""
    logging_setup.configure_logging()
    rich = next(h for h in logging.getLogger().handlers if isinstance(h, RichHandler))
    fmt = rich._log_render.time_format
    assert fmt is logging_setup._utc_time_format
