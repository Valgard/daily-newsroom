import hashlib
from datetime import UTC, datetime
from pathlib import Path

from freezegun import freeze_time

from newsroom.config import Source
from newsroom.state import State


def _sample_source() -> Source:
    return Source(
        name="arxiv-cs-cl",
        category="ai",
        subcategory="arxiv",
        url="https://arxiv.org/rss/cs.CL",
        feed_type="rss",
        interval_seconds=86400,
        enabled=True,
    )


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    s = State(tmp_path / "t.db")
    s.ensure_schema()
    s.ensure_schema()  # no error
    version = s.connection().execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 1


def test_upsert_source_inserts_new(state: State) -> None:
    src = _sample_source()
    state.upsert_source(src)
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row is not None
    assert row["url"] == src.url
    assert row["interval_seconds"] == 86400
    assert row["enabled"] == 1


def test_upsert_source_persists_url_filter(state: State) -> None:
    src = _sample_source().model_copy(
        update={
            "name": "anthropic-news",
            "feed_type": "sitemap-scrape",
            "url": "https://www.anthropic.com/sitemap.xml",
            "url_filter": r"^/news/[^/]+$",
        }
    )
    state.upsert_source(src)
    row = state.get_source_by_name("anthropic-news")
    assert row["feed_type"] == "sitemap-scrape"
    assert row["url_filter"] == r"^/news/[^/]+$"


def test_upsert_source_url_filter_defaults_to_null(state: State) -> None:
    # RSS sources without url_filter should store NULL
    state.upsert_source(_sample_source())
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["url_filter"] is None


def test_ensure_schema_runs_migration_2(tmp_path: Path) -> None:
    # Schema version after ensure_schema must be >= 2 once migration 2 is defined
    s = State(tmp_path / "t.db")
    s.ensure_schema()
    version = s.connection().execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 2  # noqa: PLR2004
    # url_filter column present on sources
    cols = [r["name"] for r in s.connection().execute("PRAGMA table_info(sources)").fetchall()]
    assert "url_filter" in cols


def test_upsert_source_updates_existing(state: State) -> None:
    src = _sample_source()
    state.upsert_source(src)
    src2 = src.model_copy(update={"url": "https://arxiv.org/rss/cs.LG", "interval_seconds": 3600})
    state.upsert_source(src2)
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["url"] == "https://arxiv.org/rss/cs.LG"
    assert row["interval_seconds"] == 3600


def test_list_enabled_sources_excludes_disabled(state: State) -> None:
    state.upsert_source(_sample_source())
    state.upsert_source(_sample_source().model_copy(update={"name": "disabled", "enabled": False}))
    enabled = state.list_enabled_sources()
    assert len(enabled) == 1
    assert enabled[0]["name"] == "arxiv-cs-cl"


def test_update_source_fetch_state(state: State) -> None:
    state.upsert_source(_sample_source())
    now = datetime(2026, 4, 19, 10, 30, tzinfo=UTC)
    state.update_source_fetch_state(
        name="arxiv-cs-cl",
        last_checked_at=now,
        last_fetched_at=now,
        etag='W/"abc"',
        last_modified="Sat, 19 Apr 2026 10:30:00 GMT",
    )
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["etag"] == 'W/"abc"'
    assert row["last_modified"] == "Sat, 19 Apr 2026 10:30:00 GMT"


def test_increment_source_error(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "connection timed out")
    state.increment_source_error("arxiv-cs-cl", "connection timed out")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["consecutive_errors"] == 2
    assert row["last_error"] == "connection timed out"


def test_reset_source_errors_on_success(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "err")
    state.reset_source_errors("arxiv-cs-cl")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["consecutive_errors"] == 0
    assert row["last_error"] is None


def _item_hash(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}\n{title}".encode()).hexdigest()[:16]


def _sample_item_kwargs(source_id: int, title: str = "Test title") -> dict:
    return {
        "source_id": source_id,
        "item_hash": _item_hash("https://example.com/a", title),
        "url": "https://example.com/a",
        "title": title,
        "author": "Author A",
        "published_at": "2026-04-19T10:00:00Z",
        "raw_summary": "Summary of A",
        "category": "ai",
    }


def test_insert_item_returns_inserted_flag(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    kwargs = _sample_item_kwargs(src["id"])
    first = state.insert_item(**kwargs)
    second = state.insert_item(**kwargs)  # same hash
    assert first is True
    assert second is False


def test_insert_item_different_title_different_hash(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="A"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="B"))
    rows = state.connection().execute("SELECT COUNT(*) FROM items").fetchone()
    assert rows[0] == 2


def test_list_items_by_status(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T2"))
    news = state.list_items_by_status("new", limit=100)
    assert len(news) == 2


def test_list_items_for_scoring_excludes_already_notified(state: State) -> None:
    """Bulk-suppression safety: an item whose `notified_at` was set out-of-band
    (e.g. a historical bulk SQL suppression) must not be picked up for scoring
    later, even if its status is still 'new'. Otherwise the scorer silently
    re-stamps `scored_at` to "today" while the Notifier's early-return swallows
    the push — producing ghost items in daily stats.
    """
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T2"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T3"))
    # Simulate bulk-suppression on T2: notified_at set without touching status.
    t2 = next(r for r in state.list_items_by_status("new", limit=10) if r["title"] == "T2")
    state.mark_item_notified(item_id=t2["id"], pushed=False)

    pending_titles = {r["title"] for r in state.list_items_for_scoring(limit=10)}
    assert pending_titles == {"T1", "T3"}


def test_mark_item_scored(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_scored(
        item_id=item["id"], importance=4, reason="releases new feature", model="claude-haiku-4-5"
    )
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "scored"
    assert row["importance"] == 4
    assert row["score_reason"] == "releases new feature"


def test_mark_item_filtered_out(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_arxiv_filter(item_id=item["id"], relevant=False)
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "filtered_out"
    assert row["arxiv_relevant"] == 0


def test_mark_item_notified(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_notified(item_id=item["id"])
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["notified_at"] is not None
    assert row["push_sent"] == 1  # default: a real push


def test_mark_item_notified_suppressed(state: State) -> None:
    """pushed=False stamps notified_at but marks push_sent=0 (bundling suppression)."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_notified(item_id=item["id"], pushed=False)
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["notified_at"] is not None
    assert row["push_sent"] == 0


def test_count_recent_notifications_ignores_suppressed(state: State) -> None:
    """count_recent_notifications_by_category must ignore push-suppressed items."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    # Suppressed notification — should NOT count towards recent-push bundling
    state.mark_item_notified(item_id=item["id"], pushed=False)
    assert state.count_recent_notifications_by_category("ai", within_minutes=15) == 0


def test_daily_stats_notified_counts_only_real_pushes(state: State) -> None:
    """get_daily_stats['notified_today'] counts push_sent=1 only."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="real"))
    state.insert_item(
        **{
            **_sample_item_kwargs(src["id"], title="suppressed"),
            "item_hash": "h-suppressed",
            "url": "https://example.com/b",
        }
    )
    items = state.list_items_by_status("new", limit=10)
    state.mark_item_notified(item_id=items[0]["id"])  # real
    state.mark_item_notified(item_id=items[1]["id"], pushed=False)  # suppressed

    today = datetime.now(UTC).date().isoformat()
    stats = state.get_daily_stats(today)
    assert stats["notified_today"] == 1  # suppressed does not count


def test_daily_stats_scored_high_excludes_bulk_notified(state: State) -> None:
    """scored_high_today counts *push-eligible* scorings today. Items whose
    notified_at was set out-of-band (bulk-SQL suppression) and never actually
    pushed (push_sent=0) are archive-noise from a historical event — not news
    today, even if the scorer re-stamped scored_at later.
    """
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="real-news"))
    state.insert_item(
        **{
            **_sample_item_kwargs(src["id"], title="bulk-noise"),
            "item_hash": "h-bulk",
            "url": "https://example.com/c",
        }
    )
    items = state.list_items_by_status("new", limit=10)
    real = next(r for r in items if r["title"] == "real-news")
    noise = next(r for r in items if r["title"] == "bulk-noise")
    # Real: scored today, untouched by bulk-suppression
    state.mark_item_scored(item_id=real["id"], importance=4, reason="x", model="m")
    # Bulk-noise: notified_at set first (suppression), THEN scored today
    state.mark_item_notified(item_id=noise["id"], pushed=False)
    state.mark_item_scored(item_id=noise["id"], importance=4, reason="x", model="m")

    today = datetime.now(UTC).date().isoformat()
    stats = state.get_daily_stats(today)
    assert stats["scored_high_today"] == 1  # bulk-noise excluded


def test_ensure_schema_runs_migration_3(tmp_path: Path) -> None:
    s = State(tmp_path / "t.db")
    s.ensure_schema()
    version = s.connection().execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 3  # noqa: PLR2004
    cols = [r["name"] for r in s.connection().execute("PRAGMA table_info(items)").fetchall()]
    assert "push_sent" in cols


def test_insert_digest_duplicate_is_silently_ignored(state: State) -> None:
    """Concurrent insert of same (date, slot) must be race-safe: loser no-ops,
    winner's row stays intact. Previously this raised IntegrityError."""
    state.insert_digest(
        date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=20, model="opus"
    )
    # Must NOT raise IntegrityError — the UNIQUE constraint is enforced via
    # INSERT OR IGNORE (silent no-op), not Python exceptions.
    state.insert_digest(
        date="2026-04-19", slot="morning", file_path="/tmp/m2.md", item_count=21, model="opus"
    )
    row = state.get_digest("2026-04-19", "morning")
    assert row is not None
    # First-writer-wins: the duplicate insert left the original row untouched.
    assert row["file_path"] == "/tmp/m.md"
    assert row["item_count"] == 20


def test_claim_digest_slot_first_call_succeeds(state: State) -> None:
    """First claim of a (date, slot) pair returns True and creates a placeholder row."""
    assert state.claim_digest_slot(date="2026-04-19", slot="morning", model="opus") is True
    row = state.get_digest("2026-04-19", "morning")
    assert row is not None
    assert row["model"] == "opus"
    # Placeholder values: real file_path / item_count arrive via finalize_digest
    assert row["file_path"] == ""
    assert row["item_count"] == 0


def test_claim_digest_slot_second_call_for_same_slot_fails(state: State) -> None:
    """Second claim of the same (date, slot) returns False — slot is owned by the first claimer."""
    assert state.claim_digest_slot(date="2026-04-19", slot="morning", model="opus") is True
    assert state.claim_digest_slot(date="2026-04-19", slot="morning", model="opus") is False


def test_claim_digest_slot_different_slots_are_independent(state: State) -> None:
    """Morning and evening for the same date are separate slots — both claimable."""
    assert state.claim_digest_slot(date="2026-04-19", slot="morning", model="opus") is True
    assert state.claim_digest_slot(date="2026-04-19", slot="evening", model="opus") is True


def test_finalize_digest_updates_claim_row(state: State) -> None:
    """After claim+finalize, get_digest returns real file_path and item_count."""
    state.claim_digest_slot(date="2026-04-19", slot="morning", model="opus")
    state.finalize_digest(date="2026-04-19", slot="morning", file_path="/tmp/out.md", item_count=42)
    row = state.get_digest("2026-04-19", "morning")
    assert row["file_path"] == "/tmp/out.md"
    assert row["item_count"] == 42
    assert row["model"] == "opus"  # model set at claim time, preserved through finalize


def test_get_digest_returns_existing(state: State) -> None:
    state.insert_digest(
        date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=20, model="opus"
    )
    row = state.get_digest("2026-04-19", "morning")
    assert row is not None
    assert row["item_count"] == 20


def test_get_digest_returns_none_for_missing(state: State) -> None:
    assert state.get_digest("2026-04-19", "morning") is None


def test_delete_digest_removes_row(state: State) -> None:
    state.insert_digest(
        date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=5, model="opus"
    )
    assert state.get_digest("2026-04-19", "morning") is not None
    state.delete_digest(date="2026-04-19", slot="morning")
    assert state.get_digest("2026-04-19", "morning") is None


def test_delete_digest_is_idempotent(state: State) -> None:
    """Deleting a non-existent digest is a no-op, not an error."""
    state.delete_digest(date="2026-04-19", slot="evening")


@freeze_time("2026-04-19 22:30:00")
def test_list_items_for_digest_excludes_low_importance(state: State) -> None:
    """Digest must include only importance >= 3 — level 2 is routine noise."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    for title, importance in [("High", 5), ("Mid", 3), ("Low", 2), ("Trivia", 1)]:
        state.insert_item(
            source_id=src["id"],
            item_hash=f"h-{title}",
            url=f"https://e.com/{title}",
            title=title,
            author=None,
            published_at="2026-04-19T10:00:00+00:00",
            raw_summary="body",
            category="ai",
        )
        item = next(i for i in state.list_items_by_status("new", limit=10) if i["title"] == title)
        state.mark_item_scored(item_id=item["id"], importance=importance, reason="r", model="h")

    got = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    titles = {r["title"] for r in got}
    assert titles == {"High", "Mid"}


@freeze_time("2026-04-19 22:30:00")
def test_list_items_for_digest_excludes_items_older_than_7_days(state: State) -> None:
    """Historical sitemap-scrape items (published years ago) must not appear."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(
        source_id=src["id"],
        item_hash="h-ancient",
        url="https://e.com/old",
        title="Ancient article",
        author=None,
        published_at="2023-08-15T10:00:00+00:00",  # years old
        raw_summary="body",
        category="ai",
    )
    state.insert_item(
        source_id=src["id"],
        item_hash="h-8d",
        url="https://e.com/eightdays",
        title="8-day-old article",
        author=None,
        published_at="2026-04-11T10:00:00+00:00",  # 8 days ago
        raw_summary="body",
        category="ai",
    )
    state.insert_item(
        source_id=src["id"],
        item_hash="h-3d",
        url="https://e.com/3d",
        title="3-day-old article",
        author=None,
        published_at="2026-04-16T10:00:00+00:00",  # 3 days ago → within 7-day window
        raw_summary="body",
        category="ai",
    )
    for title in ("Ancient article", "8-day-old article", "3-day-old article"):
        item = next(i for i in state.list_items_by_status("new", limit=10) if i["title"] == title)
        state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="h")

    got = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    titles = {r["title"] for r in got}
    # Only within-7-day items pass; ancient + 8-day out
    assert titles == {"3-day-old article"}


@freeze_time("2026-04-20 07:00:00")
def test_list_items_for_digest_includes_item_published_before_digest_cutoff(
    state: State,
) -> None:
    """The cutoff-loophole fix: item published before digest cutoff but scored after.

    Scenario: article published yesterday 14:30, scored today 06:45 (evening-digest
    at 22:30 missed it because still 'new' then). Today's morning-digest has
    cutoff=22:30 yesterday. Previous implementation excluded this item because
    published_at (14:30 yesterday) < cutoff (22:30 yesterday). Fixed: published_at
    just needs to be within 7 days of now, not >= digest cutoff.
    """
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(
        source_id=src["id"],
        item_hash="h-pre-cutoff",
        url="https://e.com/x",
        title="Published yesterday afternoon",
        author=None,
        published_at="2026-04-19T14:30:00+00:00",
        raw_summary="body",
        category="ai",
    )
    item = state.list_items_by_status("new", limit=10)[0]
    state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="h")
    # Morning-digest cutoff: last evening-generated_at = 2026-04-19T22:30 (yesterday)
    got = state.list_items_for_digest(since_iso="2026-04-19T22:30:00")
    titles = {r["title"] for r in got}
    assert titles == {"Published yesterday afternoon"}


def test_list_items_for_digest_includes_items_without_published_at(state: State) -> None:
    """Items with published_at=NULL still appear (fallback to scored_at semantics)."""
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(
        source_id=src["id"],
        item_hash="h-nopub",
        url="https://e.com/nopub",
        title="No publish date",
        author=None,
        published_at=None,
        raw_summary="body",
        category="ai",
    )
    item = state.list_items_by_status("new", limit=10)[0]
    state.mark_item_scored(item_id=item["id"], importance=5, reason="r", model="h")
    got = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    assert len(got) == 1
    assert got[0]["title"] == "No publish date"


@freeze_time("2026-04-19 22:30:00")
def test_list_items_for_digest_tertiary_sort_alphabetical(state: State) -> None:
    """Equal importance + equal published_at → alphabetical title (case-insensitive).

    Edge case that real arXiv-bulk drops trigger: many papers share the same
    daily timestamp. Without a tertiary key the order would be undefined.
    """
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    same_ts = "2026-04-19T10:00:00+00:00"
    # Insertion order is intentionally NOT alphabetical — we want to prove
    # SQL re-orders, not insertion-order leak-through. Mixed case to exercise
    # COLLATE NOCASE.
    titles = ["Zeta paper", "alpha paper", "Mu paper", "beta paper"]
    for title in titles:
        state.insert_item(
            source_id=src["id"],
            item_hash=f"h-{title}",
            url=f"https://e.com/{title}",
            title=title,
            author=None,
            published_at=same_ts,
            raw_summary="body",
            category="ai",
        )
        item = next(i for i in state.list_items_by_status("new", limit=10) if i["title"] == title)
        state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="h")

    got = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    assert [r["title"] for r in got] == ["alpha paper", "beta paper", "Mu paper", "Zeta paper"]


def test_unmark_items_by_digest_label_clears_mark(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    state.insert_item(
        **{
            **_sample_item_kwargs(src["id"], title="T2"),
            "item_hash": "h-t2",
            "url": "https://example.com/b",
        }
    )
    items = state.list_items_by_status("new", limit=10)
    state.mark_item_digested(item_id=items[0]["id"], digest_label="2026-04-19-evening")
    state.mark_item_digested(item_id=items[1]["id"], digest_label="2026-04-19-morning")

    state.unmark_items_by_digest_label("2026-04-19-evening")

    rows = state.connection().execute("SELECT included_in_digest FROM items ORDER BY id").fetchall()
    # T1 (was evening-marked) → now NULL; T2 (morning-marked) → unchanged
    assert rows[0]["included_in_digest"] is None
    assert rows[1]["included_in_digest"] == "2026-04-19-morning"


# ── Timestamp consistency: all operational stamps are ISO-8601 UTC with +00:00 ──
# (Was inconsistent: fetched_at, generated_at, last_error_at used SQLite's
#  datetime('now') — naive, no offset suffix — while scored_at / notified_at
#  used Python's datetime.now(UTC).isoformat(). Lex-comparison and TZ-aware
#  diagnostics demand consistency.)


@freeze_time("2026-05-05 06:09:42", tz_offset=0)
def test_insert_item_stamps_fetched_at_as_utc_iso8601(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"]))
    fa = state.connection().execute("SELECT fetched_at FROM items").fetchone()["fetched_at"]
    assert fa.startswith("2026-05-05T06:09:42")
    assert fa.endswith("+00:00")


@freeze_time("2026-05-05 06:09:42", tz_offset=0)
def test_increment_source_error_stamps_iso8601_utc(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "boom")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["last_error_at"].startswith("2026-05-05T06:09:42")
    assert row["last_error_at"].endswith("+00:00")


@freeze_time("2026-05-05 06:09:42", tz_offset=0)
def test_insert_digest_stamps_generated_at_iso8601_utc(state: State) -> None:
    state.insert_digest(
        date="2026-05-05", slot="morning", file_path="/tmp/x.md", item_count=1, model="m"
    )
    row = (
        state.connection()
        .execute("SELECT generated_at FROM digests WHERE date='2026-05-05' AND slot='morning'")
        .fetchone()
    )
    assert row["generated_at"].startswith("2026-05-05T06:09:42")
    assert row["generated_at"].endswith("+00:00")


def test_migration_4_normalizes_legacy_timestamps(tmp_path: Path) -> None:
    """Migration 4 converts pre-existing 'YYYY-MM-DD HH:MM:SS' values to
    'YYYY-MM-DDTHH:MM:SS+00:00' so lex-sort and TZ-aware comparisons work.

    Strategy: run ensure_schema (applies all migrations including 4), then
    forcibly DOWNGRADE one row to the legacy format and roll schema_version
    back to 3, then re-run ensure_schema → migration 4 should re-run and
    normalize the value.
    """
    db = tmp_path / "legacy.db"
    s = State(db)
    s.ensure_schema()
    src = _sample_source()
    s.upsert_source(src)
    src_row = s.get_source_by_name(src.name)
    # Insert via raw SQL with legacy fetched_at format (the old datetime('now') shape)
    conn = s.connection()
    conn.execute(
        "INSERT INTO items (source_id, item_hash, url, title, category, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (src_row["id"], "h-legacy", "https://x/y", "T", "ai", "2026-04-20 06:09:42"),
    )
    # Also seed legacy generated_at in digests + last_error_at on a source
    conn.execute(
        "INSERT INTO digests (date, slot, file_path, item_count, model, generated_at) "
        "VALUES ('2026-04-20', 'morning', '/p.md', 1, 'm', '2026-04-20 06:00:00')"
    )
    conn.execute(
        "UPDATE sources SET last_error_at = '2026-04-20 06:30:00' WHERE name = ?",
        (src.name,),
    )
    # Roll back migration 4 so it re-fires
    conn.execute("DELETE FROM schema_version WHERE version = 4")
    s.ensure_schema()
    fa = conn.execute("SELECT fetched_at FROM items WHERE item_hash='h-legacy'").fetchone()[0]
    ga = conn.execute("SELECT generated_at FROM digests WHERE date='2026-04-20'").fetchone()[0]
    lea = conn.execute("SELECT last_error_at FROM sources WHERE name=?", (src.name,)).fetchone()[0]
    assert fa == "2026-04-20T06:09:42+00:00"
    assert ga == "2026-04-20T06:00:00+00:00"
    assert lea == "2026-04-20T06:30:00+00:00"


def test_count_pending_for_scoring_matches_list_query(state: State) -> None:
    """count_pending_for_scoring is the cheap counterpart to list_items_for_scoring;
    counts must agree on the same {status IN ('new','filtered_in') AND notified_at IS NULL}
    predicate. Used by lifecycle-logging in cli.py to surface backlog size after each run.
    """
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T2"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T3"))
    # T2 → filtered_in (still in scoring queue)
    t2 = next(r for r in state.list_items_by_status("new", limit=10) if r["title"] == "T2")
    state.mark_item_arxiv_filter(item_id=t2["id"], relevant=True)
    # T3 → notified out-of-band → must NOT be counted
    t3 = next(r for r in state.list_items_by_status("new", limit=10) if r["title"] == "T3")
    state.mark_item_notified(item_id=t3["id"], pushed=False)

    assert state.count_pending_for_scoring() == 2  # T1 (new) + T2 (filtered_in)
    # Symmetry: count and list agree
    assert state.count_pending_for_scoring() == len(state.list_items_for_scoring(limit=100))
