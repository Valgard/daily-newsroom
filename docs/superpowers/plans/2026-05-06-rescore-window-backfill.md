# Backfill-Rescore Window 2026-04-21 → 2026-05-05 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-score every item in `items` whose `scored_at` falls in `[2026-04-21, 2026-05-05T10:00:00)` (n ≈ 1.446) with the recalibrated `score_item.md` prompt, write the new `importance` / `score_reason` / `score_model` to the DB **without touching `scored_at`**, and produce a before/after diff report.

**Architecture:** A standalone, idempotent CLI script (`scripts/rescore_window.py`) bypasses `score_pending_items()` and the notifier entirely. It uses `AgentClient` directly with `asyncio.gather + Semaphore(6)`. Audit + idempotency live in a JSONL log file (no schema migration). `scored_at` stays untouched so past items remain gated out of future digests.

**Tech Stack:** Python 3.12, `uv`, asyncio, `claude-agent-sdk` via existing `AgentClient`, SQLite (WAL).

---

## Pre-Decisions Locked In

| Decision | Choice | Rationale |
|----------|--------|-----------|
| `scored_at` after rescore | **unchanged** | Updating to NOW pulls 14d-old items into next digest via `list_items_for_digest(scored_at >= cutoff)`. Keeping it preserves digest-gating + matches user constraint. |
| Audit trail | **JSONL log file** (not DB column) | One-shot script, no schema migration. JSONL doubles as resume index. |
| Idempotency mechanism | **JSONL replay** | On restart, read all `item_id`s from JSONL, skip those. Deterministic. No DB markers needed. |
| `score_model` value | **`claude-haiku-4-5`** unchanged | Avoids polluting model column with timestamps; the JSONL captures "this row was rescored". |
| launchd during run | **paused** (`launchctl unload`) | Avoids SDK fork-races between concurrent `claude` subprocesses spawned by fetcher + rescore. Fetch is dumb-trigger, can absorb a 75-min pause. |
| Notifier | **never instantiated** | The rescore script imports `AgentClient` only — never `Notifier`. Static guarantee no push fires. |
| Concurrency | **6 parallel via Semaphore** | Matches yesterday's verified-throughput level. SDK retry (tenacity 3×) handles transient subprocess errors. |
| Concurrency-safety wrt fetch | **launchd paused** + per-row `UPDATE id=?` | SQLite WAL would tolerate concurrent writers, but pausing is simpler. |

---

## File Structure

- **Create**: `scripts/rescore_window.py` — the standalone tool. Self-contained, no new prod-code modules.
- **Create**: `scripts/rescore_window_README.md` — short usage notes (in English per global CLAUDE.md). Describes flags, JSONL schema, recovery.
- **No edits** to `src/newsroom/`. Reuses `AgentClient`, `State.connection()`, raw SQL.

JSONL log location: `~/Library/Application Support/daily-newsroom/rescore-2026-05-06.jsonl` (next to `state.db`, not in repo).

---

## Task 1: Worktree + branch

**Files:**
- New worktree under `.worktrees/rescore-window/` (path doesn't exist yet, `.gitignore` check needed)
- Branch: `rescore-window-2026-05-06` (no ticket, solo project per CLAUDE.md)

- [ ] **Step 1: Verify `.worktrees/` ignore status**

```bash
cd /Users/valgard/Projects/private/daily-newsroom
git check-ignore -q .worktrees/ && echo IGNORED || echo NOT_IGNORED
```

Expected: NOT_IGNORED (directory doesn't exist) → add to `.gitignore`.

- [ ] **Step 2: Add `.worktrees/` to `.gitignore` if missing**

```bash
grep -qxF '.worktrees/' .gitignore || echo '.worktrees/' >> .gitignore
git add .gitignore && git commit -m "chore: ignore .worktrees/"
```

- [ ] **Step 3: Create the worktree + branch**

```bash
git worktree add -b rescore-window-2026-05-06 .worktrees/rescore-window main
cd .worktrees/rescore-window
```

- [ ] **Step 4: Sync uv venv inside the worktree**

```bash
uv sync
```

Expected: `Resolved … packages` then `Installed …`. If it errors with "missing pyproject", you're in the wrong cwd.

---

## Task 2: Build `scripts/rescore_window.py` skeleton

**Files:**
- Create: `scripts/rescore_window.py` (in the worktree)

- [ ] **Step 1: Write the script — argparse + window query + dry-run path**

```python
#!/usr/bin/env python3
"""Rescore historical items in a time window with the current prompt.

One-shot tool used after the 2026-05-05 prompt recalibration to fix items
that the prior prompt systematically capped at importance=3. NOT for
periodic use.

The script is idempotent: replays the JSONL log to skip already-rescored IDs.
Does NOT touch `scored_at` (would otherwise pull 14d-old items into the next
digest). Does NOT instantiate the notifier (no retroactive pushes).
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

# Make src/ importable when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from newsroom.agent_client import AgentClient  # noqa: E402
from newsroom.state import DEFAULT_DB_PATH  # noqa: E402

SCORING_MODEL = "claude-haiku-4-5"
WINDOW_START = "2026-04-21T00:00:00"
WINDOW_END = "2026-05-05T10:00:00"

DEFAULT_LOG_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "daily-newsroom"
    / "rescore-2026-05-06.jsonl"
)

logger = logging.getLogger("rescore")


def fetch_window(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT items.id, items.title, items.author, items.raw_summary,
                  items.importance, items.score_reason,
                  items.scored_at, sources.name AS source_name
             FROM items
             JOIN sources ON items.source_id = sources.id
            WHERE items.scored_at >= ?
              AND items.scored_at <  ?
              AND items.status = 'scored'
            ORDER BY items.id""",
        (WINDOW_START, WINDOW_END),
    ).fetchall()
    return list(rows)


def load_done_ids(log_path: Path) -> set[int]:
    if not log_path.exists():
        return set()
    done: set[int] = set()
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("ok") and "id" in rec:
                done.add(int(rec["id"]))
    return done


async def rescore_one(
    *,
    item: sqlite3.Row,
    agent: AgentClient,
    sem: asyncio.Semaphore,
) -> dict:
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
            if not 1 <= importance <= 5:
                importance = 2
            reason = str(result.get("reason", ""))
            return {
                "ok": True,
                "id": item["id"],
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
    """UPDATE only importance/score_reason/score_model — NEVER scored_at."""
    conn.execute(
        """UPDATE items
              SET importance = ?, score_reason = ?, score_model = ?
            WHERE id = ?""",
        (rec["new_importance"], rec["new_reason"], rec["model"], rec["id"]),
    )


async def main_async(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")

    rows = fetch_window(conn)
    done = load_done_ids(log_path)
    pending = [r for r in rows if r["id"] not in done]

    if args.limit:
        pending = pending[: args.limit]

    logger.info(
        "window: %d total / %d already done / %d to rescore (limit=%s, dry_run=%s)",
        len(rows),
        len(done),
        len(pending),
        args.limit,
        args.dry_run,
    )

    if not pending:
        return 0

    agent = AgentClient()
    sem = asyncio.Semaphore(args.concurrency)

    log_fh = log_path.open("a", buffering=1)  # line-buffered
    try:
        coros = [rescore_one(item=item, agent=agent, sem=sem) for item in pending]
        # gather preserves order; we want streaming progress + per-result write.
        # Use as_completed for that.
        done_count = 0
        for fut in asyncio.as_completed(coros):
            rec = await fut
            log_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec["ok"] and not args.dry_run:
                apply_update(conn, rec)
            done_count += 1
            if done_count % 25 == 0:
                logger.info("progress: %d / %d", done_count, len(pending))
    finally:
        log_fh.close()
        conn.close()

    logger.info("done: %d items processed", done_count)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--log", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--limit", type=int, default=0, help="0 = no limit")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip the UPDATE; only write JSONL log")
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
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/rescore_window.py
```

- [ ] **Step 3: Sanity-test (no LLM calls — pure query path)**

```bash
uv run python scripts/rescore_window.py --limit 5 --dry-run --verbose 2>&1 | head -20
```

Expected first line `window: 1446 total / 0 already done / 5 to rescore`. JSONL file gets 5 success records appended. Item importance in DB unchanged (dry-run).

- [ ] **Step 4: Verify the JSONL was written and is parseable**

```bash
LOG=~/Library/Application\ Support/daily-newsroom/rescore-2026-05-06.jsonl
test -s "$LOG" && echo "size: $(wc -l < "$LOG") lines"
jq -r '[.id, .old_importance, .new_importance] | @tsv' "$LOG" | head -5
```

Expected: 5 lines, columns `id  old_imp  new_imp`. At least one row should show new_imp ≠ old_imp (because some Level-3-cap items SHOULD now be 4 or 5).

- [ ] **Step 5: Commit the skeleton**

```bash
git add scripts/rescore_window.py
git commit -m "feat(scripts): backfill rescorer for the 2026-04-21..05-05 window"
```

---

## Task 3: Trockenlauf-Approval-Checkpoint

**Files:** none — this is a checkpoint, not code.

- [ ] **Step 1: Show the trockenlauf JSONL output to the user**

```bash
jq '.' ~/Library/Application\ Support/daily-newsroom/rescore-2026-05-06.jsonl | tail -50
```

- [ ] **Step 2: STOP. Wait for user explicit go.**

Expected user signal: "go" / "weiter" / "starte den Volllauf".

If user wants to abort: `rm` the JSONL log (so the resume isn't poisoned) and abandon. The dry-run wrote no DB changes, so nothing to revert.

If user wants only the verifier-4 trial first:

```bash
uv run python scripts/rescore_window.py --limit 4 --dry-run --verbose
jq -r 'select(.id | IN(35979, 36100, 33918, 34004)) | [.id, .new_importance, .new_reason] | @tsv' \
  ~/Library/Application\ Support/daily-newsroom/rescore-2026-05-06.jsonl
```

(Limit-4 may not hit those exact IDs; might need a bespoke `--ids` flag if user demands. Ask if so.)

---

## Task 4: Pause launchd jobs

**Files:** none — system state change.

- [ ] **Step 1: Confirm jobs are loaded**

```bash
launchctl list | grep newsroom
```

Expected: 3 lines (fetch, digest-morning, digest-evening), all status `-` or PID.

- [ ] **Step 2: Unload the fetch job (the only one that writes to items)**

```bash
launchctl unload ~/Library/LaunchAgents/de.svenpoeche.newsroom.fetch.plist
launchctl list | grep newsroom.fetch && echo STILL_LOADED || echo UNLOADED
```

Expected: `UNLOADED`. Digest jobs left alone (they read items but don't write to importance).

- [ ] **Step 3: Note for cleanup**

Remember to reload after the run:
```bash
launchctl load ~/Library/LaunchAgents/de.svenpoeche.newsroom.fetch.plist
```

---

## Task 5: Full rescore run (background, file-logged)

**Files:**
- Output: log file at `/tmp/rescore-2026-05-06.log` (transient, not committed)

- [ ] **Step 1: Clear the trockenlauf JSONL — start fresh for the real run**

The dry-run JSONL would otherwise mark those 5 IDs as "done" and skip the real LLM call for them. We want them rescored with the real UPDATE applied.

```bash
rm ~/Library/Application\ Support/daily-newsroom/rescore-2026-05-06.jsonl
```

(Alternative: `mv` to a `.dryrun` suffix for forensic comparison.)

- [ ] **Step 2: Launch the run in the background**

```bash
cd /Users/valgard/Projects/private/daily-newsroom/.worktrees/rescore-window
nohup uv run python scripts/rescore_window.py --concurrency 6 \
  > /tmp/rescore-2026-05-06.log 2>&1 &
echo "pid=$!"
```

- [ ] **Step 3: Monitor**

```bash
tail -f /tmp/rescore-2026-05-06.log
```

Expected cadence: `progress: 25 / 1446`, `progress: 50 / 1446`, ... at ~80–120s intervals (≈ 0.3 items/s × concurrency=6).

If you see `Unknown child process pid` repeatedly → tenacity retries; logged via `rescore failed for item N: ...`. Single-digit failures are tolerable; double-digit means SDK-meltdown — abort, investigate, resume via JSONL.

- [ ] **Step 4: Wait for completion**

Expected total: ~75 min. The script ends with `done: 1446 items processed` (or fewer if some items errored out and weren't retried in time — they remain in the JSONL with `ok: false` and can be re-attempted via plain re-run; the resume logic skips only `ok: true` rows).

---

## Task 6: Verify + diff report

**Files:**
- Create: `/tmp/rescore-diff-report.md` — distribution + new-4/5 list
- The JSONL log is the canonical artifact; the report is derived.

- [ ] **Step 1: Sanity row-count: how many UPDATEs landed?**

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
  "SELECT importance, COUNT(*) FROM items
     WHERE scored_at >= '2026-04-21' AND scored_at < '2026-05-05T10:00:00'
     GROUP BY importance ORDER BY importance;"
```

Compare with the pre-run baseline:
```
1| 21    →  expect ~217 (target 15%) but maybe undershoot due to old-prompt-Level-1 already being good
2|934    →  expect ~870 (target 60%)
3|491    →  expect ~290 (target 20%)
4|  0    →  expect  ~58 (target 4%)
5|  0    →  expect  ~14 (target 1%)
```

These targets are exact-distribution goals; real outputs from a single Haiku rescoring will deviate. The decisive sanity check: `imp=4` count must move from 0 to "small but non-zero", and `imp=5` likewise. If `imp=4`/`imp=5` are still 0, something went wrong with the prompt or model.

- [ ] **Step 2: Generate the diff report**

```bash
LOG=~/Library/Application\ Support/daily-newsroom/rescore-2026-05-06.jsonl

# Per-day distribution diff
jq -r 'select(.ok) |
  [(.scored_at_unchanged[0:10]), .old_importance, .new_importance] | @tsv' "$LOG" |
  awk -F'\t' '{ before[$1"|"$2]++; after[$1"|"$3]++; days[$1]=1 }
              END { for (d in days) {
                      printf "%s ", d
                      for (i=1;i<=5;i++) printf "[%d:%d→%d] ", i,
                        before[d"|"i]+0, after[d"|"i]+0
                      print ""
                    } }' | sort

# New 5s
echo "## New imp=5"
jq -r 'select(.ok and .new_importance==5 and .old_importance!=5) |
       "- [\(.id)] \(.new_reason)"' "$LOG"

# New 4s (promoted from <4)
echo "## New imp=4 (promotions)"
jq -r 'select(.ok and .new_importance==4 and (.old_importance < 4)) |
       "- [\(.id)] (was \(.old_importance)) \(.new_reason)"' "$LOG"
```

- [ ] **Step 3: Cross-reference with the user's verified-mis-score items**

```bash
jq 'select(.ok and (.id | IN(35979, 36100, 33918, 34004))) |
    {id, old: .old_importance, new: .new_importance, reason: .new_reason}' "$LOG"
```

Expected: items 35979 → 5, items 33918/34004/36100 → 4. If those don't move, the run isn't doing its job.

- [ ] **Step 4: Ship the report to the user**

Print the output of step 2 to chat. Don't write a separate markdown report file unless asked — keep it inline.

---

## Task 7: Reload launchd + cleanup worktree

**Files:** none.

- [ ] **Step 1: Reload the fetch job**

```bash
launchctl load ~/Library/LaunchAgents/de.svenpoeche.newsroom.fetch.plist
launchctl list | grep newsroom.fetch
```

Expected: job listed, status `-` until next 5-min trigger.

- [ ] **Step 2: Commit script + README**

```bash
cd /Users/valgard/Projects/private/daily-newsroom/.worktrees/rescore-window
git add scripts/rescore_window.py scripts/rescore_window_README.md
git status
git commit --amend --no-edit  # only if uncommitted; otherwise new commit
```

- [ ] **Step 3: Merge worktree branch back to main (rebase, per CLAUDE.md)**

```bash
cd /Users/valgard/Projects/private/daily-newsroom
git fetch
git rebase rescore-window-2026-05-06 main
# OR for solo: just fast-forward
git merge --ff-only rescore-window-2026-05-06
```

- [ ] **Step 4: Remove worktree (CHANGE CWD FIRST per CLAUDE.md)**

```bash
cd /Users/valgard/Projects/private/daily-newsroom
git worktree remove .worktrees/rescore-window
git branch -d rescore-window-2026-05-06
```

- [ ] **Step 5: Decide JSONL audit log retention**

The JSONL stays at `~/Library/Application Support/daily-newsroom/rescore-2026-05-06.jsonl`. Don't commit to repo (sits in user-data directory, not under git). Long-term: keep ≥30 days for forensic look-up if Phase-2 work questions historical scores.

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| 75-min run dies mid-way | JSONL replay on rerun. Idempotent `UPDATE` (writing same value is a no-op). |
| `claude` SDK fork-races | launchd fetch paused; concurrency=6 (proven survivable yesterday). |
| Future digest accidentally re-includes 14d items | `scored_at` unchanged — past items remain past. Verified against `_cutoff_for_slot` + `list_items_for_digest`. |
| Notifications fire retroactively | Script never imports `Notifier`. Static guarantee. |
| Distribution doesn't shift | Compare verifier-4 IDs (35979/36100/33918/34004). If those still come out at 3, prompt changed since user's spot-check — STOP, no further commits. |
| User aborts mid-run | Kill the bg pid; partial JSONL has all completed UPDATEs already applied; resume via plain rerun. |
| Concurrent fetcher writes during run | Pre-emptively unloaded. Even if user re-loads accidentally: WAL handles it; only conflict surface is UPDATE on same `id`, which fetcher would only touch for newly-scored post-fix items (outside our window). |

---

## Self-Review

**Spec coverage (vs. user constraints):**
- ✅ Window `[2026-04-21, 2026-05-05T10:00:00)`: hard-coded constants, query uses both bounds.
- ✅ Notifier disabled: never imported in the script. Static.
- ✅ Concurrency-safety: launchd unloaded; per-row UPDATE; WAL fallback.
- ✅ Idempotency: JSONL replay with `ok: true` filter.
- ✅ Audit trail: JSONL covers `old_importance`, `new_importance`, `old_reason`, `new_reason`, `scored_at_unchanged`. `scored_at` itself untouched. **Decision recorded:** no DB column added (one-shot, YAGNI per user's "Skript ist ein One-Shot, keine Production-Code-Erweiterung").
- ✅ Trockenlauf on 5 items before full run.
- ✅ Performance plan: 75 min @ concurrency=6, background-logged.
- ✅ Diff report at end (per-day distribution + new-4/5 list).
- ✅ Script archived under `scripts/` for future re-calibration.

**Placeholder scan:** none. Every code block runnable. Every command exact.

**Type consistency:** `rescore_one` returns dict with stable keys; `apply_update` references only those keys; `load_done_ids` filters on `ok` + `id` keys → matches what `rescore_one` writes.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-rescore-window-backfill.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute tasks here using `superpowers:executing-plans`, batch with checkpoints

Which approach?
