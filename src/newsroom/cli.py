"""CLI entry point wiring all subcommands via typer."""

from __future__ import annotations

import asyncio
import logging
import os
import time
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
    """Orchestrate fetch → arxiv-filter → score → notify.

    Each phase emits an INFO-level lifecycle marker, even when there is nothing
    to do — a 'silent normal idle' run is otherwise indistinguishable from a
    'crashed mid-flight' run, which is what made the 2026-05-05 backlog
    diagnosis painful (a 4 h apparent gap turned out to be 'no items due').
    """
    t0 = time.monotonic()
    pid = os.getpid()
    logger.info("run start (pid=%d)", pid, extra={"event": "run_start", "pid": pid})

    results = await fetch_due_sources(state, category=category, source=source)
    n_inserted = sum(r.items_inserted for r in results)
    logger.info(
        "fetch phase: %d sources checked, %d new items",
        len(results),
        n_inserted,
        extra={
            "event": "fetch_phase_done",
            "sources": len(results),
            "new_items": n_inserted,
        },
    )

    agent = AgentClient()
    n_arxiv = await filter_pending_arxiv_items(state, agent=agent)
    logger.info(
        "filter phase: %d arxiv items processed",
        n_arxiv,
        extra={"event": "filter_phase_done", "processed": n_arxiv},
    )

    notifier = Notifier()
    n_scored = await score_pending_items(state, agent=agent, notifier=notifier, limit=100)
    pending_after = state.count_pending_for_scoring()
    logger.info(
        "score phase: %d scored, %d still pending",
        n_scored,
        pending_after,
        extra={
            "event": "score_phase_done",
            "scored": n_scored,
            "pending_after": pending_after,
        },
    )

    duration_s = round(time.monotonic() - t0, 2)
    logger.info(
        "run end (%.1fs)",
        duration_s,
        extra={"event": "run_end", "duration_s": duration_s},
    )
    return results


# Asymmetric catchup window: only fires at or AFTER the launchd-scheduled
# time, never before — otherwise the regular launchd tick at 07:00/20:00
# never gets a chance to run because fetch-tick at 05:00 already triggered
# the catchup. Window length matches plist StartCalendarInterval delay
# tolerance for sleep-wake recovery.
_CATCHUP_WINDOW_HOURS = 2

# Must match config/launchd/de.svenpoeche.newsroom.digest-{morning,evening}.plist
# StartCalendarInterval Hour values.
SCHEDULED_MORNING_HOUR = 7
SCHEDULED_EVENING_HOUR = 20


def _is_in_catchup_window(hour: int, slot: str) -> bool:
    """True if `hour` is in [scheduled, scheduled + WINDOW] inclusive.

    The catchup safety-net (spec §6.5) only kicks in once the regularly
    scheduled launchd time has passed. This avoids racing the regular tick.
    """
    scheduled = SCHEDULED_MORNING_HOUR if slot == "morning" else SCHEDULED_EVENING_HOUR
    return scheduled <= hour <= scheduled + _CATCHUP_WINDOW_HOURS


def _maybe_run_digest_catchup(state: State) -> None:
    """If past the scheduled time and no digest for today, generate it.

    Safety net per spec §6.5: catches missed digest slots when launchd fires
    late (Mac was asleep).
    """
    from newsroom.digester import BERLIN_TZ  # noqa: PLC0415

    now = datetime.now(BERLIN_TZ)
    today = now.date().isoformat()
    for slot in ("morning", "evening"):
        if _is_in_catchup_window(now.hour, slot) and state.get_digest(today, slot) is None:
            logger.info("Digest-catchup: generating missed %s digest for %s", slot, today)
            asyncio.run(
                generate_digest(
                    state=state,
                    slot=slot,
                    date=now.date(),
                    notifier=Notifier(),
                )
            )


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
    paths = asyncio.run(
        generate_digest(
            state=state,
            slot=slot,
            date=date,
            force=force,
            notifier=Notifier(),
        )
    )
    if paths:
        for p in paths:
            console.print(f"✓ Digest written: {p}")
    else:
        console.print("No digest written (no new items).")


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
