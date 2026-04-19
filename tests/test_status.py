"""Tests for status module: smoke + format helpers + next-fetch/ticks logic."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from freezegun import freeze_time
from rich.console import Console

from newsroom.config import Source
from newsroom.state import State
from newsroom.status import (
    _describe_next,
    _format_relative_time,
    _next_fetch_for,
    _next_launchd_ticks,
    print_status,
)

BERLIN = ZoneInfo("Europe/Berlin")


def test_print_status_does_not_crash_on_empty_state(tmp_path: Path, capsys) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    console = Console(force_terminal=False, no_color=True, width=80)
    print_status(state, console=console)
    # Just verify it ran — actual content varies by environment (keychain, log dir)
    assert True  # no exception == pass


# ── _format_relative_time ────────────────────────────────────────────


def test_format_relative_time_overdue() -> None:
    assert _format_relative_time(-10) == "overdue"


def test_format_relative_time_zero_is_overdue() -> None:
    assert _format_relative_time(0) == "overdue"


def test_format_relative_time_seconds() -> None:
    assert _format_relative_time(30) == "<1min"


def test_format_relative_time_minutes() -> None:
    assert _format_relative_time(42 * 60) == "42min"


def test_format_relative_time_hours_and_minutes() -> None:
    assert _format_relative_time(3 * 3600 + 15 * 60) == "3h 15min"


# ── _next_fetch_for ───────────────────────────────────────────────────


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_ready_when_never_checked() -> None:
    row = {"disabled_until": None, "last_checked_at": None, "interval_seconds": 3600}
    assert _next_fetch_for(row, datetime.now(UTC)) == "ready"


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_due_in_future() -> None:
    row = {
        "disabled_until": None,
        "last_checked_at": "2026-04-19T09:45:00+00:00",  # 15 min ago
        "interval_seconds": 3600,
    }
    # next fetch = 45 min from now
    assert _next_fetch_for(row, datetime.now(UTC)) == "45min"


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_overdue() -> None:
    row = {
        "disabled_until": None,
        "last_checked_at": "2026-04-19T08:00:00+00:00",  # 2h ago, interval 1h
        "interval_seconds": 3600,
    }
    assert _next_fetch_for(row, datetime.now(UTC)) == "overdue"


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_disabled_until_future() -> None:
    row = {
        "disabled_until": "2026-04-19T11:00:00+00:00",  # 1h in future
        "last_checked_at": "2026-04-19T09:00:00+00:00",
        "interval_seconds": 3600,
    }
    result = _next_fetch_for(row, datetime.now(UTC))
    assert "disabled" in result


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_disabled_in_past_falls_through() -> None:
    # disabled_until already passed → use normal logic
    row = {
        "disabled_until": "2026-04-19T09:00:00+00:00",
        "last_checked_at": "2026-04-19T09:45:00+00:00",
        "interval_seconds": 3600,
    }
    assert _next_fetch_for(row, datetime.now(UTC)) == "45min"


@freeze_time("2026-04-19 10:00:00")
def test_next_fetch_for_naive_iso_string() -> None:
    # Defensive: caller may have stored naive ISO (no tz)
    row = {
        "disabled_until": None,
        "last_checked_at": "2026-04-19T09:45:00",  # naive
        "interval_seconds": 3600,
    }
    # astimezone(UTC) assumes local time for naive; we still want a usable output
    result = _next_fetch_for(row, datetime.now(UTC))
    assert result in ("45min", "overdue", "ready") or result.endswith("min")


# ── _describe_next ────────────────────────────────────────────────────


def test_describe_next_same_day() -> None:
    now = datetime(2026, 4, 19, 10, 0, tzinfo=BERLIN)
    target = datetime(2026, 4, 19, 20, 0, tzinfo=BERLIN)
    assert _describe_next(now, target) == "heute 20:00"


def test_describe_next_next_day() -> None:
    now = datetime(2026, 4, 19, 22, 0, tzinfo=BERLIN)
    target = datetime(2026, 4, 20, 7, 0, tzinfo=BERLIN)
    assert _describe_next(now, target) == "morgen 07:00"


def test_describe_next_later() -> None:
    now = datetime(2026, 4, 19, 10, 0, tzinfo=BERLIN)
    target = datetime(2026, 4, 21, 7, 0, tzinfo=BERLIN)
    assert _describe_next(now, target) == "21.04. 07:00"


# ── _next_launchd_ticks ──────────────────────────────────────────────


def test_next_launchd_ticks_morning_shows_today_evening_and_tomorrow_morning() -> None:
    # 10:00 Sunday → next evening today, next morning tomorrow
    now = datetime(2026, 4, 19, 10, 0, tzinfo=BERLIN)
    result = _next_launchd_ticks(now)
    assert "fetch" in result
    assert "digest-morning morgen 07:00" in result
    assert "digest-evening heute 20:00" in result


def test_next_launchd_ticks_late_night_shows_tomorrow_for_both() -> None:
    # 22:00 → both digests are tomorrow
    now = datetime(2026, 4, 19, 22, 0, tzinfo=BERLIN)
    result = _next_launchd_ticks(now)
    assert "digest-morning morgen 07:00" in result
    assert "digest-evening morgen 20:00" in result


def test_next_launchd_ticks_before_morning_shows_today_for_both() -> None:
    # 06:30 → morning is today 07:00, evening is today 20:00
    now = datetime(2026, 4, 19, 6, 30, tzinfo=BERLIN)
    result = _next_launchd_ticks(now)
    assert "digest-morning heute 07:00" in result
    assert "digest-evening heute 20:00" in result


# ── print_status integration ────────────────────────────────────────


def test_print_status_includes_next_fetch_column_and_ticks_row(tmp_path: Path, capsys) -> None:
    """Sanity: after adding a source, the rendered output mentions both new pieces."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="anthropic",
            category="ai",
            subcategory="lab",
            url="https://www.anthropic.com/news/rss.xml",
            feed_type="rss",
            interval_seconds=3600,
        )
    )
    console = Console(force_terminal=False, no_color=True, width=120)
    with freeze_time("2026-04-19 10:00:00"):
        print_status(state, console=console)
    captured = capsys.readouterr().out
    # Sources table has Next fetch column (header or value)
    assert "Next fetch" in captured
    # Launchd-ticks panel line is present
    assert "launchd" in captured or "digest-morning" in captured
