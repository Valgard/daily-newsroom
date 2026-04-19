from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from newsroom.agent_client import ParseError
from newsroom.config import Source
from newsroom.scorer import score_pending_items
from newsroom.state import State


@pytest.fixture
def state_with_items(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="anthropic",
            category="ai",
            subcategory="lab",
            url="https://www.anthropic.com/news/rss.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("anthropic")["id"]
    for i in range(3):
        state.insert_item(
            source_id=src_id,
            item_hash=f"h{i}",
            url=f"https://a.com/{i}",
            title=f"Title {i}",
            author=None,
            published_at=None,
            raw_summary=f"summary {i}",
            category="ai",
        )
    return state


async def test_scorer_writes_importance(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.side_effect = [
        {"importance": 5, "reason": "major release"},
        {"importance": 3, "reason": "interesting post"},
        {"importance": 2, "reason": "minor update"},
    ]
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=10)
    rows = (
        state_with_items.connection()
        .execute("SELECT * FROM items ORDER BY importance DESC")
        .fetchall()
    )
    assert [r["importance"] for r in rows] == [5, 3, 2]
    assert all(r["status"] == "scored" for r in rows)
    assert notifier_mock.maybe_notify.call_count == 3


async def test_scorer_limits_batch_size(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"importance": 3, "reason": "ok"}
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=2)
    assert mock_client.ask.call_count == 2
    remaining = state_with_items.list_items_by_status("new", limit=10)
    assert len(remaining) == 1


async def test_scorer_leaves_item_on_parse_error(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.side_effect = ParseError("bad JSON")
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=1)
    remaining = state_with_items.list_items_by_status("new", limit=10)
    assert len(remaining) == 3  # none scored
