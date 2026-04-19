"""SQLite state manager for newsroom."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from newsroom.config import Source

DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "daily-newsroom" / "state.db"


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
                last_error_at = datetime('now')
             WHERE name = ?""",
            (error, name),
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

    def list_items_for_scoring(self, limit: int = 100) -> list[sqlite3.Row]:
        """Return items with status 'new' OR 'filtered_in', ready to be scored.

        Joined with sources to expose source_name + source_subcategory.
        """
        conn = self.connection()
        return list(
            conn.execute(
                "SELECT items.*, sources.name AS source_name, "
                "sources.subcategory AS source_subcategory "
                "FROM items JOIN sources ON items.source_id = sources.id "
                "WHERE items.status IN ('new', 'filtered_in') "
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
        """Insert item. Returns True if inserted, False if duplicate hash."""
        conn = self.connection()
        cur = conn.execute(
            """INSERT INTO items
                (source_id, item_hash, url, title, author, published_at, raw_summary, category)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(item_hash) DO NOTHING""",
            (source_id, item_hash, url, title, author, published_at, raw_summary, category),
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
        conn = self.connection()
        sql = (
            "SELECT items.*, sources.name AS source_name,"
            " sources.subcategory AS source_subcategory"
            " FROM items JOIN sources ON items.source_id = sources.id"
            " WHERE items.status = 'scored' AND items.included_in_digest IS NULL"
            "  AND items.scored_at >= ?"
            " ORDER BY items.importance DESC, items.published_at DESC"
        )
        return list(conn.execute(sql, (since_iso,)).fetchall())

    def mark_item_scored(
        self,
        *,
        item_id: int,
        importance: int,
        reason: str,
        model: str,
    ) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE items SET
                status = 'scored', importance = ?, score_reason = ?,
                score_model = ?, scored_at = datetime('now')
             WHERE id = ?""",
            (importance, reason, model, item_id),
        )

    def mark_item_arxiv_filter(self, *, item_id: int, relevant: bool) -> None:
        new_status = "filtered_in" if relevant else "filtered_out"
        conn = self.connection()
        conn.execute(
            "UPDATE items SET status = ?, arxiv_relevant = ? WHERE id = ?",
            (new_status, int(relevant), item_id),
        )

    def mark_item_notified(self, *, item_id: int) -> None:
        """Stamp notification time. Status remains 'scored' — notified_at is the record.

        Uses Python's datetime (respects freezegun/test clocks) rather than
        SQLite's datetime('now') so the bundling window is test-deterministic.
        """
        conn = self.connection()
        conn.execute(
            "UPDATE items SET notified_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), item_id),
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
            "WHERE category = ? AND notified_at IS NOT NULL "
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
        conn = self.connection()
        conn.execute(
            "INSERT INTO digests (date, slot, file_path, item_count, model) VALUES (?, ?, ?, ?, ?)",
            (date, slot, file_path, item_count, model),
        )

    def get_digest(self, date: str, slot: str) -> sqlite3.Row | None:
        conn = self.connection()
        return conn.execute(
            "SELECT * FROM digests WHERE date = ? AND slot = ?",
            (date, slot),
        ).fetchone()

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
        scored_high = conn.execute(
            "SELECT COUNT(*) FROM items WHERE DATE(scored_at) = ? AND importance >= 4",
            (date_iso,),
        ).fetchone()[0]
        notified = conn.execute(
            "SELECT COUNT(*) FROM items WHERE DATE(notified_at) = ?", (date_iso,)
        ).fetchone()[0]
        return {
            "total": total,
            "fetched_today": fetched,
            "scored_high_today": scored_high,
            "notified_today": notified,
        }
