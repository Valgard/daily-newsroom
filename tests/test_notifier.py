"""Tests for the notifier module."""

import logging
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


def test_threshold_arxiv_pushes_only_at_imp5() -> None:
    # arxiv: paradigm-shifting papers (imp=5) push; lower scores go via digest only.
    # Constant across day and night — symmetric with the night quiet-hours threshold.
    # category="ai" is required: the table resolves ("ai", "arxiv") → (5, 5).
    assert compute_threshold_for_hour(HOUR_DAYTIME_1, category="ai", subcategory="arxiv") == 5
    assert compute_threshold_for_hour(HOUR_QUIET_1, category="ai", subcategory="arxiv") == 5


def test_meets_threshold_arxiv_imp5_pushes() -> None:
    item = {"importance": 5, "category": "ai"}
    assert meets_threshold(item, hour=HOUR_DAYTIME_1, subcategory="arxiv")


def test_meets_threshold_arxiv_imp4_blocked() -> None:
    item = {"importance": 4, "category": "ai"}
    assert not meets_threshold(item, hour=HOUR_DAYTIME_1, subcategory="arxiv")


def test_meets_threshold_true() -> None:
    item = {"importance": 5, "category": "ai"}
    assert meets_threshold(item, hour=HOUR_QUIET_1, subcategory=None)


def test_meets_threshold_false_in_quiet_hours() -> None:
    item = {"importance": 4, "category": "ai"}
    assert not meets_threshold(item, hour=HOUR_QUIET_1, subcategory=None)


def _insert_scored_item(state: State, importance: int, title: str = "T") -> int:
    state.upsert_source(
        Source(
            name="a",
            category="ai",
            subcategory="lab",
            url="https://a.com/rss",
            feed_type="rss",
            interval_seconds=3600,
        )
    )
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id,
        item_hash="h1",
        url="https://a.com/x",
        title=title,
        author=None,
        published_at=None,
        raw_summary="body",
        category="ai",
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


# ── bundling (spec §4.3) ──────────────────────────────────────────────


def _insert_second_scored_item(state: State, importance: int, title: str) -> int:
    """Add a second item to the existing 'a' source; assumes source already present."""
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id,
        item_hash=f"h-{title}",
        url=f"https://a.com/{title}",
        title=title,
        author=None,
        published_at=None,
        raw_summary="body",
        category="ai",
    )
    new_item = [i for i in state.list_items_by_status("new", limit=10) if i["title"] == title][0]
    state.mark_item_scored(item_id=new_item["id"], importance=importance, reason="r", model="haiku")
    return new_item["id"]


async def test_notifier_suppresses_second_push_within_15min(state: State) -> None:
    """Second importance-5 item in same category within 15 min → no push, marked as notified."""
    item1_id = _insert_scored_item(state, importance=5, title="First")
    row1 = state.get_item_with_source(item1_id)
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)

    # First push at 10:00 — succeeds
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row1, state)
    assert send_mock.await_count == 1

    # Second item 5 min later — same category, should be suppressed
    item2_id = _insert_second_scored_item(state, importance=5, title="Second")
    row2 = state.get_item_with_source(item2_id)
    with freeze_time("2026-04-19 10:05:00"):
        await notifier.maybe_notify(row2, state)
    assert send_mock.await_count == 1  # still only one push

    # But the second item IS marked as notified so the scorer doesn't retry it
    updated2 = state.get_item_with_source(item2_id)
    assert updated2["notified_at"] is not None
    # And push_sent=0 — a suppression, not a real push (for stats + bundling counting)
    assert updated2["push_sent"] == 0


async def test_notifier_pushes_again_after_15min_window(state: State) -> None:
    """After 15 min, the bundling window expires → a new push is allowed."""
    item1_id = _insert_scored_item(state, importance=5, title="First")
    row1 = state.get_item_with_source(item1_id)
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)

    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row1, state)
    assert send_mock.await_count == 1

    # 16 min later — outside bundling window, push allowed again
    item2_id = _insert_second_scored_item(state, importance=5, title="Second")
    row2 = state.get_item_with_source(item2_id)
    with freeze_time("2026-04-19 10:16:00"):
        await notifier.maybe_notify(row2, state)
    assert send_mock.await_count == 2  # noqa: PLR2004


# ── digest-ready notification (spec §4.4 step 10) ─────────────────────


# ── Phase-2a Welt subcategory tests (new; must fail before migration) ─


def test_threshold_world_breaking_lowers_to_3_daytime() -> None:
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="breaking")
    assert threshold == 3


def test_threshold_world_breaking_uses_4_quiet() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="world", subcategory="breaking")
    assert threshold == 4


def test_threshold_world_news_uses_4_daytime() -> None:
    """Welt news/analysis fall through to (world, *) wildcard — same as Phase-1 ai."""
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="news")
    assert threshold == 4


def test_threshold_world_analysis_uses_4_daytime() -> None:
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="analysis")
    assert threshold == 4


def test_threshold_unknown_category_falls_through_to_default() -> None:
    """Phase-3 readiness: a future tech category pushes Phase-1-default until tuned."""
    threshold = compute_threshold_for_hour(hour=10, category="tech", subcategory="news")
    assert threshold == 4


# ── Phase-1 regression tests (written before table migration) ─────────


def test_threshold_arxiv_returns_5_daytime_phase1_regression() -> None:
    """7d90fbc: arxiv pushes only at imp=5 even at daytime — must survive table migration."""
    threshold = compute_threshold_for_hour(hour=10, category="ai", subcategory="arxiv")
    assert threshold == 5


def test_threshold_arxiv_returns_5_quiet_phase1_regression() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="ai", subcategory="arxiv")
    assert threshold == 5


def test_threshold_ai_lab_returns_4_daytime_phase1_regression() -> None:
    """Phase-1 baseline: non-arxiv ai items push at imp=4 daytime."""
    threshold = compute_threshold_for_hour(hour=10, category="ai", subcategory="lab")
    assert threshold == 4


def test_threshold_ai_lab_returns_5_quiet_phase1_regression() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="ai", subcategory="lab")
    assert threshold == 5


# ── digest-ready notification (spec §4.4 step 10) ─────────────────────


async def test_notify_digest_ready_morning_format() -> None:
    """Morning slot produces 'Morgen-Digest bereit (N Items)' message."""
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    await notifier.notify_digest_ready(
        slot="morning",
        item_count=20,
        file_path="/tmp/2026-04-19.md",
    )
    send_mock.assert_awaited_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["title"] == "Newsroom"
    assert kwargs["message"] == "Morgen-Digest bereit (20 Items)"
    assert kwargs["url"] == "file:///tmp/2026-04-19.md"


async def test_notify_digest_ready_evening_format() -> None:
    """Evening slot produces 'Abend-Digest bereit (N Items)' message."""
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    await notifier.notify_digest_ready(
        slot="evening",
        item_count=14,
        file_path="/tmp/2026-04-19.md",
    )
    send_mock.assert_awaited_once()
    assert send_mock.call_args.kwargs["message"] == "Abend-Digest bereit (14 Items)"


async def test_notify_digest_ready_send_failure_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the send_fn raises, notify_digest_ready logs a warning and returns normally."""

    async def raising_send(**_kwargs: object) -> None:
        raise RuntimeError("pync down")

    notifier = Notifier(send_fn=raising_send)
    with caplog.at_level(logging.WARNING, logger="newsroom.notifier"):
        await notifier.notify_digest_ready(
            slot="morning",
            item_count=1,
            file_path="/tmp/x.md",
        )
    assert any("digest notification send failed" in rec.message for rec in caplog.records)
