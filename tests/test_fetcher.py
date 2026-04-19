from datetime import UTC, datetime

import httpx
from freezegun import freeze_time
from pytest_httpx import HTTPXMock

from newsroom.fetcher import fetch_one_raw, should_fetch


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


async def test_fetch_one_raw_timeout_returns_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectTimeout("timed out"))
    outcome = await fetch_one_raw(_source_row())
    assert outcome.status is None
    assert outcome.error is not None
    assert "timed out" in outcome.error.lower()
