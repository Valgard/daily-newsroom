"""Tests for the notifier module."""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from newsroom.config import Source
from newsroom.notifier import (
    Notifier,
    compute_threshold_for_hour,
    meets_threshold,
)
from newsroom.state import State


HOUR_DAYTIME_1 = 10
HOUR_DAYTIME_2 = 15
HOUR_QUIET_1 = 23
HOUR_QUIET_2 = 3


def test_threshold_daytime() -> None:
    assert compute_threshold_for_hour(HOUR_DAYTIME_1, category="ai") == 4
    assert compute_threshold_for_hour(HOUR_DAYTIME_2, category="ai") == 4


def test_threshold_quiet_hours() -> None:
    assert compute_threshold_for_hour(HOUR_QUIET_1, category="ai") == 5
    assert compute_threshold_for_hour(HOUR_QUIET_2, category="ai") == 5


def test_threshold_arxiv_never_pushes() -> None:
    # arxiv subcategory should never push single items; effective threshold is impossible
    assert compute_threshold_for_hour(HOUR_DAYTIME_1, subcategory="arxiv") >= 6


def test_meets_threshold_true() -> None:
    item = {"importance": 5, "category": "ai"}
    assert meets_threshold(item, hour=HOUR_QUIET_1, subcategory=None)


def test_meets_threshold_false_in_quiet_hours() -> None:
    item = {"importance": 4, "category": "ai"}
    assert not meets_threshold(item, hour=HOUR_QUIET_1, subcategory=None)


def _insert_scored_item(state: State, importance: int, title: str = "T") -> int:
    state.upsert_source(Source(
        name="a", category="ai", subcategory="lab",
        url="https://a.com/rss", feed_type="rss", interval_seconds=3600,
    ))
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1", url="https://a.com/x", title=title,
        author=None, published_at=None, raw_summary="body", category="ai",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=importance, reason="r", model="haiku")
    return item_id


async def test_notifier_sends_notification_for_high_importance(state: State) -> None:
    item_id = _insert_scored_item(state, importance=5, title="Important")
    row = state.get_item_with_source(item_id)

    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    send_mock.assert_awaited_once()
    call_kwargs = send_mock.call_args.kwargs
    assert "Important" in call_kwargs.get("message", "")


async def test_notifier_skips_below_threshold(state: State) -> None:
    item_id = _insert_scored_item(state, importance=2, title="Minor")
    row = state.get_item_with_source(item_id)
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    send_mock.assert_not_awaited()


async def test_notifier_marks_item_notified(state: State) -> None:
    item_id = _insert_scored_item(state, importance=5, title="T")
    row = state.get_item_with_source(item_id)
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    updated = state.get_item_with_source(item_id)
    assert updated["notified_at"] is not None
