"""RSS/HTTP fetcher: conditional GET, feed parsing, item extraction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from newsroom.state import State

import feedparser
import httpx
from dateutil import parser as dateparser

USER_AGENT = "daily-newsroom/0.1 (+https://github.com/none)"
DEFAULT_TIMEOUT = 30.0
HTTP_NOT_MODIFIED = 304
HTTP_OK = 200

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
SITEMAP_VARIETY_THRESHOLD = 0.1
SITEMAP_OG_CONCURRENCY = 5

logger = logging.getLogger(__name__)


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
    """Dispatch JSON feeds by shape: Algolia dict-with-hits vs GitHub releases list."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "hits" in data:
        return _parse_json_hn_algolia(data)
    if isinstance(data, list):
        return _parse_json_github_releases(data)
    return []


def _parse_json_hn_algolia(data: dict) -> list[ParsedItem]:
    items: list[ParsedItem] = []
    for hit in data.get("hits", []):
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


def _parse_json_github_releases(releases: list) -> list[ParsedItem]:
    items: list[ParsedItem] = []
    for rel in releases:
        url = rel.get("html_url")
        title = rel.get("name") or rel.get("tag_name")
        if not url or not title:
            continue
        items.append(
            ParsedItem(
                url=url,
                title=title,
                author=(rel.get("author") or {}).get("login"),
                published_at=_normalize_date(rel.get("published_at")),
                raw_summary=(rel.get("body") or "")[:500],
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


@dataclass
class FetchResult:
    """Summary of one source's fetch attempt."""

    source_name: str
    status: int | None
    items_inserted: int
    error: str | None = None


async def fetch_due_sources(
    state: State,
    *,
    category: str | None = None,
    source: str | None = None,
) -> list[FetchResult]:
    """Iterate all enabled sources, fetch due ones, insert items, update state.

    `category` and `source` are optional debug filters — only matching sources are
    considered. Production launchd invocations pass neither, so the full set runs.
    """
    from newsroom.state import State  # noqa: PLC0415 (avoid circular dep)

    assert isinstance(state, State)

    results: list[FetchResult] = []
    sources = state.list_enabled_sources()
    for src_row in sources:
        if category is not None and src_row["category"] != category:
            continue
        if source is not None and src_row["name"] != source:
            continue
        if not should_fetch(src_row):
            continue
        result = await _fetch_and_ingest_one(src_row, state)
        results.append(result)
    return results


async def _fetch_and_ingest_one(src_row: Any, state: State) -> FetchResult:
    outcome = await fetch_one_raw(src_row)
    name = src_row["name"]
    now = datetime.now(UTC)

    if outcome.error is not None:
        state.increment_source_error(name, outcome.error)
        return FetchResult(source_name=name, status=None, items_inserted=0, error=outcome.error)

    if outcome.status == HTTP_NOT_MODIFIED:
        state.update_source_fetch_state(name=name, last_checked_at=now)
        state.reset_source_errors(name)
        return FetchResult(source_name=name, status=HTTP_NOT_MODIFIED, items_inserted=0)

    if outcome.status != HTTP_OK:
        err = f"HTTP {outcome.status}"
        state.increment_source_error(name, err)
        return FetchResult(source_name=name, status=outcome.status, items_inserted=0, error=err)

    # 200 OK: parse + insert
    assert outcome.body is not None
    feed_type = src_row["feed_type"]
    if feed_type == "sitemap-scrape":
        url_filter = src_row["url_filter"] or r".*"
        parsed = await parse_sitemap_scrape(outcome.body, url_filter=url_filter, now=now)
    else:
        parsed = parse_feed(outcome.body, feed_type=feed_type)
    inserted = 0
    for item in parsed:
        if state.insert_item(
            source_id=src_row["id"],
            item_hash=item.item_hash,
            url=item.url,
            title=item.title,
            author=item.author,
            published_at=item.published_at,
            raw_summary=item.raw_summary,
            category=src_row["category"],
        ):
            inserted += 1

    state.update_source_fetch_state(
        name=name,
        last_checked_at=now,
        last_fetched_at=now,
        etag=outcome.etag,
        last_modified=outcome.last_modified,
    )
    state.reset_source_errors(name)
    return FetchResult(source_name=name, status=HTTP_OK, items_inserted=inserted)


# ── sitemap-scrape ───────────────────────────────────────────────────

_OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]*)"', re.IGNORECASE)
_OG_DESC_RE = re.compile(r'<meta\s+property="og:description"\s+content="([^"]*)"', re.IGNORECASE)
_OG_PUBLISHED_RE = re.compile(
    r'<meta\s+property="article:published_time"\s+content="([^"]*)"', re.IGNORECASE
)


def _deslugify(slug: str) -> str:
    """Turn `first-article` into `First Article` for a last-ditch title."""
    return " ".join(word.capitalize() for word in slug.split("-") if word)


def _parse_sitemap_entries(raw: bytes) -> list[tuple[str, str | None]]:
    """Return [(loc, lastmod), ...] from sitemap XML. Empty list on parse error."""
    try:
        root = ET.fromstring(raw)  # noqa: S314 — sitemap XML, not untrusted payload
    except ET.ParseError:
        return []
    entries: list[tuple[str, str | None]] = []
    for url_elem in root.findall("sm:url", SITEMAP_NS):
        loc = url_elem.findtext("sm:loc", namespaces=SITEMAP_NS)
        if not loc:
            continue
        lastmod = url_elem.findtext("sm:lastmod", namespaces=SITEMAP_NS)
        entries.append((loc, lastmod))
    return entries


def _lastmods_are_trustworthy(entries: list[tuple[str, str | None]]) -> bool:
    """Heuristic: reject stale sitemaps where all lastmods are the same site-build date.

    Requires: (a) at least 2 distinct values and (b) ≥10% distinct-to-total ratio.
    Large sitemaps with single-value lastmods (e.g. deeplearning.ai's 798 entries all
    stamped with the daily build time) fail condition (a) regardless of size.
    """
    lastmods = [lm for _, lm in entries if lm]
    if not lastmods:
        return False
    unique = len(set(lastmods))
    if unique < 2:  # noqa: PLR2004
        return False
    return unique / len(lastmods) >= SITEMAP_VARIETY_THRESHOLD


async def _fetch_og_metadata(url: str, client: httpx.AsyncClient) -> dict[str, str | None] | None:
    """Fetch URL and extract og:title / og:description / article:published_time.

    Returns None on non-200 response or network error — caller should skip the item.
    """
    try:
        response = await client.get(url, timeout=15.0, follow_redirects=True)
    except (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
    ) as e:
        logger.debug("og fetch failed for %s: %s", url, e)
        return None
    if response.status_code != HTTP_OK:
        logger.debug("og fetch non-200 for %s: %s", url, response.status_code)
        return None
    html = response.text
    return {
        "title": _first_group(_OG_TITLE_RE, html),
        "description": _first_group(_OG_DESC_RE, html),
        "published_time": _first_group(_OG_PUBLISHED_RE, html),
    }


def _first_group(regex: re.Pattern[str], text: str) -> str | None:
    m = regex.search(text)
    return m.group(1) if m else None


async def parse_sitemap_scrape(
    raw: bytes,
    *,
    url_filter: str,
    now: datetime,
    http_client: httpx.AsyncClient | None = None,
    max_concurrent: int = SITEMAP_OG_CONCURRENCY,
) -> list[ParsedItem]:
    """Parse a sitemap, filter URLs, scrape OpenGraph metadata per URL.

    `published_at` fallback chain per item: trustworthy sitemap lastmod →
    `article:published_time` from OG → `now`. Never returns None.

    Items with a non-200 OG response are skipped (see _fetch_og_metadata).
    Malformed XML yields [] (never raises).
    """
    entries = _parse_sitemap_entries(raw)
    if not entries:
        return []
    path_re = re.compile(url_filter)
    filtered = [(url, lm) for (url, lm) in entries if path_re.search(urlparse(url).path)]
    if not filtered:
        return []
    trust_lastmod = _lastmods_are_trustworthy(filtered)
    now_iso = now.isoformat()

    if http_client is None:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            og_results = await _gather_og(filtered, client, max_concurrent)
    else:
        og_results = await _gather_og(filtered, http_client, max_concurrent)

    items: list[ParsedItem] = []
    for (url, lastmod), og in zip(filtered, og_results, strict=True):
        if og is None:
            # OG fetch failed — skip item (will be retried on next fetch cycle)
            continue
        title = og.get("title") or _deslugify(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
        if not title:
            continue
        published = _choose_published_at(
            lastmod=lastmod if trust_lastmod else None,
            og_published=og.get("published_time"),
            fallback=now_iso,
        )
        items.append(
            ParsedItem(
                url=url,
                title=title,
                author=None,
                published_at=published,
                raw_summary=og.get("description"),
            )
        )
    return items


def _choose_published_at(*, lastmod: str | None, og_published: str | None, fallback: str) -> str:
    """Resolve the published_at fallback chain, normalizing to ISO-8601."""
    for candidate in (lastmod, og_published):
        if candidate:
            normalized = _normalize_date(candidate)
            if normalized:
                return normalized
    return fallback


async def _gather_og(
    entries: list[tuple[str, str | None]],
    client: httpx.AsyncClient,
    max_concurrent: int,
) -> list[dict[str, str | None] | None]:
    """Run _fetch_og_metadata concurrently with a semaphore."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _one(url: str) -> dict[str, str | None] | None:
        async with sem:
            return await _fetch_og_metadata(url, client)

    return await asyncio.gather(*(_one(url) for url, _ in entries))
