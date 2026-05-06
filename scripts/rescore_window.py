#!/usr/bin/env python3
"""Rescore historical items in a time window with the current prompt.

One-shot tool used after the 2026-05-05 prompt recalibration to fix items
that the prior prompt systematically capped at importance=3. Not for
periodic use.

The script is idempotent: replays the JSONL log to skip already-rescored IDs.
Does NOT touch ``scored_at`` (would otherwise pull 14d-old items into the
next digest via ``list_items_for_digest`` filtering ``scored_at >= cutoff``).
Does NOT instantiate the notifier (no retroactive pushes).

Usage examples::

    # Trockenlauf, 5 generic items, separate dryrun-JSONL
    uv run python scripts/rescore_window.py --limit 5 --dry-run

    # Targeted Verifier-Probe, dry-run only
    uv run python scripts/rescore_window.py --ids 35979,36100,33918,34004 --dry-run

    # Full run, concurrency 6, file-logged
    nohup uv run python scripts/rescore_window.py --concurrency 6 \\
        > /tmp/rescore-2026-05-06.log 2>&1 &
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# Make src/ importable when run from repo root or worktree
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from newsroom.agent_client import AgentClient  # noqa: E402
from newsroom.state import DEFAULT_DB_PATH  # noqa: E402

SCORING_MODEL = "claude-haiku-4-5"
WINDOW_START = "2026-04-21T00:00:00"
WINDOW_END = "2026-05-05T10:00:00"

DATA_DIR = Path.home() / "Library" / "Application Support" / "daily-newsroom"
LOG_NAME_PROD = "rescore-2026-05-06.jsonl"
LOG_NAME_DRY = "rescore-2026-05-06.dryrun.jsonl"

logger = logging.getLogger("rescore")


def fetch_window(conn: sqlite3.Connection, *, ids: list[int] | None = None) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    if ids:
        placeholders = ",".join("?" * len(ids))
        sql = f"""SELECT items.id, items.title, items.author, items.raw_summary,
                         items.importance, items.score_reason,
                         items.scored_at, sources.name AS source_name
                    FROM items
                    JOIN sources ON items.source_id = sources.id
                   WHERE items.id IN ({placeholders})
                     AND items.scored_at >= ?
                     AND items.scored_at <  ?
                     AND items.status = 'scored'
                   ORDER BY items.id"""
        params = [*ids, WINDOW_START, WINDOW_END]
    else:
        sql = """SELECT items.id, items.title, items.author, items.raw_summary,
                        items.importance, items.score_reason,
                        items.scored_at, sources.name AS source_name
                   FROM items
                   JOIN sources ON items.source_id = sources.id
                  WHERE items.scored_at >= ?
                    AND items.scored_at <  ?
                    AND items.status = 'scored'
                  ORDER BY items.id"""
        params = [WINDOW_START, WINDOW_END]
    return list(conn.execute(sql, params).fetchall())


def load_done_ids(log_path: Path) -> set[int]:
    """IDs already successfully rescored — replayed from JSONL for idempotency."""
    if not log_path.exists():
        return set()
    done: set[int] = set()
    with log_path.open() as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok") and "id" in rec:
                done.add(int(rec["id"]))
    return done


async def rescore_one(*, item: sqlite3.Row, agent: AgentClient, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            result = await agent.ask(
                prompt_name="score_item",
                variables={
                    "source_name": item["source_name"],
                    "title": item["title"],
                    "author": item["author"] or "Unknown",
                    "summary": item["raw_summary"] or "(no body)",
                },
                model=SCORING_MODEL,
                parse="json",
            )
            try:
                importance = int(result.get("importance", 2))
            except (TypeError, ValueError):
                importance = 2
            if not 1 <= importance <= 5:  # noqa: PLR2004
                importance = 2
            reason = str(result.get("reason", ""))
            return {
                "ok": True,
                "id": item["id"],
                "title": item["title"],
                "source_name": item["source_name"],
                "old_importance": item["importance"],
                "new_importance": importance,
                "old_reason": item["score_reason"],
                "new_reason": reason,
                "model": SCORING_MODEL,
                "scored_at_unchanged": item["scored_at"],
                "ts": datetime.now(UTC).isoformat(),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("rescore failed for item %s: %s", item["id"], e)
            return {
                "ok": False,
                "id": item["id"],
                "error": str(e),
                "ts": datetime.now(UTC).isoformat(),
            }


def apply_update(conn: sqlite3.Connection, rec: dict) -> None:
    """UPDATE only importance / score_reason / score_model — NEVER scored_at."""
    conn.execute(
        """UPDATE items
              SET importance = ?, score_reason = ?, score_model = ?
            WHERE id = ?""",
        (rec["new_importance"], rec["new_reason"], rec["model"], rec["id"]),
    )


async def main_async(args: argparse.Namespace) -> int:
    if args.log:
        log_path = Path(args.log)
    else:
        log_path = DATA_DIR / (LOG_NAME_DRY if args.dry_run else LOG_NAME_PROD)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    ids: list[int] | None = None
    if args.ids:
        ids = [int(x.strip()) for x in args.ids.split(",") if x.strip()]

    conn = sqlite3.connect(args.db, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")

    rows = fetch_window(conn, ids=ids)
    done = load_done_ids(log_path)
    pending = [r for r in rows if r["id"] not in done]

    if args.limit:
        pending = pending[: args.limit]

    logger.info(
        "log=%s | window: %d total / %d already done / %d to rescore "
        "(limit=%s, ids=%s, dry_run=%s, concurrency=%d)",
        log_path,
        len(rows),
        len(done),
        len(pending),
        args.limit or "—",
        args.ids or "—",
        args.dry_run,
        args.concurrency,
    )

    if not pending:
        return 0

    agent = AgentClient()
    sem = asyncio.Semaphore(args.concurrency)
    log_fh = log_path.open("a", buffering=1)

    done_count = 0
    try:
        coros = [rescore_one(item=item, agent=agent, sem=sem) for item in pending]
        for fut in asyncio.as_completed(coros):
            rec = await fut
            log_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec["ok"] and not args.dry_run:
                apply_update(conn, rec)
            done_count += 1
            if done_count % 25 == 0 or done_count == len(pending):
                logger.info("progress: %d / %d", done_count, len(pending))
    finally:
        log_fh.close()
        conn.close()

    logger.info("done: %d items processed (log=%s)", done_count, log_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--log",
        default="",
        help="Override JSONL log path. If empty, picks "
        "rescore-2026-05-06.jsonl (or .dryrun.jsonl with --dry-run).",
    )
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated item ids to target (intersected with the window).",
    )
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip the UPDATE; only write JSONL log (separate dryrun-suffixed file).",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
