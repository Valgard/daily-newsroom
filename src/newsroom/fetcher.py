"""RSS/HTTP fetcher: conditional GET, feed parsing, item extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx
from dateutil import parser as dateparser

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


@dataclass
class ParsedItem:
    """A uniform item extracted from any feed type."""

    url: str
    title: str
    author: str | None
    published_at: str | None  # ISO 8601 string, or None
    raw_summary: str | None

    @property
    def item_hash(self) -> str:
        return hashlib.sha256(f"{self.url}\n{self.title}".encode()).hexdigest()[:16]


def parse_feed(raw: bytes, *, feed_type: str) -> list[ParsedItem]:
    """Parse raw feed body into uniform items. Never raises — returns [] on malformed input."""
    if feed_type in ("rss", "atom"):
        return _parse_feedparser(raw)
    if feed_type == "json":
        return _parse_json_hn(raw)
    if feed_type == "html-scrape":
        # Reserved for a later task that adds OpenAI scraping; Phase 1 primarily uses RSS
        return []
    return []


def _parse_feedparser(raw: bytes) -> list[ParsedItem]:
    parsed = feedparser.parse(raw)
    if not parsed.entries:
        return []

    items: list[ParsedItem] = []
    for entry in parsed.entries:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            continue
        author = entry.get("author")
        published = entry.get("published") or entry.get("updated")
        summary = entry.get("summary")
        items.append(
            ParsedItem(
                url=url,
                title=title,
                author=author,
                published_at=_normalize_date(published),
                raw_summary=summary,
            )
        )
    return items


def _parse_json_hn(raw: bytes) -> list[ParsedItem]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    hits = data.get("hits", [])
    items: list[ParsedItem] = []
    for hit in hits:
        url = hit.get("url")
        title = hit.get("title")
        if not url or not title:
            continue
        items.append(
            ParsedItem(
                url=url,
                title=title,
                author=hit.get("author"),
                published_at=_normalize_date(hit.get("created_at")),
                raw_summary=f"HN points: {hit.get('points', 0)}",
            )
        )
    return items


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return dateparser.parse(raw).isoformat()
    except (ValueError, OverflowError, TypeError):
        return None
