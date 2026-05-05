"""SQLite state manager for newsroom."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from newsroom.config import Source

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "daily-newsroom" / "state.db"

# Digest filter: reject items whose known publication date is older than this.
# Independent of the digest-cycle cutoff — this guards against sitemap-scrape
# backfill surfacing old URLs as "today's news".
DIGEST_MAX_ITEM_AGE_DAYS = 7

# Digest filter: items below this importance are routine noise, kept in the
# archive but not shown in the newsletter digest.
DIGEST_MIN_IMPORTANCE = 3


MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE sources (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT UNIQUE NOT NULL,
            category            TEXT NOT NULL,
            subcategory         TEXT,
            url                 TEXT NOT NULL,
            feed_type           TEXT NOT NULL,
            interval_seconds    INTEGER NOT NULL,
            enabled             INTEGER NOT NULL DEFAULT 1,
            last_fetched_at     TEXT,
            last_checked_at     TEXT,
            etag                TEXT,
            last_modified       TEXT,
            consecutive_errors  INTEGER NOT NULL DEFAULT 0,
            disabled_until      TEXT,
            last_error          TEXT,
            last_error_at       TEXT
        )""",
        """CREATE TABLE items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id           INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            item_hash           TEXT UNIQUE NOT NULL,
            url                 TEXT NOT NULL,
            title               TEXT NOT NULL,
            author              TEXT,
            published_at        TEXT,
            raw_summary         TEXT,
            category            TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'new',
            importance          INTEGER,
            score_reason        TEXT,
            score_model         TEXT,
            scored_at           TEXT,
            arxiv_relevant      INTEGER,
            notified_at         TEXT,
            included_in_digest  TEXT,
            fetched_at          TEXT NOT NULL DEFAULT (datetime('now')),
            cross_link_summary  TEXT
        )""",
        """CREATE TABLE digests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,
            slot          TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            item_count    INTEGER NOT NULL,
            model         TEXT NOT NULL,
            generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(date, slot)
        )""",
        "CREATE INDEX idx_items_status ON items(status) WHERE status IN ('new', 'filtered_in')",
        "CREATE INDEX idx_items_source ON items(source_id)",
        "CREATE INDEX idx_items_published ON items(published_at DESC)",
        """CREATE INDEX idx_items_digest_pending ON items(status, importance)
            WHERE status = 'scored' AND included_in_digest IS NULL""",
        "CREATE INDEX idx_sources_enabled ON sources(enabled, last_checked_at) WHERE enabled = 1",
    ],
    2: [
        # Optional URL-filter regex used by sitemap-scrape feed_type.
        "ALTER TABLE sources ADD COLUMN url_filter TEXT",
    ],
    3: [
        # Distinguish actual pushes (push_sent=1) from bundling-suppressions /
        # bulk backfill marks (push_sent=0). Fixes stats['notified_today'] and
        # the bundling window counter.
        "ALTER TABLE items ADD COLUMN push_sent INTEGER NOT NULL DEFAULT 0",
        # Historical backfill: before this migration, bundling was not
        # implemented, so any item with notified_at set was a real push iff
        # it met the push threshold (importance >= 4). Items with
        # notified_at set but importance NULL or < 4 are bulk-marks
        # (e.g. our recent 2358-item SQL suppression) — they stay push_sent=0.
        "UPDATE items SET push_sent = 1 "
        "WHERE notified_at IS NOT NULL AND importance IS NOT NULL AND importance >= 4",
    ],
    4: [
        # Normalize legacy SQLite-default timestamps ('YYYY-MM-DD HH:MM:SS', no TZ)
        # to ISO-8601 UTC ('YYYY-MM-DDTHH:MM:SS+00:00'). Matches scored_at /
        # notified_at format already produced by Python. Required for lex-correct
        # ORDER BY across mixed legacy + new rows; also unblocks TZ-aware analysis.
        "UPDATE items SET fetched_at = REPLACE(fetched_at, ' ', 'T') || '+00:00' "
        "WHERE fetched_at NOT LIKE '%+%' AND fetched_at NOT LIKE '%Z'",
        "UPDATE digests SET generated_at = REPLACE(generated_at, ' ', 'T') || '+00:00' "
        "WHERE generated_at NOT LIKE '%+%' AND generated_at NOT LIKE '%Z'",
        "UPDATE sources SET last_error_at = REPLACE(last_error_at, ' ', 'T') || '+00:00' "
        "WHERE last_error_at IS NOT NULL "
        "  AND last_error_at NOT LIKE '%+%' AND last_error_at NOT LIKE '%Z'",
    ],
}


class State:
    """SQLite wrapper. One instance per process; thread-unsafe."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Migrations ─────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        conn = self.connection()
        # bootstrap: schema_version table may not exist yet
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        for version in sorted(MIGRATIONS):
            if version > current:
                with conn:
                    for stmt in MIGRATIONS[version]:
                        conn.execute(stmt)
                    conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    # ── Sources ────────────────────────────────────────────────────────

    def upsert_source(self, src: Source) -> None:
        """Insert or update a source. Config-driven: URL/interval/category/enabled can change."""
        conn = self.connection()
        conn.execute(
            """INSERT INTO sources
                (name, category, subcategory, url, feed_type, interval_seconds, enabled, url_filter)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(name) DO UPDATE SET
                category = excluded.category,
                subcategory = excluded.subcategory,
                url = excluded.url,
                feed_type = excluded.feed_type,
                interval_seconds = excluded.interval_seconds,
                enabled = excluded.enabled,
                url_filter = excluded.url_filter""",
            (
                src.name,
                src.category,
                src.subcategory,
                src.url,
                src.feed_type,
                src.interval_seconds,
                int(src.enabled),
                src.url_filter,
            ),
        )

    def get_source_by_name(self, name: str) -> sqlite3.Row | None:
        conn = self.connection()
        return conn.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()

    def list_enabled_sources(self) -> list[sqlite3.Row]:
        conn = self.connection()
        return list(
            conn.execute("SELECT * FROM sources WHERE enabled = 1 ORDER BY name").fetchall()
        )

    def update_source_fetch_state(
        self,
        *,
        name: str,
        last_checked_at: datetime,
        last_fetched_at: datetime | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Update per-fetch state. `None` preserves existing values via COALESCE.

        Intended for both 304 (only last_checked_at passed) and 200 responses.
        Note: this method cannot clear etag/last_modified to NULL — callers that
        need to do so must use a separate UPDATE. See spec §5.4.
        """
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                last_checked_at = ?,
                last_fetched_at = COALESCE(?, last_fetched_at),
                etag = COALESCE(?, etag),
                last_modified = COALESCE(?, last_modified)
             WHERE name = ?""",
            (
                last_checked_at.isoformat(),
                last_fetched_at.isoformat() if last_fetched_at else None,
                etag,
                last_modified,
                name,
            ),
        )

    def increment_source_error(self, name: str, error: str) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                consecutive_errors = consecutive_errors + 1,
                last_error = ?,
                last_error_at = ?
             WHERE name = ?""",
            (error, datetime.now(UTC).isoformat(), name),
        )

    def reset_source_errors(self, name: str) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                consecutive_errors = 0,
                last_error = NULL,
                last_error_at = NULL
             WHERE name = ?""",
            (name,),
        )

    def disable_source_until(self, name: str, until: datetime) -> None:
        conn = self.connection()
        conn.execute(
            "UPDATE sources SET disabled_until = ? WHERE name = ?",
            (until.isoformat(), name),
        )

    # ── Items ──────────────────────────────────────────────────────────

    def list_pending_arxiv_items(self) -> list[sqlite3.Row]:
        """Return 'new' items whose source has subcategory='arxiv'.

        Used by the arxiv filter (pre-scoring) to decide relevance.
        """
        conn = self.connection()
        return list(
            conn.execute(
                "SELECT items.* FROM items "
                "JOIN sources ON items.source_id = sources.id "
                "WHERE items.status = 'new' AND sources.subcategory = 'arxiv'"
            ).fetchall()
        )

    def count_pending_for_scoring(self) -> int:
        """Cheap count of items the scorer would pick up (no LIMIT, no JOIN).

        Mirrors the predicate of list_items_for_scoring exactly so the two
        always agree. Used by lifecycle logging to surface backlog size at
        run end.
        """
        conn = self.connection()
        return conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE status IN ('new', 'filtered_in') AND notified_at IS NULL"
        ).fetchone()[0]

    def list_items_for_scoring(self, limit: int = 100) -> list[sqlite3.Row]:
        """Return items with status 'new' OR 'filtered_in' and no `notified_at`.

        The `notified_at IS NULL` guard protects against out-of-band suppression:
        if something (a historical bulk SQL, a migration, a debug session) sets
        `notified_at` without transitioning `status` to 'scored', the item would
        otherwise sit in the scoring queue and get silently re-scored hours or
        days later, overwriting `scored_at`. See 2026-04-20 forensic thread.

        Joined with sources to expose source_name + source_subcategory.
        """
        conn = self.connection()
        return list(
            conn.execute(
                "SELECT items.*, sources.name AS source_name, "
                "sources.subcategory AS source_subcategory "
                "FROM items JOIN sources ON items.source_id = sources.id "
                "WHERE items.status IN ('new', 'filtered_in') "
                "  AND items.notified_at IS NULL "
                "ORDER BY items.fetched_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        )

    def get_item_with_source(self, item_id: int) -> sqlite3.Row | None:
        """Fetch a single item by id, joined with sources for source_name + source_subcategory."""
        conn = self.connection()
        return conn.execute(
            "SELECT items.*, sources.name AS source_name, "
            "sources.subcategory AS source_subcategory "
            "FROM items JOIN sources ON items.source_id = sources.id "
            "WHERE items.id = ?",
            (item_id,),
        ).fetchone()

    def insert_item(
        self,
        *,
        source_id: int,
        item_hash: str,
        url: str,
        title: str,
        author: str | None,
        published_at: str | None,
        raw_summary: str | None,
        category: str,
    ) -> bool:
        """Insert item. Returns True if inserted, False if duplicate hash.

        fetched_at is set explicitly via Python so the format matches
        scored_at / notified_at (ISO-8601 with +00:00). The schema default
        datetime('now') remains as a defensive backstop only.
        """
        conn = self.connection()
        cur = conn.execute(
            """INSERT INTO items
                (source_id, item_hash, url, title, author, published_at,
                 raw_summary, category, fetched_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(item_hash) DO NOTHING""",
            (
                source_id,
                item_hash,
                url,
                title,
                author,
                published_at,
                raw_summary,
                category,
                datetime.now(UTC).isoformat(),
            ),
        )
        return cur.rowcount > 0

    def list_items_by_status(self, status: str, limit: int = 100) -> list[sqlite3.Row]:
        conn = self.connection()
        sql = (
            "SELECT items.*, sources.name AS source_name,"
            " sources.subcategory AS source_subcategory"
            " FROM items JOIN sources ON items.source_id = sources.id"
            " WHERE items.status = ? ORDER BY items.fetched_at ASC LIMIT ?"
        )
        return list(conn.execute(sql, (status, limit)).fetchall())

    def list_items_for_digest(self, since_iso: str) -> list[sqlite3.Row]:
        """Items eligible for the next digest.

        Filters:
        - status='scored' AND not already in another digest
        - scored_at >= digest-cycle cutoff (`since_iso`)
        - importance >= DIGEST_MIN_IMPORTANCE (default 3; level 2 is routine)
        - published_at >= NOW − DIGEST_MAX_ITEM_AGE_DAYS OR NULL
          (separate age-grenze, independent of `since_iso` — prevents sitemap-
          scrape backfill from surfacing years-old URLs, while still letting
          items published before the digest-cycle cutoff through as long as
          they're within the age window.)
        """
        conn = self.connection()
        age_cutoff_iso = (datetime.now(UTC) - timedelta(days=DIGEST_MAX_ITEM_AGE_DAYS)).isoformat()
        sql = (
            "SELECT items.*, sources.name AS source_name,"
            " sources.subcategory AS source_subcategory"
            " FROM items JOIN sources ON items.source_id = sources.id"
            " WHERE items.status = 'scored' AND items.included_in_digest IS NULL"
            "  AND items.scored_at >= ?"
            "  AND items.importance >= ?"
            "  AND (items.published_at IS NULL OR items.published_at >= ?)"
            " ORDER BY items.importance DESC, items.published_at DESC"
        )
        return list(
            conn.execute(sql, (since_iso, DIGEST_MIN_IMPORTANCE, age_cutoff_iso)).fetchall()
        )

    def mark_item_scored(
        self,
        *,
        item_id: int,
        importance: int,
        reason: str,
        model: str,
    ) -> None:
        """Transition an item to status='scored' + stamp scored_at.

        Uses Python's datetime (ISO-8601 with 'T' separator) so comparisons
        against other ISO strings — e.g. digest cutoffs — are lexically
        consistent, and so freezegun can control the clock in tests.
        """
        conn = self.connection()
        conn.execute(
            """UPDATE items SET
                status = 'scored', importance = ?, score_reason = ?,
                score_model = ?, scored_at = ?
             WHERE id = ?""",
            (importance, reason, model, datetime.now(UTC).isoformat(), item_id),
        )

    def mark_item_arxiv_filter(self, *, item_id: int, relevant: bool) -> None:
        new_status = "filtered_in" if relevant else "filtered_out"
        conn = self.connection()
        conn.execute(
            "UPDATE items SET status = ?, arxiv_relevant = ? WHERE id = ?",
            (new_status, int(relevant), item_id),
        )

    def mark_item_notified(self, *, item_id: int, pushed: bool = True) -> None:
        """Stamp notification time + whether the user actually got a push.

        `pushed=True` (default): a real macOS notification was sent.
        `pushed=False`: the notifier deliberately suppressed the push
        (e.g. bundling within 15 min, bulk backfill). The item is still
        considered "handled" — the scorer won't retry and it still flows
        through to the digest — but it doesn't count towards push stats
        or the bundling window.

        Uses Python's datetime (respects freezegun/test clocks) rather than
        SQLite's datetime('now') so the bundling window is test-deterministic.
        """
        conn = self.connection()
        conn.execute(
            "UPDATE items SET notified_at = ?, push_sent = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), int(pushed), item_id),
        )

    def mark_item_digested(self, *, item_id: int, digest_label: str) -> None:
        conn = self.connection()
        conn.execute(
            "UPDATE items SET included_in_digest = ? WHERE id = ?",
            (digest_label, item_id),
        )

    def count_recent_notifications_by_category(
        self,
        category: str,
        within_minutes: int,
    ) -> int:
        """Count items in `category` notified within the last `within_minutes`.

        Cutoff is computed in Python (freezegun-aware); the stored notified_at
        strings are ISO-8601 and comparable lexicographically.
        """
        cutoff_iso = (datetime.now(UTC) - timedelta(minutes=within_minutes)).isoformat()
        conn = self.connection()
        return conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE category = ? AND push_sent = 1 "
            "  AND notified_at >= ?",
            (category, cutoff_iso),
        ).fetchone()[0]

    # ── Digests ────────────────────────────────────────────────────────

    def insert_digest(
        self,
        *,
        date: str,
        slot: str,
        file_path: str,
        item_count: int,
        model: str,
    ) -> None:
        """Insert a complete digest row. Race-safe: duplicate (date, slot) silently no-ops.

        generated_at is stamped explicitly via Python so the format matches the
        rest of our timestamps (ISO-8601 with +00:00). Schema default kept as
        defensive backstop.
        """
        conn = self.connection()
        conn.execute(
            "INSERT OR IGNORE INTO digests "
            "(date, slot, file_path, item_count, model, generated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date, slot, file_path, item_count, model, datetime.now(UTC).isoformat()),
        )

    def claim_digest_slot(self, *, date: str, slot: str, model: str) -> bool:
        """Atomically reserve a (date, slot) pair before expensive work (LLM call).

        Returns True if this caller claimed the slot (safe to proceed with Opus),
        False if another process already owns it (caller must abort and not spend
        tokens). Complete the claim by calling finalize_digest after file write.
        """
        conn = self.connection()
        cursor = conn.execute(
            "INSERT OR IGNORE INTO digests "
            "(date, slot, file_path, item_count, model, generated_at) "
            "VALUES (?, ?, '', 0, ?, ?)",
            (date, slot, model, datetime.now(UTC).isoformat()),
        )
        return cursor.rowcount > 0

    def finalize_digest(self, *, date: str, slot: str, file_path: str, item_count: int) -> None:
        """Populate a claimed digest row with real file_path and item_count."""
        conn = self.connection()
        conn.execute(
            "UPDATE digests SET file_path = ?, item_count = ? WHERE date = ? AND slot = ?",
            (file_path, item_count, date, slot),
        )

    def get_digest(self, date: str, slot: str) -> sqlite3.Row | None:
        conn = self.connection()
        return conn.execute(
            "SELECT * FROM digests WHERE date = ? AND slot = ?",
            (date, slot),
        ).fetchone()

    def delete_digest(self, *, date: str, slot: str) -> None:
        """Remove the digests row for (date, slot). No-op if not present."""
        conn = self.connection()
        conn.execute(
            "DELETE FROM digests WHERE date = ? AND slot = ?",
            (date, slot),
        )

    def unmark_items_by_digest_label(self, label: str) -> None:
        """Reset `included_in_digest = NULL` for all items tagged with `label`.

        Used by --force to undo a previous digest's item-marking so the items
        re-enter `list_items_for_digest` on the regeneration pass.
        """
        conn = self.connection()
        conn.execute(
            "UPDATE items SET included_in_digest = NULL WHERE included_in_digest = ?",
            (label,),
        )

    def get_last_digest_generated_at(self, slot: str) -> str | None:
        """Return ISO timestamp of most recent generation of given slot, or None."""
        conn = self.connection()
        row = conn.execute(
            "SELECT MAX(generated_at) FROM digests WHERE slot = ?",
            (slot,),
        ).fetchone()
        return row[0] if row else None

    def get_daily_stats(self, date_iso: str) -> dict[str, int]:
        """Return today's counts: total items, fetched-today, scored-high-today, notified-today."""
        conn = self.connection()
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        fetched = conn.execute(
            "SELECT COUNT(*) FROM items WHERE DATE(fetched_at) = ?", (date_iso,)
        ).fetchone()[0]
        # Count items scored today with high importance — but exclude bulk-noise:
        # items whose notified_at was set out-of-band (historical SQL suppression)
        # and never actually pushed. Symmetric to list_items_for_scoring's guard.
        scored_high = conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE DATE(scored_at) = ? AND importance >= 4 "
            "  AND (notified_at IS NULL OR push_sent = 1)",
            (date_iso,),
        ).fetchone()[0]
        notified = conn.execute(
            "SELECT COUNT(*) FROM items WHERE DATE(notified_at) = ? AND push_sent = 1",
            (date_iso,),
        ).fetchone()[0]
        return {
            "total": total,
            "fetched_today": fetched,
            "scored_high_today": scored_high,
            "notified_today": notified,
        }
