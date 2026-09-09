import logging
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


async def test_scorer_opts_out_of_parse_retries(state_with_items: State) -> None:
    """Per-item scoring must not pay for retries the next cycle provides for free.

    Retrying here costs three Haiku calls plus ten seconds of blocking backoff in a
    sequential loop, for an item that comes back around in five minutes anyway.
    """
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"importance": 3, "reason": "ok"}
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=1)
    assert mock_client.ask.call_args.kwargs["retry_parse"] is False


async def test_scorer_leaves_item_on_parse_error(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.side_effect = ParseError("bad JSON")
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=1)
    remaining = state_with_items.list_items_by_status("new", limit=10)
    assert len(remaining) == 3  # none scored


async def test_scorer_routes_prompt_by_category(tmp_path: Path) -> None:
    """Phase 2a: ai items use score_item_ai, world items use score_item_world."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    for name, cat, sub in [
        ("anthropic", "ai", "lab"),
        ("tagesschau-news", "world", "news"),
    ]:
        state.upsert_source(
            Source(
                name=name,
                category=cat,
                subcategory=sub,
                url=f"https://example.com/{name}.rss",
                feed_type="rss",
                interval_seconds=3600,
                enabled=True,
            )
        )
    ai_id = state.get_source_by_name("anthropic")["id"]
    world_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=ai_id,
        item_hash="h-ai",
        url="https://a.com/ai",
        title="AI item",
        author=None,
        published_at="2026-04-19T02:00:00Z",
        raw_summary="body",
        category="ai",
    )
    state.insert_item(
        source_id=world_id,
        item_hash="h-world",
        url="https://t.de/world",
        title="World item",
        author=None,
        published_at="2026-04-19T02:00:00Z",
        raw_summary="body",
        category="world",
    )

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"importance": 3, "reason": "test"}
    await score_pending_items(state, agent=mock_agent)

    prompts_used = [call.kwargs["prompt_name"] for call in mock_agent.ask.call_args_list]
    # Each routing must fire exactly once — set equality would hide a duplicate-call
    # bug (e.g. AI routed twice, world once still satisfies the set).
    assert prompts_used.count("score_item_ai") == 1
    assert prompts_used.count("score_item_world") == 1
    assert len(prompts_used) == 2


async def test_scorer_emits_progress_heartbeat_every_10_items(tmp_path, caplog) -> None:
    """During long score runs (e.g. arxiv burst → 100 pending), every 10 items
    processed must emit an INFO progress line so live throughput is visible
    instead of the 4 h silence we saw on 2026-05-05.
    """
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
    for i in range(25):
        state.insert_item(
            source_id=src_id,
            item_hash=f"h-bulk-{i}",
            url=f"https://a.com/bulk/{i}",
            title=f"Bulk {i}",
            author=None,
            published_at=None,
            raw_summary=None,
            category="ai",
        )

    mock_client = AsyncMock()
    mock_client.ask.return_value = {"importance": 3, "reason": "ok"}
    with caplog.at_level(logging.INFO, logger="newsroom.scorer"):
        await score_pending_items(state, agent=mock_client, notifier=AsyncMock(), limit=100)

    progress = [
        r.getMessage()
        for r in caplog.records
        if r.name == "newsroom.scorer" and "progress" in r.getMessage().lower()
    ]
    # 25 items processed → heartbeats at 10 and 20 (final 25 doesn't trigger:
    # post-completion summary is the cli.py "score phase" line, not a heartbeat)
    assert len(progress) == 2
    assert "10/25" in progress[0]
    assert "20/25" in progress[1]
