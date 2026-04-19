"""RSS/HTTP fetcher: conditional GET, feed parsing, item extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

USER_AGENT = "daily-newsroom/0.1 (+https://github.com/none)"
DEFAULT_TIMEOUT = 30.0
HTTP_NOT_MODIFIED = 304
HTTP_OK = 200


@dataclass
class FetchOutcome:
    """Result of one HTTP fetch attempt."""

    status: int | None  # None = network error
    body: bytes | None  # None on 304 or error
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None  # human-readable for transient errors


def should_fetch(source_row: dict[str, Any] | Any) -> bool:
    """Decide whether a source is due for fetching, given its DB row."""
    # sqlite3.Row supports dict-like access
    enabled = source_row["enabled"] if "enabled" in _keys(source_row) else 1
    if not enabled:
        return False

    disabled_until = source_row["disabled_until"] if "disabled_until" in _keys(source_row) else None
    now = datetime.now(UTC)
    if disabled_until and datetime.fromisoformat(disabled_until).astimezone(UTC) > now:
        return False

    last_checked = source_row["last_checked_at"] if "last_checked_at" in _keys(source_row) else None
    if last_checked is None:
        return True

    elapsed = (now - datetime.fromisoformat(last_checked).astimezone(UTC)).total_seconds()
    return elapsed >= source_row["interval_seconds"]


def _keys(row: Any) -> list[str]:
    """Get column names from sqlite3.Row or dict (both support .keys())."""
    return list(row.keys())


async def fetch_one_raw(source_row: dict[str, Any] | Any) -> FetchOutcome:
    """Make one HTTP request with conditional GET headers. Does NOT parse body."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    etag = source_row["etag"] if "etag" in _keys(source_row) else None
    lm = source_row["last_modified"] if "last_modified" in _keys(source_row) else None
    if etag:
        headers["If-None-Match"] = etag
    if lm:
        headers["If-Modified-Since"] = lm

    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(source_row["url"], headers=headers)
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    ) as e:
        return FetchOutcome(status=None, body=None, error=f"network error: {e}")

    if response.status_code == HTTP_NOT_MODIFIED:
        return FetchOutcome(status=HTTP_NOT_MODIFIED, body=None)

    return FetchOutcome(
        status=response.status_code,
        body=response.content if response.status_code == HTTP_OK else None,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
    )
