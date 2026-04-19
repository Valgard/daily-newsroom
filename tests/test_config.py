from pathlib import Path

import pytest

from newsroom.config import ConfigError, load_sources


def test_load_sources_parses_valid_yaml(fixtures_dir: Path) -> None:
    sources = load_sources(fixtures_dir / "sources.fixture.yaml")
    assert len(sources) == 3  # noqa: PLR2004
    arxiv = next(s for s in sources if s.name == "arxiv-cs-cl")
    assert arxiv.category == "ai"
    assert arxiv.subcategory == "arxiv"
    assert arxiv.feed_type == "rss"
    assert arxiv.interval_seconds == 86400  # noqa: PLR2004
    assert arxiv.enabled is True


def test_load_sources_parses_disabled_source(fixtures_dir: Path) -> None:
    sources = load_sources(fixtures_dir / "sources.fixture.yaml")
    disabled = next(s for s in sources if s.name == "disabled-source")
    assert disabled.enabled is False


def test_load_sources_rejects_invalid_feed_type(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "sources:\n"
        "  - name: x\n"
        "    category: ai\n"
        "    url: https://e.com\n"
        "    feed_type: gopher\n"
        "    interval_seconds: 60\n"
    )
    with pytest.raises(ConfigError):
        load_sources(bad)


def test_load_sources_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_sources(tmp_path / "nope.yaml")


def test_load_sources_rejects_duplicate_names(tmp_path: Path) -> None:
    dupe = tmp_path / "dupe.yaml"
    dupe.write_text(
        "sources:\n"
        "  - {name: x, category: ai, url: 'https://a.com', feed_type: rss, interval_seconds: 60}\n"
        "  - {name: x, category: ai, url: 'https://b.com', feed_type: rss, interval_seconds: 60}\n"
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_sources(dupe)
