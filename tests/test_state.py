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
    assert row["interval_seconds"] == 86400  # noqa: PLR2004
    assert row["enabled"] == 1  # noqa: PLR2004


def test_upsert_source_updates_existing(state: State) -> None:
    src = _sample_source()
    state.upsert_source(src)
    src2 = src.model_copy(update={"url": "https://arxiv.org/rss/cs.LG", "interval_seconds": 3600})
    state.upsert_source(src2)
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["url"] == "https://arxiv.org/rss/cs.LG"
    assert row["interval_seconds"] == 3600  # noqa: PLR2004


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
    assert row["consecutive_errors"] == 2  # noqa: PLR2004
    assert row["last_error"] == "connection timed out"


def test_reset_source_errors_on_success(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "err")
    state.reset_source_errors("arxiv-cs-cl")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["consecutive_errors"] == 0
    assert row["last_error"] is None
