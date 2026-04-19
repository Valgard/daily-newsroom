"""Status command: overview of auth, DB, sources, recent activity."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


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

    # Sources table
    table = Table(title="Sources")
    table.add_column("Name")
    table.add_column("Last fetched")
    table.add_column("Errors")
    table.add_column("Status")
    now = datetime.now()
    for src in state.list_enabled_sources():
        last = src["last_fetched_at"]
        age = "never"
        if last:
            try:
                delta = now.astimezone() - datetime.fromisoformat(last)
                age = f"{int(delta.total_seconds() / 60)} min ago"
            except ValueError:
                age = last
        errors = src["consecutive_errors"]
        status = "✓" if errors == 0 else "⚠"
        table.add_row(src["name"], age, str(errors), status)
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
