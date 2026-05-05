"""Tests for the sitemap-scrape feed type: parser, variety heuristic, OG fallback chain."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from newsroom import fetcher as fetcher_module
from newsroom.fetcher import ParsedItem, parse_sitemap_scrape

NOW = datetime(2026, 4, 19, 12, 0, tzinfo=UTC)


def _og_html(
    title: str | None = None, desc: str | None = None, published: str | None = None
) -> bytes:
    """Build minimal HTML with requested og/article meta tags."""
    parts = ["<html><head>"]
    if title is not None:
        parts.append(f'<meta property="og:title" content="{title}">')
    if desc is not None:
        parts.append(f'<meta property="og:description" content="{desc}">')
    if published is not None:
        parts.append(f'<meta property="article:published_time" content="{published}">')
    parts.append("</head><body></body></html>")
    return "".join(parts).encode()


# ── variety heuristic + URL filter ───────────────────────────────────


async def test_parse_sitemap_scrape_filters_urls_by_regex(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """Only URLs matching url_filter are scraped; /about and /careers are dropped."""
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}",
            content=_og_html(title=f"Title {slug}", desc="Body"),
        )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/[^/]+$", now=NOW)
    assert len(items) == 3
    titles = {i.title for i in items}
    assert titles == {
        "Title first-article",
        "Title second-article",
        "Title third-article",
    }


async def test_parse_sitemap_scrape_varied_lastmod_wins(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """Varied sitemap lastmods (Anthropic-style) are trusted as published_at."""
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}", content=_og_html(title=slug)
        )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    by_title = {i.title: i for i in items}
    assert by_title["first-article"].published_at == "2026-04-14T10:00:00+00:00"
    assert by_title["second-article"].published_at == "2026-04-16T11:00:00+00:00"
    assert by_title["third-article"].published_at == "2026-04-18T09:00:00+00:00"


async def test_parse_sitemap_scrape_stale_lastmod_falls_back_to_og(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """The-Batch-style: all lastmods identical → ignored; og article:published_time used."""
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-alpha",
        content=_og_html(title="Alpha", published="2025-01-22T07:18:00-08:00"),
    )
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-beta",
        content=_og_html(title="Beta", published="2025-02-15T10:00:00Z"),
    )
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-gamma",
        content=_og_html(title="Gamma", published="2025-03-01T12:30:00Z"),
    )
    raw = (fixtures_dir / "sitemap_stale.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/the-batch/", now=NOW)
    by_title = {i.title: i for i in items}
    # NOT the sitemap's 2026-04-17 build date
    assert by_title["Alpha"].published_at.startswith("2025-01-22")
    assert by_title["Beta"].published_at == "2025-02-15T10:00:00+00:00"
    assert by_title["Gamma"].published_at == "2025-03-01T12:30:00+00:00"


async def test_parse_sitemap_scrape_scrape_time_as_last_fallback(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """Stale sitemap + no og:article:published_time → published_at = now."""
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-alpha",
        content=_og_html(title="A"),
    )
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-beta",
        content=_og_html(title="B"),
    )
    httpx_mock.add_response(
        url="https://example.com/the-batch/article-gamma",
        content=_og_html(title="G"),
    )
    raw = (fixtures_dir / "sitemap_stale.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/the-batch/", now=NOW)
    expected = NOW.isoformat()
    assert all(i.published_at == expected for i in items)


# ── item content + edge cases ─────────────────────────────────────────


async def test_parse_sitemap_scrape_extracts_description(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """og:description flows into raw_summary."""
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}",
            content=_og_html(title=slug, desc=f"Summary of {slug}"),
        )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    by_title = {i.title: i for i in items}
    assert by_title["first-article"].raw_summary == "Summary of first-article"


async def test_parse_sitemap_scrape_missing_og_title_falls_back_to_slug(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """If og:title is absent, derive a title from the URL slug."""
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}",
            content=_og_html(title=None),
        )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    titles = {i.title for i in items}
    # Slug deslugification: "first-article" → "First Article"
    assert "First Article" in titles
    assert "Second Article" in titles
    assert "Third Article" in titles


async def test_parse_sitemap_scrape_og_fetch_failure_skips_item(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """If OG fetch returns 404 the item is skipped (not added with empty fields)."""
    httpx_mock.add_response(url="https://example.com/news/first-article", status_code=404)
    httpx_mock.add_response(
        url="https://example.com/news/second-article",
        content=_og_html(title="Second"),
    )
    httpx_mock.add_response(
        url="https://example.com/news/third-article",
        content=_og_html(title="Third"),
    )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    titles = {i.title for i in items}
    assert titles == {"Second", "Third"}


@pytest.mark.parametrize(
    "exc_cls",
    [
        httpx.ReadError,
        httpx.WriteError,
        httpx.PoolTimeout,
    ],
)
async def test_parse_sitemap_scrape_og_transport_error_skips_item(
    httpx_mock: HTTPXMock,
    fixtures_dir: Path,
    exc_cls: type[Exception],
) -> None:
    """Transport-level httpx errors (ReadError, WriteError, PoolTimeout) on one OG fetch
    must not crash the whole sitemap run. The failing item is skipped; the rest pass through.

    Regression: before the fix, only ConnectError/ConnectTimeout/ReadTimeout/RemoteProtocolError
    were caught — ReadError (and friends) escaped, asyncio.gather raised, the entire fetch died.
    """
    httpx_mock.add_exception(
        exc_cls("transport-level boom"),
        url="https://example.com/news/first-article",
    )
    httpx_mock.add_response(
        url="https://example.com/news/second-article", content=_og_html(title="Second")
    )
    httpx_mock.add_response(
        url="https://example.com/news/third-article", content=_og_html(title="Third")
    )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    assert {i.title for i in items} == {"Second", "Third"}


async def test_gather_og_returns_none_for_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: even if _fetch_og_metadata raises an exception type the
    inner try/except did not anticipate (future httpx subclass, library bug, etc.),
    _gather_og must NOT propagate — it returns None for that slot and lets the
    others succeed. asyncio.gather without return_exceptions=True would tear down
    the entire batch, killing the whole fetch run.
    """
    call_count = 0

    async def flaky(url: str, client: object) -> dict[str, str | None] | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated regression: leaked exception")
        return {"title": "ok", "description": None, "published_time": None}

    monkeypatch.setattr(fetcher_module, "_fetch_og_metadata", flaky)
    entries = [("https://a.example/", None), ("https://b.example/", None)]
    results = await fetcher_module._gather_og(entries, client=None, max_concurrent=2)  # type: ignore[arg-type]
    assert results[0] is None
    assert results[1] is not None
    assert results[1]["title"] == "ok"


async def test_parse_sitemap_scrape_malformed_xml_returns_empty() -> None:
    """Bad XML must not raise; return [] per parse_feed contract."""
    items = await parse_sitemap_scrape(b"<not-xml", url_filter=r".*", now=NOW)
    assert items == []


async def test_parse_sitemap_scrape_returns_parsed_items(
    httpx_mock: HTTPXMock, fixtures_dir: Path
) -> None:
    """Return type is list[ParsedItem] with item_hash populated."""
    for slug in ("first-article", "second-article", "third-article"):
        httpx_mock.add_response(
            url=f"https://example.com/news/{slug}",
            content=_og_html(title=slug),
        )
    raw = (fixtures_dir / "sitemap_varied.xml").read_bytes()
    items = await parse_sitemap_scrape(raw, url_filter=r"^/news/", now=NOW)
    assert all(isinstance(i, ParsedItem) for i in items)
    hashes = {i.item_hash for i in items}
    assert len(hashes) == 3  # distinct items → distinct hashes
