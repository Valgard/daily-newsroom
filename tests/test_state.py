import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
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


def test_ensure_schema_runs_migration_3(tmp_path: Path) -> None:
    s = State(tmp_path / "t.db")
    s.ensure_schema()
    version = s.connection().execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 3  # noqa: PLR2004
    cols = [r["name"] for r in s.connection().execute("PRAGMA table_info(items)").fetchall()]
    assert "push_sent" in cols


def test_digests_insert_unique_per_slot(state: State) -> None:
    state.insert_digest(
        date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=20, model="opus"
    )
    with pytest.raises(Exception):
        state.insert_digest(
            date="2026-04-19", slot="morning", file_path="/tmp/m2.md", item_count=21, model="opus"
        )


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
