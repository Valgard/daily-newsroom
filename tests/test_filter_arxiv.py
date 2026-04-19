from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from newsroom.config import Source
from newsroom.filter_arxiv import filter_pending_arxiv_items
from newsroom.state import State


@pytest.fixture
def state_with_arxiv_item(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="arxiv-cs-cl",
            category="ai",
            subcategory="arxiv",
            url="https://arxiv.org/rss/cs.CL",
            feed_type="rss",
            interval_seconds=86400,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("arxiv-cs-cl")["id"]
    state.insert_item(
        source_id=src_id,
        item_hash="h1",
        url="https://arxiv.org/abs/2604.12345",
        title="Agent Memory with Vector Retrieval",
        author="Author",
        published_at=None,
        raw_summary="Abstract about memory in LLM agents...",
        category="ai",
    )
    return state


async def test_filter_marks_relevant_items_filtered_in(state_with_arxiv_item: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": True, "reason": "agent memory"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    row = (
        state_with_arxiv_item.connection()
        .execute("SELECT * FROM items WHERE title LIKE '%Memory%'")
        .fetchone()
    )
    assert row["status"] == "filtered_in"
    assert row["arxiv_relevant"] == 1


async def test_filter_marks_irrelevant_filtered_out(state_with_arxiv_item: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": False, "reason": "medical imaging"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    row = state_with_arxiv_item.connection().execute("SELECT * FROM items").fetchone()
    assert row["status"] == "filtered_out"
    assert row["arxiv_relevant"] == 0


async def test_filter_only_processes_arxiv_subcategory(state_with_arxiv_item: State) -> None:
    # Add a non-arxiv item; filter should leave it alone
    state_with_arxiv_item.upsert_source(
        Source(
            name="simon-willison",
            category="ai",
            subcategory="curated",
            url="https://simonwillison.net/atom/everything/",
            feed_type="atom",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state_with_arxiv_item.get_source_by_name("simon-willison")["id"]
    state_with_arxiv_item.insert_item(
        source_id=src_id,
        item_hash="h2",
        url="https://simonwillison.net/post",
        title="Post",
        author=None,
        published_at=None,
        raw_summary="body",
        category="ai",
    )
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": True, "reason": "ok"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    # Only 1 call — the arxiv item
    assert mock_client.ask.call_count == 1
