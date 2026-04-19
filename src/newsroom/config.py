"""Config loader: YAML → Pydantic-validated Source objects."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

FeedType = Literal["rss", "atom", "json", "html-scrape", "sitemap-scrape"]


class ConfigError(Exception):
    """Raised for any malformed or missing config."""


class Source(BaseModel):
    """One feed source from sources.yaml."""

    name: str = Field(min_length=1)
    category: str = Field(min_length=1)
    subcategory: str | None = None
    url: str = Field(pattern=r"^https?://")
    feed_type: FeedType
    interval_seconds: int = Field(gt=0)
    enabled: bool = True
    # Optional URL-path regex for sitemap-scrape (e.g. r"^/news/[^/]+$").
    # Other feed_types ignore it.
    url_filter: str | None = None

    @field_validator("name")
    @classmethod
    def name_is_slug(cls, v: str) -> str:
        # Name wird als Filename/Label genutzt → nur URL-sichere Zeichen
        if not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(f"name must be alphanumeric + dash/underscore only: {v!r}")
        return v


class _SourcesFile(BaseModel):
    sources: list[Source]


def load_sources(path: Path) -> list[Source]:
    """Parse and validate sources.yaml. Raises ConfigError on any problem."""
    if not path.exists():
        raise ConfigError(f"sources config not found: {path}")

    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ConfigError(f"sources.yaml is not valid YAML: {e}") from e

    if not isinstance(data, dict) or "sources" not in data:
        raise ConfigError("sources.yaml must have a top-level 'sources:' list")

    try:
        parsed = _SourcesFile(**data)
    except ValidationError as e:
        raise ConfigError(f"sources.yaml validation failed: {e}") from e

    names = [s.name for s in parsed.sources]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"duplicate source names: {sorted(dupes)}")

    return parsed.sources
