"""Tests for logging setup."""

import json
import logging
from pathlib import Path

import pytest

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
