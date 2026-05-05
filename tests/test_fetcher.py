from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time
from pytest_httpx import HTTPXMock

from newsroom.config import Source
from newsroom.fetcher import (
    FETCH_MAX_ITEM_AGE_DAYS,
    ParsedItem,
    _is_fresh,
    fetch_due_sources,
    fetch_one_raw,
    parse_feed,
    should_fetch,
)
from newsroom.state import State


def _source_row(**overrides) -> dict:
    base = dict(
        name="s",
        category="ai",
        subcategory=None,
        url="https://e.com/rss",
        feed_type="rss",
        interval_seconds=3600,
        enabled=1,
        last_fetched_at=None,
        last_checked_at=None,
        etag=None,
        last_modified=None,
        consecutive_errors=0,
        disabled_until=None,
    )
    base.update(overrides)
    return base


# ── _is_fresh: age-filter at ingest ─────────────────────────────────

_NOW = datetime(2026, 4, 20, 17, 0, tzinfo=UTC)


def test_is_fresh_recent_iso_accepted() -> None:
    assert _is_fresh("2026-04-19T10:00:00+00:00", now=_NOW) is True


def test_is_fresh_old_iso_rejected() -> None:
    assert _is_fresh("2024-01-01T00:00:00+00:00", now=_NOW) is False


def test_is_fresh_exactly_seven_days_ago_accepted() -> None:
    # Cutoff is inclusive: pub >= now - 7d → still fresh
    seven_days = (_NOW - timedelta(days=FETCH_MAX_ITEM_AGE_DAYS)).isoformat()
    assert _is_fresh(seven_days, now=_NOW) is True


def test_is_fresh_just_past_cutoff_rejected() -> None:
    # 7 days + 1 minute ago → rejected
    over = (_NOW - timedelta(days=FETCH_MAX_ITEM_AGE_DAYS, minutes=1)).isoformat()
    assert _is_fresh(over, now=_NOW) is False


def test_is_fresh_none_published_accepted() -> None:
    # Ambiguous (no date) passes through — we'd rather ingest noise than drop
    # a legitimate current item from a feed that omits dates.
    assert _is_fresh(None, now=_NOW) is True


def test_is_fresh_malformed_date_accepted() -> None:
    # Defensive: unparseable date = can't verify old → pass through
    assert _is_fresh("not-a-date", now=_NOW) is True


def test_is_fresh_naive_iso_treated_as_utc() -> None:
    # Feed without tz info → assume UTC, don't reject
    naive = (_NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert _is_fresh(naive, now=_NOW) is True


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_first_time_returns_true() -> None:
    assert should_fetch(_source_row()) is True


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_disabled_returns_false() -> None:
    assert should_fetch(_source_row(enabled=0)) is False


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_disabled_until_future_returns_false() -> None:
    future = (datetime(2026, 4, 19, 11, 0, tzinfo=UTC)).isoformat()
    assert should_fetch(_source_row(disabled_until=future)) is False


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_respects_interval_not_due() -> None:
    checked = (datetime(2026, 4, 19, 9, 30, tzinfo=UTC)).isoformat()
    assert should_fetch(_source_row(last_checked_at=checked, interval_seconds=3600)) is False


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_respects_interval_due() -> None:
    checked = (datetime(2026, 4, 19, 8, 59, tzinfo=UTC)).isoformat()
    assert should_fetch(_source_row(last_checked_at=checked, interval_seconds=3600)) is True


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_tolerates_naive_last_checked_iso() -> None:
    # naive ISO (no +00:00) — simulates a caller that forgot tzinfo
    naive_iso = "2026-04-19T08:59:00"
    assert should_fetch(_source_row(last_checked_at=naive_iso, interval_seconds=3600)) is True


async def test_fetch_one_raw_200_returns_body(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://e.com/rss", content=b"<rss/>", headers={"ETag": 'W/"x"'})
    outcome = await fetch_one_raw(_source_row())
    assert outcome.status == 200
    assert outcome.body == b"<rss/>"
    assert outcome.etag == 'W/"x"'


async def test_fetch_one_raw_304_returns_not_modified(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://e.com/rss", status_code=304)
    outcome = await fetch_one_raw(_source_row(etag='W/"old"'))
    assert outcome.status == 304
    assert outcome.body is None


async def test_fetch_one_raw_sends_conditional_headers(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url="https://e.com/rss", status_code=304)
    await fetch_one_raw(_source_row(etag='W/"e"', last_modified="Sat, 19 Apr 2026 09:00:00 GMT"))
    request = httpx_mock.get_request()
    assert request.headers.get("If-None-Match") == 'W/"e"'
    assert request.headers.get("If-Modified-Since") == "Sat, 19 Apr 2026 09:00:00 GMT"


@pytest.mark.parametrize(
    ("exc_cls", "message"),
    [
        (httpx.ConnectError, "cannot connect"),
        (httpx.ConnectTimeout, "connect timed out"),
        (httpx.ReadTimeout, "read timed out"),
        (httpx.RemoteProtocolError, "malformed response"),
    ],
)
async def test_fetch_one_raw_network_error_returns_error(
    httpx_mock: HTTPXMock, exc_cls: type, message: str
) -> None:
    httpx_mock.add_exception(exc_cls(message))
    outcome = await fetch_one_raw(_source_row())
    assert outcome.status is None
    assert outcome.error is not None
    assert message.lower() in outcome.error.lower()


def test_parse_rss(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "feed_rss_sample.xml").read_bytes()
    items = parse_feed(raw, feed_type="rss")
    assert len(items) == 2
    first = items[0]
    assert first.title == "First Item"
    assert first.url == "https://example.com/first"
    assert first.author is not None
    assert first.published_at is not None


def test_parse_atom(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "feed_atom_sample.xml").read_bytes()
    items = parse_feed(raw, feed_type="atom")
    assert len(items) == 1
    assert items[0].title == "Atom Entry One"
    assert items[0].url == "https://example.com/atom/1"
    assert items[0].author == "Atom Author"


def test_parse_json_hn(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "feed_hn_algolia.json").read_bytes()
    items = parse_feed(raw, feed_type="json")
    assert len(items) == 2
    assert items[0].title == "HN Story One"
    assert items[0].url == "https://example.com/hn-one"


def test_parse_rss_malformed_returns_empty() -> None:
    items = parse_feed(b"<not-rss/>", feed_type="rss")
    assert items == []


def test_parsed_item_has_stable_hash() -> None:
    item_a = ParsedItem(
        url="https://x.com/a", title="T", author=None, published_at=None, raw_summary=None
    )
    item_b = ParsedItem(
        url="https://x.com/a", title="T", author="different", published_at="later", raw_summary=None
    )
    assert item_a.item_hash == item_b.item_hash
    assert len(item_a.item_hash) == 16


# ── Orchestration tests ────────────────────────────────────────────────────────


def _insert_source_via_state(
    state: State,
    name: str = "s",
    interval: int = 3600,
    url: str = "https://e.com/rss",
    feed_type: str = "rss",
) -> None:
    state.upsert_source(
        Source(
            name=name,
            category="ai",
            subcategory=None,
            url=url,
            feed_type=feed_type,
            interval_seconds=interval,
            enabled=True,
        )
    )


@freeze_time("2026-04-19 10:00:00")
async def test_fetch_due_sources_inserts_items(
    state: State, httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    _insert_source_via_state(state)
    httpx_mock.add_response(
        url="https://e.com/rss",
        content=(fixtures_dir / "feed_rss_sample.xml").read_bytes(),
        headers={"ETag": 'W/"abc"'},
    )
    results = await fetch_due_sources(state)
    assert len(results) == 1
    assert results[0].items_inserted == 2
    # Items in DB
    new_items = state.list_items_by_status("new", limit=10)
    assert len(new_items) == 2


async def test_fetch_due_sources_skips_not_due(state: State, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    # Simulate recent fetch
    state.update_source_fetch_state(
        name="s",
        last_checked_at=datetime.now(UTC),
        last_fetched_at=None,
    )
    results = await fetch_due_sources(state)
    assert results == []


async def test_fetch_due_sources_handles_304(state: State, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    httpx_mock.add_response(url="https://e.com/rss", status_code=304)
    results = await fetch_due_sources(state)
    assert results[0].items_inserted == 0
    assert results[0].status == 304


async def test_fetch_due_sources_handles_network_error(state: State, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    results = await fetch_due_sources(state)
    assert results[0].error is not None
    row = state.get_source_by_name("s")
    assert row["consecutive_errors"] == 1


def test_parse_json_github_releases(fixtures_dir: Path) -> None:
    raw = (fixtures_dir / "feed_github_releases.json").read_bytes()
    items = parse_feed(raw, feed_type="json")
    assert len(items) == 2
    first = items[0]
    assert first.title == "Release 1.2.3"
    assert first.url == "https://github.com/anthropics/claude-code/releases/tag/v1.2.3"
    assert first.author == "octocat"
    assert first.published_at == "2026-04-19T10:00:00+00:00"
    # Second item: name is None → falls back to tag_name; author dict is None → None
    second = items[1]
    assert second.title == "v1.2.4"
    assert second.author is None


@freeze_time("2026-04-20 12:00:00")
async def test_fetch_due_sources_dispatches_sitemap_scrape(
    state: State, httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """End-to-end: a sitemap-scrape source goes through the new parser + inserts items.

    Frozen clock so the fetcher's freshness window (FETCH_MAX_ITEM_AGE_DAYS=7)
    doesn't reject fixture lastmods as real time advances.
    """
    state.upsert_source(
        Source(
            name="anthropic-news",
            category="ai",
            subcategory="lab",
            url="https://example.com/sitemap.xml",
            feed_type="sitemap-scrape",
            interval_seconds=3600,
            enabled=True,
            url_filter=r"^/news/",
        )
    )
    # 1) sitemap response
    httpx_mock.add_response(
        url="https://example.com/sitemap.xml",
        content=(fixtures_dir / "sitemap_varied.xml").read_bytes(),
    )
    # 2) per-URL OG responses (only /news/ paths — /about and /careers filtered out)
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}",
            content=(
                f'<html><head><meta property="og:title" content="T {slug}"></head></html>'
            ).encode(),
        )
    results = await fetch_due_sources(state)
    assert len(results) == 1
    assert results[0].items_inserted == 3  # /about and /careers filtered out
    # Items in DB
    new_items = state.list_items_by_status("new", limit=10)
    assert len(new_items) == 3
    titles = {i["title"] for i in new_items}
    assert titles == {"T first-article", "T second-article", "T third-article"}
