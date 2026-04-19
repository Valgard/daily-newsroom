"""CLI entry point wiring all subcommands via typer."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from newsroom import __version__
from newsroom.agent_client import AgentClient
from newsroom.config import load_sources
from newsroom.digester import NoSlotError, determine_slot, generate_digest
from newsroom.fetcher import fetch_due_sources, should_fetch
from newsroom.filter_arxiv import filter_pending_arxiv_items
from newsroom.logging_setup import configure_logging
from newsroom.notifier import (
    Notifier,
    _send_with_pync,  # dev-only test ping; private name intentional
)
from newsroom.scorer import score_pending_items
from newsroom.state import DEFAULT_DB_PATH, State

app = typer.Typer(help="Daily Newsroom — local news-digest agent")
console = Console()
logger = logging.getLogger(__name__)


@app.callback()
def _cli_entry(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG-level logging"),
) -> None:
    """Initialize logging before any subcommand runs."""
    configure_logging(verbose=verbose)


def _db_path() -> Path:
    return Path(os.environ.get("NEWSROOM_DB_PATH", str(DEFAULT_DB_PATH)))


def _sources_path() -> Path:
    default = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
    return Path(os.environ.get("NEWSROOM_SOURCES_PATH", str(default)))


def _bootstrap_state() -> State:
    """Ensure schema exists and sync sources.yaml → DB. Idempotent."""
    state = State(_db_path())
    state.ensure_schema()
    sources_path = _sources_path()
    if sources_path.exists():
        for src in load_sources(sources_path):
            state.upsert_source(src)
    return state


# ── Subcommands ────────────────────────────────────────────────────────────────


@app.command()
def version() -> None:
    """Print version."""
    console.print(f"newsroom {__version__}")


@app.command()
def init() -> None:
    """Initialize state DB (idempotent)."""
    state = _bootstrap_state()
    console.print(f"✓ State-DB ready at {_db_path()}")
    _ = state  # used for side-effects only


@app.command("validate-config")
def validate_config() -> None:
    """Validate sources.yaml."""
    path = _sources_path()
    sources = load_sources(path)
    console.print(f"✓ {len(sources)} sources loaded from {path}")


@app.command()
def fetch(
    dry_run: bool = typer.Option(False, "--dry-run"),
    category: str | None = typer.Option(None, "--category", help="Fetch only this category"),
    source: str | None = typer.Option(None, "--source", help="Fetch only this source"),
) -> None:
    """Fetch all due sources, score new items, maybe push notifications."""
    state = _bootstrap_state()
    if dry_run:
        srcs = state.list_enabled_sources()
        due = [s for s in srcs if should_fetch(s)]
        if category:
            due = [s for s in due if s["category"] == category]
        if source:
            due = [s for s in due if s["name"] == source]
        table = Table(title="Sources due")
        table.add_column("Name")
        table.add_column("Category")
        table.add_column("Interval")
        for s in due:
            table.add_row(s["name"], s["category"], f"{s['interval_seconds']}s")
        console.print(table)
        return

    results = asyncio.run(_run_fetch_score_notify(state, category=category, source=source))
    table = Table(title="Fetch results")
    table.add_column("Source")
    table.add_column("Status")
    table.add_column("New items")
    table.add_column("Error")
    for r in results:
        table.add_row(r.source_name, str(r.status), str(r.items_inserted), r.error or "")
    console.print(table)

    # Digest catchup check (safety net for missed digest slots)
    _maybe_run_digest_catchup(state)


async def _run_fetch_score_notify(
    state: State,
    *,
    category: str | None,
    source: str | None,
):
    """Orchestrate fetch → arxiv-filter → score → notify."""
    results = await fetch_due_sources(state, category=category, source=source)
    agent = AgentClient()
    await filter_pending_arxiv_items(state, agent=agent)
    notifier = Notifier()
    await score_pending_items(state, agent=agent, notifier=notifier, limit=100)
    return results


_CATCHUP_WINDOW_HOURS = 2


def _maybe_run_digest_catchup(state: State) -> None:
    """If near morning or evening window and no digest for today, generate it.

    Safety net per spec §6.5: catches missed digest slots when launchd fires late.
    """
    from newsroom.digester import BERLIN_TZ, EVENING_START_HOUR, MORNING_START_HOUR  # noqa: PLC0415

    now = datetime.now(BERLIN_TZ)
    today = now.date().isoformat()
    for slot, target_hour in [
        ("morning", MORNING_START_HOUR + _CATCHUP_WINDOW_HOURS),
        ("evening", EVENING_START_HOUR + _CATCHUP_WINDOW_HOURS),
    ]:
        near_window = abs(now.hour - target_hour) <= _CATCHUP_WINDOW_HOURS
        if near_window and state.get_digest(today, slot) is None:
            logger.info("Digest-catchup: generating missed %s digest for %s", slot, today)
            asyncio.run(generate_digest(state=state, slot=slot, date=now.date()))


@app.command()
def score(
    dry_run: bool = typer.Option(False, "--dry-run"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Score pending items (standalone; normally chained from fetch)."""
    state = _bootstrap_state()
    if dry_run:
        pending = state.list_items_by_status("new", limit=limit)
        pending += state.list_items_by_status("filtered_in", limit=limit)
        console.print(f"Would score {len(pending)} pending items")
        return
    agent = AgentClient()
    notifier = Notifier()
    count = asyncio.run(score_pending_items(state, agent=agent, notifier=notifier, limit=limit))
    console.print(f"Scored {count} items")


@app.command()
def digest(
    dry_run: bool = typer.Option(False, "--dry-run"),
    time_slot: str | None = typer.Option(None, "--time", help="morning|evening"),
    date_str: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate morning or evening digest for today (auto-detects slot from time)."""
    state = _bootstrap_state()
    slot = time_slot
    if slot is None:
        try:
            slot = determine_slot()
        except NoSlotError:
            console.print("Outside morning/evening windows; no digest generated.")
            return
    date = _date.fromisoformat(date_str) if date_str else _date.today()
    if dry_run:
        from newsroom.digester import _cutoff_for_slot  # noqa: PLC0415

        cutoff = _cutoff_for_slot(slot, state)
        items = state.list_items_for_digest(since_iso=cutoff)
        console.print(f"Would generate {slot} digest for {date} with {len(items)} items")
        return
    path = asyncio.run(generate_digest(state=state, slot=slot, date=date, force=force))
    console.print(f"✓ Digest written: {path}")


@app.command()
def notify(
    test: bool = typer.Option(False, "--test", help="Send a test notification"),
) -> None:
    """Notification utilities (mainly --test)."""
    if test:
        _send_with_pync(
            "[Newsroom · Info]",
            "Notification test — wenn du das siehst, funktioniert pync.",
        )
        console.print("Test notification sent (check Notification Center).")
        return
    console.print("Use --test to send a test notification.")


@app.command()
def status() -> None:
    """Print status overview: auth, DB, sources, today's activity."""
    from newsroom.status import print_status  # noqa: PLC0415

    state = _bootstrap_state()
    print_status(state, console=console)
