import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from newsroom.config import Source
from newsroom.state import State


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "test.db")
    s.ensure_schema()
    return s


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
