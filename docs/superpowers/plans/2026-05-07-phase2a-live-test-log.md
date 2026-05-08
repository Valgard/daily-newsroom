# Phase 2a Live-Test Log (start 2026-05-08)

**Window:** 2026-05-08 → 2026-05-14 (7 days)

Per spec §7.3 Definition-of-Done — operational metrics tracked over a 7-day live test before Phase 2a is declared "done":

- Welt push S/N subjectively ≥60 % "worthwhile"
- ≤20 Welt pushes/day on 7-day average (informal volume guardrail; exceeding triggers Open Question 8.2.A burst-cap, not a DoD fail)
- 0 missed digests
- Welt score distribution lies in loose target bands: ≥1 % at 5, ≥2 % at 4, ≥15 % at 3, ≥50 % at 2, ≥10 % at 1.

## Daily checkpoints

| Day | Welt pushes | AI pushes | Push S/N (subj.) | Notes |
|-----|---|---|---|---|
| 2026-05-08 | | | | |
| 2026-05-09 | | | | |
| 2026-05-10 | | | | |
| 2026-05-11 | | | | |
| 2026-05-12 | | | | |
| 2026-05-13 | | | | |
| 2026-05-14 | | | | |

## End-of-window distribution check (run on 2026-05-14)

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
    "SELECT importance, COUNT(*)
       FROM items
       JOIN sources ON items.source_id = sources.id
      WHERE sources.category = 'world'
        AND items.scored_at >= '2026-05-08'
      GROUP BY importance ORDER BY importance;"
```

Targets (loose bands per spec §7.3):
- ≥1 % at 5
- ≥2 % at 4
- ≥15 % at 3
- ≥50 % at 2
- ≥10 % at 1

## Push count check

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
    "SELECT date(notified_at) AS day, COUNT(*)
       FROM items
       JOIN sources ON items.source_id = sources.id
      WHERE sources.category = 'world'
        AND items.notified_at IS NOT NULL
        AND items.push_sent = 1
        AND notified_at >= '2026-05-08'
      GROUP BY day ORDER BY day;"
```

Average ≤20/day = pass; >20/day triggers Open Question 8.2.A burst-cap patch.

## Missed-digests check

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
    "SELECT date, slot, item_count
       FROM digests
      WHERE date >= '2026-05-08'
      ORDER BY date, slot;"
```

Expected: 14 rows (7 days × 2 slots). Missing rows = missed digest.

## Push S/N evaluation

Subjective — for each push during the window, note whether it was "worth interrupting me" in the table above. Day-level aggregate goes into the S/N column.
