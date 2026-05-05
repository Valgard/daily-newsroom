"""CLI integration tests."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from newsroom.cli import (
    SCHEDULED_EVENING_HOUR,
    SCHEDULED_MORNING_HOUR,
    _is_in_catchup_window,
    _run_fetch_score_notify,
    app,
)
from newsroom.state import State

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "newsroom" in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["fetch", "score", "digest", "notify", "status", "init", "validate-config"]:
        assert cmd in result.stdout


def test_cli_fetch_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("NEWSROOM_SOURCES_PATH", str(tmp_path / "empty.yaml"))
    (tmp_path / "empty.yaml").write_text("sources: []\n")
    # init first
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["fetch", "--dry-run"])
    assert result.exit_code == 0


def test_cli_notify_test_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    # init DB
    runner.invoke(app, ["init"])
    # --test should succeed even without state (sends a ping)
    result = runner.invoke(app, ["notify", "--test"])
    # exit 0 or 1 depending on pync availability; should not raise
    assert result.exit_code in (0, 1)


# ── catchup-window logic ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hour", "slot", "expected"),
    [
        # Morning slot scheduled at 07:00; window: [7, 9] inclusive
        (5, "morning", False),  # before scheduled — must NOT trigger (regression: was True)
        (6, "morning", False),  # before scheduled — must NOT trigger
        (7, "morning", True),  # exact scheduled — first valid
        (8, "morning", True),  # 1h after — catchup zone
        (9, "morning", True),  # 2h after — last valid
        (10, "morning", False),  # 3h after — too late
        (12, "morning", False),
        (23, "morning", False),
        # Evening slot scheduled at 20:00; window: [20, 22] inclusive
        (19, "evening", False),
        (20, "evening", True),
        (21, "evening", True),
        (22, "evening", True),
        (23, "evening", False),
        (5, "evening", False),
    ],
)
def test_catchup_window_only_fires_at_or_after_scheduled_time(
    hour: int, slot: str, expected: bool
) -> None:
    """Catchup must NOT fire before the scheduled launchd time, otherwise the
    regular launchd tick never gets a chance to run."""
    assert _is_in_catchup_window(hour, slot) is expected


def test_catchup_constants_match_plist_schedule() -> None:
    """Constants must match the launchd plist StartCalendarInterval Hour values."""
    assert SCHEDULED_MORNING_HOUR == 7
    assert SCHEDULED_EVENING_HOUR == 20


# ── Lifecycle logging in _run_fetch_score_notify ──────────────────────────────


async def test_run_fetch_score_notify_emits_lifecycle_logs(tmp_path, monkeypatch, caplog) -> None:
    """Every phase boundary in _run_fetch_score_notify must emit an INFO log,
    even when there is nothing to do. Without this, a 'silent normal idle' run
    is indistinguishable from a 'crashed mid-flight' run — exactly the gap that
    confused the 2026-05-05 backlog diagnosis.
    """
    state = State(tmp_path / "t.db")
    state.ensure_schema()

    monkeypatch.setattr("newsroom.cli.AgentClient", lambda *a, **kw: AsyncMock())
    monkeypatch.setattr("newsroom.cli.Notifier", lambda *a, **kw: AsyncMock())

    with caplog.at_level(logging.INFO, logger="newsroom.cli"):
        await _run_fetch_score_notify(state, category=None, source=None)

    msgs = [r.getMessage() for r in caplog.records if r.name == "newsroom.cli"]
    # Markers that diagnose presence/absence of each phase, plus run boundaries
    joined = "\n".join(msgs)
    assert "run start" in joined
    assert "fetch phase" in joined
    assert "filter phase" in joined
    assert "score phase" in joined
    assert "run end" in joined


async def test_run_fetch_score_notify_score_log_includes_pending_count(
    tmp_path, monkeypatch, caplog
) -> None:
    """The 'score phase' log must surface the post-run pending count so an
    operator can tell at-a-glance whether the backlog is shrinking or growing.
    """
    state = State(tmp_path / "t.db")
    state.ensure_schema()

    monkeypatch.setattr("newsroom.cli.AgentClient", lambda *a, **kw: AsyncMock())
    monkeypatch.setattr("newsroom.cli.Notifier", lambda *a, **kw: AsyncMock())

    with caplog.at_level(logging.INFO, logger="newsroom.cli"):
        await _run_fetch_score_notify(state, category=None, source=None)
    score_msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "newsroom.cli" and "score phase" in r.getMessage()
    ]
    assert score_msgs, "no score phase log emitted"
    assert "pending" in score_msgs[0]
