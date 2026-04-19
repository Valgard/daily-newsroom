"""Status command: overview of auth, DB, sources, recent activity."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

BERLIN_TZ = ZoneInfo("Europe/Berlin")

FETCH_INTERVAL_MIN = 5
MORNING_TICK_HOUR = 7
EVENING_TICK_HOUR = 20

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


def _format_relative_time(delta_seconds: float) -> str:
    """Render a future-delta as 'overdue' / '<1min' / 'Nmin' / 'Xh Ymin'."""
    if delta_seconds <= 0:
        return "overdue"
    if delta_seconds < SECONDS_PER_MINUTE:
        return "<1min"
    if delta_seconds < SECONDS_PER_HOUR:
        return f"{int(delta_seconds // SECONDS_PER_MINUTE)}min"
    hours = int(delta_seconds // SECONDS_PER_HOUR)
    minutes = int((delta_seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE)
    return f"{hours}h {minutes}min"


def _next_fetch_for(source_row: Any, now: datetime) -> str:
    """Describe when the given source is next eligible for fetching."""
    disabled_until = source_row["disabled_until"]
    if disabled_until:
        try:
            until = datetime.fromisoformat(disabled_until).astimezone(UTC)
            if until > now:
                delta = (until - now).total_seconds()
                return f"disabled {_format_relative_time(delta)}"
        except ValueError:
            pass

    last_checked = source_row["last_checked_at"]
    if last_checked is None:
        return "ready"

    try:
        last = datetime.fromisoformat(last_checked).astimezone(UTC)
    except ValueError:
        return "ready"

    next_at = last + timedelta(seconds=source_row["interval_seconds"])
    delta = (next_at - now).total_seconds()
    return _format_relative_time(delta)


def _describe_next(now_berlin: datetime, target: datetime) -> str:
    """Render a future Berlin-local datetime as 'heute HH:MM' / 'morgen HH:MM' / 'DD.MM. HH:MM'."""
    today = now_berlin.date()
    tomorrow = today + timedelta(days=1)
    if target.date() == today:
        return f"heute {target.strftime('%H:%M')}"
    if target.date() == tomorrow:
        return f"morgen {target.strftime('%H:%M')}"
    return target.strftime("%d.%m. %H:%M")


def _next_launchd_ticks(now_berlin: datetime) -> str:
    """Describe the next scheduled launchd fires — fetch + both digests."""

    def _next_daily(hour: int) -> datetime:
        today_at = now_berlin.replace(hour=hour, minute=0, second=0, microsecond=0)
        return today_at if now_berlin < today_at else today_at + timedelta(days=1)

    return (
        f"fetch ≤{FETCH_INTERVAL_MIN}min · "
        f"digest-morning {_describe_next(now_berlin, _next_daily(MORNING_TICK_HOUR))} · "
        f"digest-evening {_describe_next(now_berlin, _next_daily(EVENING_TICK_HOUR))}"
    )


def print_status(state, *, console: Console) -> None:
    console.print(Panel.fit("daily-newsroom · Status", style="bold"))

    # Auth
    creds = _read_keychain_oauth()
    if creds:
        console.print(
            f"Auth: ✓ {creds.get('subscriptionType', '?')} (tier {creds.get('rateLimitTier', '?')})"
        )
    else:
        console.print("Auth: [red]✗ no Claude Code credentials found[/red]")

    # DB
    db_size_mb = (
        Path(state.db_path).stat().st_size / 1024 / 1024 if Path(state.db_path).exists() else 0
    )
    today = datetime.now().date().isoformat()
    stats = state.get_daily_stats(today)
    console.print(f"State-DB: ✓ {db_size_mb:.1f} MB, {stats['total']} items")

    # Logs
    log_dir = Path.home() / "Library" / "Logs" / "newsroom"
    log_size = (
        sum(f.stat().st_size for f in log_dir.glob("*.log")) / 1024 / 1024
        if log_dir.exists()
        else 0
    )
    console.print(f"Logs: ~/Library/Logs/newsroom/ ({log_size:.1f} MB)")

    # Next launchd ticks
    console.print(f"Next launchd: {_next_launchd_ticks(datetime.now(BERLIN_TZ))}")

    # Sources table
    table = Table(title="Sources")
    table.add_column("Name")
    table.add_column("Last fetched")
    table.add_column("Next fetch")
    table.add_column("Errors")
    table.add_column("Status")
    now_utc = datetime.now(UTC)
    for src in state.list_enabled_sources():
        last = src["last_fetched_at"]
        age = "never"
        if last:
            try:
                delta = now_utc - datetime.fromisoformat(last).astimezone(UTC)
                age = f"{int(delta.total_seconds() / 60)} min ago"
            except ValueError:
                age = last
        next_fetch = _next_fetch_for(src, now_utc)
        errors = src["consecutive_errors"]
        status = "✓" if errors == 0 else "⚠"
        table.add_row(src["name"], age, next_fetch, str(errors), status)
    console.print(table)

    # Today's activity
    console.print(
        f"\nToday: {stats['fetched_today']} fetched, "
        f"{stats['scored_high_today']} scored ≥4, "
        f"{stats['notified_today']} pushed"
    )

    last_morning = state.get_digest(today, "morning")
    last_evening = state.get_digest(today, "evening")
    if last_morning:
        console.print(f"Morning digest: ✓ {last_morning['item_count']} items")
    if last_evening:
        console.print(f"Evening digest: ✓ {last_evening['item_count']} items")


def _read_keychain_oauth() -> dict | None:
    """Fetch the Claude Code OAuth record from macOS Keychain (read-only)."""
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        return json.loads(raw).get("claudeAiOauth", {})
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
