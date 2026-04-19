# Daily Newsroom — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, macOS-native news-digest agent that fetches AI/LLM/ML sources every 5 minutes via launchd, scores items for importance via Claude Haiku, pushes macOS notifications for high-importance items, and generates morning + evening markdown digests in `~/Documents/!AI/news/` via Claude Opus.

**Architecture:** "Dumb trigger, smart script." launchd provides three triggers (fetch every 5 min, digest 07:00, digest 20:00); a Python CLI (`newsroom`) holds all logic, uses SQLite for state, and converges state rather than processing events — missed triggers self-heal. LLM calls go through the Claude Agent SDK, which spawns the `claude` CLI subprocess and uses the user's Max 20x subscription via macOS Keychain OAuth.

**Tech Stack:** Python 3.12 (asdf/uv), `claude-agent-sdk`, `feedparser`, `httpx`, `typer`, `rich`, `pynс`, `pydantic`, `python-dateutil`, stdlib `sqlite3`. Ruff for format/lint, pytest with `freezegun` and `pytest-httpx` for tests. launchd plists for scheduling.

**Spec reference:** `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`

---

## Pre-flight: Working Directory

All paths in this plan are **relative to the project root**: `/Users/valgard/Projects/private/daily-newsroom/`. Git is already initialized with branch `main`; the design spec is committed at `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`.

All commands assume the working directory is the project root unless otherwise noted.

---

## Task Order

Tasks are ordered by dependency. Each produces a committable, tested unit.

1. Project scaffolding (uv init, dependencies, package skeleton)
2. Config module (Pydantic models, YAML loader)
3. State module — schema, migrations, source CRUD
4. State module — items & digests CRUD
5. Agent-client wrapper (claude-agent-sdk + prompt loading + retry)
6. Fetcher — `should_fetch` + conditional HTTP
7. Fetcher — feed parsers (RSS/Atom/JSON)
8. Fetcher — orchestration (fetch-due-sources loop)
9. Filter-arxiv module
10. Scorer module
11. Notifier module (threshold + pync)
12. Digester — collection + cross-link + Opus + write
13. Digester — idempotency + fallback raw digest
14. CLI — subcommand wiring (typer) + dry-run flags
15. CLI — `status` command with rich tables
16. Logging — RichHandler + JSONL events
17. Prompts + source config
18. launchd plists + shell wrappers + install scripts
19. Integration tests + README + CLAUDE.md

---

## Task 1: Project Scaffolding

**Goal:** Bootstrap a uv-managed Python package with dependency structure and dev tooling. After this task, `pytest` runs (finding zero tests) and `ruff check` runs clean.

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `src/newsroom/__init__.py`
- Create: `src/newsroom/__main__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [x] **Step 1: Initialize uv project with Python 3.12**

```bash
uv init --package --python 3.12 --name newsroom --lib
```

This creates `pyproject.toml`, `src/newsroom/__init__.py`, and `.python-version`.

- [x] **Step 2: Set asdf Python version**

```bash
echo "python 3.12.7" > .tool-versions
```

- [x] **Step 3: Add runtime dependencies**

```bash
uv add claude-agent-sdk feedparser httpx pync pydantic typer rich python-dateutil tenacity anyio
```

- [x] **Step 4: Add dev dependencies**

```bash
uv add --dev ruff vulture pytest pytest-asyncio pytest-mock pytest-httpx freezegun
```

- [x] **Step 5: Write `pyproject.toml` tool configuration**

Append to `pyproject.toml`:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "PL"]
ignore = ["PLR0913"]  # too-many-arguments

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "real_llm: tests that hit the real Anthropic API (skipped by default)",
]
addopts = "-m 'not real_llm'"

[project.scripts]
newsroom = "newsroom.cli:app"
```

- [x] **Step 6: Write `src/newsroom/__main__.py`**

```python
"""Entry point for `python -m newsroom`."""
from newsroom.cli import app

if __name__ == "__main__":
    app()
```

- [x] **Step 7: Write placeholder `src/newsroom/cli.py`**

```python
"""CLI entry point. Real subcommands added in Task 14."""
import typer

app = typer.Typer(help="Daily Newsroom — local news-digest agent")


@app.command()
def version() -> None:
    """Print version."""
    from newsroom import __version__
    print(f"newsroom {__version__}")
```

- [x] **Step 8: Set version in `src/newsroom/__init__.py`**

```python
"""Daily Newsroom — local news-digest agent."""
__version__ = "0.1.0"
```

- [x] **Step 9: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures."""
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES
```

- [x] **Step 10: Create empty `tests/__init__.py` and `tests/fixtures/` directory**

```bash
touch tests/__init__.py
mkdir -p tests/fixtures
touch tests/fixtures/.gitkeep
```

- [x] **Step 11: Verify tooling runs**

```bash
uv run pytest
uv run ruff check
uv run python -m newsroom version
```

Expected:
- `pytest` → "no tests ran", exit 0
- `ruff check` → "All checks passed!"
- `python -m newsroom version` → `newsroom 0.1.0`

- [x] **Step 12: Commit**

```bash
git add .
git commit -m "chore: project scaffolding (uv, deps, package skeleton)"
```

---

## Task 2: Config Module

**Goal:** Load and validate `sources.yaml` into typed Pydantic models. Fails loudly on malformed config.

**Files:**
- Create: `src/newsroom/config.py`
- Create: `tests/test_config.py`
- Create: `tests/fixtures/sources.fixture.yaml`

- [x] **Step 1: Write test fixture**

Create `tests/fixtures/sources.fixture.yaml`:

```yaml
sources:
  - name: arxiv-cs-cl
    category: ai
    subcategory: arxiv
    url: https://arxiv.org/rss/cs.CL
    feed_type: rss
    interval_seconds: 86400
    enabled: true
  - name: simon-willison
    category: ai
    subcategory: curated
    url: https://simonwillison.net/atom/everything/
    feed_type: atom
    interval_seconds: 3600
    enabled: true
  - name: disabled-source
    category: ai
    subcategory: lab
    url: https://example.com/rss
    feed_type: rss
    interval_seconds: 3600
    enabled: false
```

- [x] **Step 2: Write the failing test**

Create `tests/test_config.py`:

```python
from pathlib import Path
import pytest
from newsroom.config import Source, load_sources, ConfigError


def test_load_sources_parses_valid_yaml(fixtures_dir: Path) -> None:
    sources = load_sources(fixtures_dir / "sources.fixture.yaml")
    assert len(sources) == 3
    arxiv = next(s for s in sources if s.name == "arxiv-cs-cl")
    assert arxiv.category == "ai"
    assert arxiv.subcategory == "arxiv"
    assert arxiv.feed_type == "rss"
    assert arxiv.interval_seconds == 86400
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
```

- [x] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.config'`

- [x] **Step 4: Implement `src/newsroom/config.py`**

```python
"""Config loader: YAML → Pydantic-validated Source objects."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


FeedType = Literal["rss", "atom", "json", "html-scrape"]


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
```

- [x] **Step 5: Add `pyyaml` dependency**

```bash
uv add pyyaml
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: all 5 tests PASS.

- [x] **Step 7: Run linter**

```bash
uv run ruff check src/newsroom/config.py tests/test_config.py
uv run ruff format src/newsroom/config.py tests/test_config.py
```

- [x] **Step 8: Commit**

```bash
git add src/newsroom/config.py tests/test_config.py tests/fixtures/sources.fixture.yaml pyproject.toml uv.lock
git commit -m "feat(config): pydantic source config loader"
```

---

## Task 3: State Module — Schema, Migrations, Source CRUD

**Goal:** SQLite state manager with schema migrations and Source-table read/write. After this task, `newsroom init` could be built on top (deferred to Task 14).

**Files:**
- Create: `src/newsroom/state.py`
- Create: `tests/test_state.py`

- [x] **Step 1: Write the failing tests**

Create `tests/test_state.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from newsroom.config import Source
from newsroom.state import State


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "test.db")
    s.ensure_schema()
    return s


def _sample_source() -> Source:
    return Source(
        name="arxiv-cs-cl",
        category="ai",
        subcategory="arxiv",
        url="https://arxiv.org/rss/cs.CL",
        feed_type="rss",
        interval_seconds=86400,
        enabled=True,
    )


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    s = State(tmp_path / "t.db")
    s.ensure_schema()
    s.ensure_schema()  # no error
    version = s.connection().execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version >= 1


def test_upsert_source_inserts_new(state: State) -> None:
    src = _sample_source()
    state.upsert_source(src)
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row is not None
    assert row["url"] == src.url
    assert row["interval_seconds"] == 86400
    assert row["enabled"] == 1


def test_upsert_source_updates_existing(state: State) -> None:
    src = _sample_source()
    state.upsert_source(src)
    # Change URL + interval
    src2 = src.model_copy(update={"url": "https://arxiv.org/rss/cs.LG", "interval_seconds": 3600})
    state.upsert_source(src2)
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["url"] == "https://arxiv.org/rss/cs.LG"
    assert row["interval_seconds"] == 3600


def test_list_enabled_sources_excludes_disabled(state: State) -> None:
    state.upsert_source(_sample_source())
    state.upsert_source(_sample_source().model_copy(update={"name": "disabled", "enabled": False}))
    enabled = state.list_enabled_sources()
    assert len(enabled) == 1
    assert enabled[0]["name"] == "arxiv-cs-cl"


def test_update_source_fetch_state(state: State) -> None:
    state.upsert_source(_sample_source())
    now = datetime(2026, 4, 19, 10, 30, tzinfo=timezone.utc)
    state.update_source_fetch_state(
        name="arxiv-cs-cl",
        last_checked_at=now,
        last_fetched_at=now,
        etag='W/"abc"',
        last_modified="Sat, 19 Apr 2026 10:30:00 GMT",
    )
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["etag"] == 'W/"abc"'
    assert row["last_modified"] == "Sat, 19 Apr 2026 10:30:00 GMT"


def test_increment_source_error(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "connection timed out")
    state.increment_source_error("arxiv-cs-cl", "connection timed out")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["consecutive_errors"] == 2
    assert row["last_error"] == "connection timed out"


def test_reset_source_errors_on_success(state: State) -> None:
    state.upsert_source(_sample_source())
    state.increment_source_error("arxiv-cs-cl", "err")
    state.reset_source_errors("arxiv-cs-cl")
    row = state.get_source_by_name("arxiv-cs-cl")
    assert row["consecutive_errors"] == 0
    assert row["last_error"] is None
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_state.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.state'`

- [x] **Step 3: Implement `src/newsroom/state.py` (part 1: connection + migrations + sources)**

```python
"""SQLite state manager for newsroom."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from newsroom.config import Source


DEFAULT_DB_PATH = Path.home() / "Library" / "Application Support" / "daily-newsroom" / "state.db"


MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
        """CREATE TABLE sources (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT UNIQUE NOT NULL,
            category            TEXT NOT NULL,
            subcategory         TEXT,
            url                 TEXT NOT NULL,
            feed_type           TEXT NOT NULL,
            interval_seconds    INTEGER NOT NULL,
            enabled             INTEGER NOT NULL DEFAULT 1,
            last_fetched_at     TEXT,
            last_checked_at     TEXT,
            etag                TEXT,
            last_modified       TEXT,
            consecutive_errors  INTEGER NOT NULL DEFAULT 0,
            disabled_until      TEXT,
            last_error          TEXT,
            last_error_at       TEXT
        )""",
        """CREATE TABLE items (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id           INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            item_hash           TEXT UNIQUE NOT NULL,
            url                 TEXT NOT NULL,
            title               TEXT NOT NULL,
            author              TEXT,
            published_at        TEXT,
            raw_summary         TEXT,
            category            TEXT NOT NULL,
            status              TEXT NOT NULL DEFAULT 'new',
            importance          INTEGER,
            score_reason        TEXT,
            score_model         TEXT,
            scored_at           TEXT,
            arxiv_relevant      INTEGER,
            notified_at         TEXT,
            included_in_digest  TEXT,
            fetched_at          TEXT NOT NULL DEFAULT (datetime('now')),
            cross_link_summary  TEXT
        )""",
        """CREATE TABLE digests (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            date          TEXT NOT NULL,
            slot          TEXT NOT NULL,
            file_path     TEXT NOT NULL,
            item_count    INTEGER NOT NULL,
            model         TEXT NOT NULL,
            generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(date, slot)
        )""",
        "CREATE INDEX idx_items_status ON items(status) WHERE status IN ('new', 'filtered_in')",
        "CREATE INDEX idx_items_source ON items(source_id)",
        "CREATE INDEX idx_items_published ON items(published_at DESC)",
        """CREATE INDEX idx_items_digest_pending ON items(status, importance)
            WHERE status = 'scored' AND included_in_digest IS NULL""",
        "CREATE INDEX idx_sources_enabled ON sources(enabled, last_checked_at) WHERE enabled = 1",
    ],
}


class State:
    """SQLite wrapper. One instance per process; thread-unsafe."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Migrations ─────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        conn = self.connection()
        # bootstrap: schema_version table may not exist yet
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        for version in sorted(MIGRATIONS):
            if version > current:
                for stmt in MIGRATIONS[version]:
                    # skip re-creating schema_version from migration 1 (already exists)
                    if "CREATE TABLE schema_version" in stmt:
                        continue
                    conn.execute(stmt)
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))

    # ── Sources ────────────────────────────────────────────────────────

    def upsert_source(self, src: Source) -> None:
        """Insert or update a source. Config-driven: URL/interval/category/enabled can change."""
        conn = self.connection()
        conn.execute(
            """INSERT INTO sources
                (name, category, subcategory, url, feed_type, interval_seconds, enabled)
             VALUES (?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(name) DO UPDATE SET
                category = excluded.category,
                subcategory = excluded.subcategory,
                url = excluded.url,
                feed_type = excluded.feed_type,
                interval_seconds = excluded.interval_seconds,
                enabled = excluded.enabled""",
            (
                src.name, src.category, src.subcategory, src.url,
                src.feed_type, src.interval_seconds, int(src.enabled),
            ),
        )

    def get_source_by_name(self, name: str) -> sqlite3.Row | None:
        conn = self.connection()
        return conn.execute("SELECT * FROM sources WHERE name = ?", (name,)).fetchone()

    def list_enabled_sources(self) -> list[sqlite3.Row]:
        conn = self.connection()
        return list(conn.execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY name"
        ).fetchall())

    def update_source_fetch_state(
        self,
        *,
        name: str,
        last_checked_at: datetime,
        last_fetched_at: datetime | None = None,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                last_checked_at = ?,
                last_fetched_at = COALESCE(?, last_fetched_at),
                etag = COALESCE(?, etag),
                last_modified = COALESCE(?, last_modified)
             WHERE name = ?""",
            (
                last_checked_at.isoformat(),
                last_fetched_at.isoformat() if last_fetched_at else None,
                etag, last_modified, name,
            ),
        )

    def increment_source_error(self, name: str, error: str) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                consecutive_errors = consecutive_errors + 1,
                last_error = ?,
                last_error_at = datetime('now')
             WHERE name = ?""",
            (error, name),
        )

    def reset_source_errors(self, name: str) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE sources SET
                consecutive_errors = 0,
                last_error = NULL,
                last_error_at = NULL
             WHERE name = ?""",
            (name,),
        )

    def disable_source_until(self, name: str, until: datetime) -> None:
        conn = self.connection()
        conn.execute(
            "UPDATE sources SET disabled_until = ? WHERE name = ?",
            (until.isoformat(), name),
        )
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_state.py -v
```

Expected: all 7 tests PASS.

- [x] **Step 5: Lint & format**

```bash
uv run ruff check src/newsroom/state.py tests/test_state.py
uv run ruff format src/newsroom/state.py tests/test_state.py
```

- [x] **Step 6: Commit**

```bash
git add src/newsroom/state.py tests/test_state.py
git commit -m "feat(state): sqlite schema, migrations, source CRUD"
```

---

## Task 4: State Module — Items & Digests CRUD

**Goal:** Extend `state.py` with items and digests operations. Item dedup via `item_hash`. Idempotent digest inserts.

**Files:**
- Modify: `src/newsroom/state.py` (append methods)
- Modify: `tests/test_state.py` (append tests)

- [x] **Step 1: Append tests to `tests/test_state.py`**

```python
import hashlib

def _item_hash(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}\n{title}".encode()).hexdigest()[:16]


def _sample_item_kwargs(source_id: int, title: str = "Test title") -> dict:
    return {
        "source_id": source_id,
        "item_hash": _item_hash("https://example.com/a", title),
        "url": "https://example.com/a",
        "title": title,
        "author": "Author A",
        "published_at": "2026-04-19T10:00:00Z",
        "raw_summary": "Summary of A",
        "category": "ai",
    }


def test_insert_item_returns_inserted_flag(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    kwargs = _sample_item_kwargs(src["id"])
    first = state.insert_item(**kwargs)
    second = state.insert_item(**kwargs)  # same hash
    assert first is True
    assert second is False


def test_insert_item_different_title_different_hash(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="A"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="B"))
    rows = state.connection().execute("SELECT COUNT(*) FROM items").fetchone()
    assert rows[0] == 2


def test_list_items_by_status(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    state.insert_item(**_sample_item_kwargs(src["id"], title="T2"))
    news = state.list_items_by_status("new", limit=100)
    assert len(news) == 2


def test_mark_item_scored(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_scored(item_id=item["id"], importance=4, reason="releases new feature", model="claude-haiku-4-5")
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "scored"
    assert row["importance"] == 4
    assert row["score_reason"] == "releases new feature"


def test_mark_item_filtered_out(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_arxiv_filter(item_id=item["id"], relevant=False)
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["status"] == "filtered_out"
    assert row["arxiv_relevant"] == 0


def test_mark_item_notified(state: State) -> None:
    state.upsert_source(_sample_source())
    src = state.get_source_by_name("arxiv-cs-cl")
    state.insert_item(**_sample_item_kwargs(src["id"], title="T1"))
    item = state.list_items_by_status("new", limit=1)[0]
    state.mark_item_notified(item_id=item["id"])
    row = state.connection().execute("SELECT * FROM items WHERE id = ?", (item["id"],)).fetchone()
    assert row["notified_at"] is not None


def test_digests_insert_unique_per_slot(state: State) -> None:
    state.insert_digest(date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=20, model="opus")
    with pytest.raises(Exception):
        state.insert_digest(date="2026-04-19", slot="morning", file_path="/tmp/m2.md", item_count=21, model="opus")


def test_get_digest_returns_existing(state: State) -> None:
    state.insert_digest(date="2026-04-19", slot="morning", file_path="/tmp/m.md", item_count=20, model="opus")
    row = state.get_digest("2026-04-19", "morning")
    assert row is not None
    assert row["item_count"] == 20


def test_get_digest_returns_none_for_missing(state: State) -> None:
    assert state.get_digest("2026-04-19", "morning") is None
```

- [x] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_state.py -v
```

Expected: new tests fail (`AttributeError: 'State' object has no attribute 'insert_item'`).

- [x] **Step 3: Append items & digests methods to `src/newsroom/state.py`**

```python
    # ── Items ──────────────────────────────────────────────────────────

    def insert_item(
        self,
        *,
        source_id: int,
        item_hash: str,
        url: str,
        title: str,
        author: str | None,
        published_at: str | None,
        raw_summary: str | None,
        category: str,
    ) -> bool:
        """Insert item. Returns True if inserted, False if duplicate hash."""
        conn = self.connection()
        cur = conn.execute(
            """INSERT INTO items
                (source_id, item_hash, url, title, author, published_at, raw_summary, category)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(item_hash) DO NOTHING""",
            (source_id, item_hash, url, title, author, published_at, raw_summary, category),
        )
        return cur.rowcount > 0

    def list_items_by_status(self, status: str, limit: int = 100) -> list[sqlite3.Row]:
        conn = self.connection()
        return list(conn.execute(
            "SELECT items.*, sources.name AS source_name, sources.subcategory AS source_subcategory "
            "FROM items JOIN sources ON items.source_id = sources.id "
            "WHERE items.status = ? ORDER BY items.fetched_at ASC LIMIT ?",
            (status, limit),
        ).fetchall())

    def list_items_for_digest(self, since_iso: str) -> list[sqlite3.Row]:
        conn = self.connection()
        return list(conn.execute(
            "SELECT items.*, sources.name AS source_name, sources.subcategory AS source_subcategory "
            "FROM items JOIN sources ON items.source_id = sources.id "
            "WHERE items.status = 'scored' AND items.included_in_digest IS NULL "
            "  AND items.scored_at >= ? "
            "ORDER BY items.importance DESC, items.published_at DESC",
            (since_iso,),
        ).fetchall())

    def mark_item_scored(
        self, *, item_id: int, importance: int, reason: str, model: str,
    ) -> None:
        conn = self.connection()
        conn.execute(
            """UPDATE items SET
                status = 'scored', importance = ?, score_reason = ?,
                score_model = ?, scored_at = datetime('now')
             WHERE id = ?""",
            (importance, reason, model, item_id),
        )

    def mark_item_arxiv_filter(self, *, item_id: int, relevant: bool) -> None:
        new_status = "filtered_in" if relevant else "filtered_out"
        conn = self.connection()
        conn.execute(
            "UPDATE items SET status = ?, arxiv_relevant = ? WHERE id = ?",
            (new_status, int(relevant), item_id),
        )

    def mark_item_notified(self, *, item_id: int) -> None:
        conn = self.connection()
        conn.execute(
            "UPDATE items SET notified_at = datetime('now') WHERE id = ?",
            (item_id,),
        )

    def mark_item_digested(self, *, item_id: int, digest_label: str) -> None:
        conn = self.connection()
        conn.execute(
            "UPDATE items SET included_in_digest = ? WHERE id = ?",
            (digest_label, item_id),
        )

    def count_recent_notifications_by_category(
        self, category: str, within_minutes: int,
    ) -> int:
        conn = self.connection()
        return conn.execute(
            "SELECT COUNT(*) FROM items "
            "WHERE category = ? AND notified_at IS NOT NULL "
            "  AND notified_at >= datetime('now', ?)",
            (category, f"-{within_minutes} minutes"),
        ).fetchone()[0]

    # ── Digests ────────────────────────────────────────────────────────

    def insert_digest(
        self, *, date: str, slot: str, file_path: str, item_count: int, model: str,
    ) -> None:
        conn = self.connection()
        conn.execute(
            "INSERT INTO digests (date, slot, file_path, item_count, model) "
            "VALUES (?, ?, ?, ?, ?)",
            (date, slot, file_path, item_count, model),
        )

    def get_digest(self, date: str, slot: str) -> sqlite3.Row | None:
        conn = self.connection()
        return conn.execute(
            "SELECT * FROM digests WHERE date = ? AND slot = ?",
            (date, slot),
        ).fetchone()

    def get_last_digest_generated_at(self, slot: str) -> str | None:
        """Return ISO timestamp of most recent generation of given slot, or None."""
        conn = self.connection()
        row = conn.execute(
            "SELECT MAX(generated_at) FROM digests WHERE slot = ?",
            (slot,),
        ).fetchone()
        return row[0] if row else None
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_state.py -v
```

Expected: all tests PASS (now 16 total).

- [x] **Step 5: Commit**

```bash
git add src/newsroom/state.py tests/test_state.py
git commit -m "feat(state): items and digests CRUD"
```

---

## Task 5: Agent-Client Wrapper

**Goal:** Single entry point for LLM calls: loads prompt from `config/prompts/<name>.md`, templates variables in, calls claude-agent-sdk, parses result (JSON or raw text), retries on rate-limit.

**Files:**
- Create: `src/newsroom/agent_client.py`
- Create: `config/prompts/.gitkeep`
- Create: `tests/test_agent_client.py`

- [x] **Step 1: Create prompts directory and placeholder**

```bash
mkdir -p config/prompts
touch config/prompts/.gitkeep
```

- [x] **Step 2: Write a fixture prompt for testing**

Create `tests/fixtures/prompt_test_echo.md`:

```markdown
Respond with a JSON object: `{"echoed": "<value>"}` where value is `{{ value }}`.
```

- [x] **Step 3: Write failing tests**

Create `tests/test_agent_client.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from newsroom.agent_client import AgentClient, ParseError, render_prompt


def test_render_prompt_substitutes_variables(fixtures_dir: Path) -> None:
    result = render_prompt(fixtures_dir / "prompt_test_echo.md", {"value": "hello"})
    assert "hello" in result
    assert "{{" not in result


def test_render_prompt_fails_on_missing_variable(fixtures_dir: Path) -> None:
    with pytest.raises(KeyError):
        render_prompt(fixtures_dir / "prompt_test_echo.md", {})


async def test_ask_parses_json(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream('{"echoed": "hi"}')
        result = await client.ask(
            prompt_name="prompt_test_echo", variables={"value": "hi"},
            model="claude-haiku-4-5", parse="json",
        )
    assert result == {"echoed": "hi"}


async def test_ask_raises_on_invalid_json(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream("not-json-at-all")
        with pytest.raises(ParseError):
            await client.ask(
                prompt_name="prompt_test_echo", variables={"value": "x"},
                model="claude-haiku-4-5", parse="json",
            )


async def test_ask_returns_raw_text_when_parse_none(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream("# Markdown\n\nBody.")
        result = await client.ask(
            prompt_name="prompt_test_echo", variables={"value": "x"},
            model="claude-opus-4-7", parse="text",
        )
    assert result == "# Markdown\n\nBody."


async def _mock_stream(text: str):
    """Mock an async generator that yields a ResultMessage-shaped dict."""
    class _Msg:
        def __init__(self, result: str) -> None:
            self.result = result
    yield _Msg(text)
```

- [x] **Step 4: Run tests to verify failure**

```bash
uv run pytest tests/test_agent_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.agent_client'`

- [x] **Step 5: Implement `src/newsroom/agent_client.py`**

```python
"""Thin wrapper around claude-agent-sdk with prompt loading and JSON parsing."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt, wait_exponential,
)


ParseMode = Literal["json", "text"]
DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


class ParseError(Exception):
    """LLM response could not be parsed as expected."""


class AgentError(Exception):
    """LLM call failed after retries."""


def render_prompt(path: Path, variables: dict[str, Any]) -> str:
    """Load a prompt file and substitute {{ variable }} tokens.

    Uses a simple Jinja-like syntax but without full Jinja to avoid the dependency.
    Missing variable → KeyError.
    """
    template = path.read_text()

    def sub(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in variables:
            raise KeyError(f"prompt {path.name} references {{{{ {key} }}}} but variable not provided")
        return str(variables[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM response text.

    Handles: raw JSON, JSON wrapped in ```json fences, JSON with leading/trailing prose.
    """
    text = text.strip()
    # Fenced JSON block
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        # Greedy match: first { … last } across the whole text
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not obj_match:
            raise ParseError(f"no JSON object found in response: {text[:200]!r}")
        candidate = obj_match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON in response: {e}; candidate={candidate[:200]!r}") from e


class AgentClient:
    """Call Claude via claude-agent-sdk with prompt templating."""

    def __init__(self, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> None:
        self.prompts_dir = prompts_dir

    @retry(
        retry=retry_if_exception_type(AgentError),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def ask(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        model: str,
        parse: ParseMode = "json",
    ) -> Any:
        """Run a prompt and return parsed result."""
        prompt_path = self.prompts_dir / f"{prompt_name}.md"
        prompt_text = render_prompt(prompt_path, variables)

        options = ClaudeAgentOptions(
            allowed_tools=[],  # no tools needed for scoring/filtering/summarization
            model=model,
        )
        last_result: str | None = None
        try:
            async for msg in query(prompt=prompt_text, options=options):
                if isinstance(msg, ResultMessage):
                    last_result = msg.result
        except Exception as e:  # SDK errors are broad
            raise AgentError(f"agent call failed: {e}") from e

        if last_result is None:
            raise AgentError("agent returned no ResultMessage")

        if parse == "json":
            return _extract_json(last_result)
        elif parse == "text":
            return last_result
        else:
            raise ValueError(f"unknown parse mode: {parse}")
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_agent_client.py -v
```

Expected: 5 tests PASS.

- [x] **Step 7: Commit**

```bash
git add src/newsroom/agent_client.py tests/test_agent_client.py tests/fixtures/prompt_test_echo.md config/prompts/.gitkeep
git commit -m "feat(agent): claude-agent-sdk wrapper with prompt templating"
```

---

## Task 6: Fetcher — `should_fetch` + Conditional HTTP

**Goal:** Build the scheduling-decision helper and HTTP fetcher with ETag/If-Modified-Since.

**Files:**
- Create: `src/newsroom/fetcher.py`
- Create: `tests/test_fetcher.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_fetcher.py`:

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time
from pytest_httpx import HTTPXMock

from newsroom.fetcher import FetchOutcome, fetch_one_raw, should_fetch


def _source_row(**overrides) -> dict:
    base = dict(
        name="s", category="ai", subcategory=None,
        url="https://e.com/rss", feed_type="rss", interval_seconds=3600,
        enabled=1, last_fetched_at=None, last_checked_at=None,
        etag=None, last_modified=None, consecutive_errors=0,
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
    future = (datetime(2026, 4, 19, 11, 0, tzinfo=timezone.utc)).isoformat()
    assert should_fetch(_source_row(disabled_until=future)) is False


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_respects_interval_not_due() -> None:
    checked = (datetime(2026, 4, 19, 9, 30, tzinfo=timezone.utc)).isoformat()
    assert should_fetch(_source_row(last_checked_at=checked, interval_seconds=3600)) is False


@freeze_time("2026-04-19 10:00:00")
def test_should_fetch_respects_interval_due() -> None:
    checked = (datetime(2026, 4, 19, 8, 59, tzinfo=timezone.utc)).isoformat()
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
```

- [x] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_fetcher.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.fetcher'`

- [x] **Step 3: Implement `src/newsroom/fetcher.py` (part 1: should_fetch + fetch_one_raw)**

```python
"""RSS/HTTP fetcher: conditional GET, feed parsing, item extraction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


USER_AGENT = "daily-newsroom/0.1 (+https://github.com/none)"
DEFAULT_TIMEOUT = 30.0


@dataclass
class FetchOutcome:
    """Result of one HTTP fetch attempt."""
    status: int | None             # None = network error
    body: bytes | None             # None on 304 or error
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None       # human-readable for transient errors


def should_fetch(source_row: dict[str, Any] | Any) -> bool:
    """Decide whether a source is due for fetching, given its DB row."""
    # sqlite3.Row supports dict-like access
    enabled = source_row["enabled"] if "enabled" in _keys(source_row) else 1
    if not enabled:
        return False

    disabled_until = source_row["disabled_until"] if "disabled_until" in _keys(source_row) else None
    now = datetime.now(timezone.utc)
    if disabled_until:
        if datetime.fromisoformat(disabled_until) > now:
            return False

    last_checked = source_row["last_checked_at"] if "last_checked_at" in _keys(source_row) else None
    if last_checked is None:
        return True

    elapsed = (now - datetime.fromisoformat(last_checked)).total_seconds()
    return elapsed >= source_row["interval_seconds"]


def _keys(row: Any) -> list[str]:
    """Get column names from sqlite3.Row or dict."""
    if hasattr(row, "keys"):
        return list(row.keys())
    return list(row)


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
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
        return FetchOutcome(status=None, body=None, error=f"network error: {e}")

    if response.status_code == 304:
        return FetchOutcome(status=304, body=None)

    return FetchOutcome(
        status=response.status_code,
        body=response.content if response.status_code == 200 else None,
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
    )
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_fetcher.py -v
```

Expected: all 9 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/newsroom/fetcher.py tests/test_fetcher.py
git commit -m "feat(fetcher): should_fetch + conditional HTTP"
```

---

## Task 7: Fetcher — Feed Parsers

**Goal:** Parse RSS, Atom, and JSON (HN Algolia format) responses into a uniform `ParsedItem` list. Compute item hashes. Handle malformed feeds gracefully.

**Files:**
- Modify: `src/newsroom/fetcher.py`
- Modify: `tests/test_fetcher.py`
- Create: `tests/fixtures/feed_rss_sample.xml`
- Create: `tests/fixtures/feed_atom_sample.xml`
- Create: `tests/fixtures/feed_hn_algolia.json`

- [x] **Step 1: Create RSS fixture**

`tests/fixtures/feed_rss_sample.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample Feed</title>
    <item>
      <title>First Item</title>
      <link>https://example.com/first</link>
      <description>First item body</description>
      <pubDate>Sat, 19 Apr 2026 10:00:00 GMT</pubDate>
      <author>author@example.com (Author One)</author>
    </item>
    <item>
      <title>Second Item</title>
      <link>https://example.com/second</link>
      <description>Second item body</description>
      <pubDate>Sat, 19 Apr 2026 11:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
```

- [x] **Step 2: Create Atom fixture**

`tests/fixtures/feed_atom_sample.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Sample Atom</title>
  <id>https://example.com/</id>
  <entry>
    <title>Atom Entry One</title>
    <link href="https://example.com/atom/1"/>
    <id>https://example.com/atom/1</id>
    <summary>Entry one body</summary>
    <published>2026-04-19T10:00:00Z</published>
    <author><name>Atom Author</name></author>
  </entry>
</feed>
```

- [x] **Step 3: Create HN Algolia JSON fixture**

`tests/fixtures/feed_hn_algolia.json`:

```json
{
  "hits": [
    {
      "objectID": "12345",
      "title": "HN Story One",
      "url": "https://example.com/hn-one",
      "author": "hnuser",
      "points": 150,
      "created_at": "2026-04-19T09:00:00Z"
    },
    {
      "objectID": "12346",
      "title": "HN Story Two",
      "url": "https://example.com/hn-two",
      "author": "hnuser2",
      "points": 200,
      "created_at": "2026-04-19T09:30:00Z"
    }
  ]
}
```

- [x] **Step 4: Append parser tests to `tests/test_fetcher.py`**

```python
from newsroom.fetcher import ParsedItem, parse_feed


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
    item_a = ParsedItem(url="https://x.com/a", title="T", author=None, published_at=None, raw_summary=None)
    item_b = ParsedItem(url="https://x.com/a", title="T", author="different", published_at="later", raw_summary=None)
    assert item_a.item_hash == item_b.item_hash
    assert len(item_a.item_hash) == 16
```

- [x] **Step 5: Append parsers to `src/newsroom/fetcher.py`**

```python
import hashlib
import json

import feedparser


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
        # Reserved for Task where we add OpenAI scraping; Phase 1 primarily uses RSS
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
        published = entry.get("published", entry.get("updated"))
        summary = entry.get("summary")
        items.append(ParsedItem(
            url=url, title=title, author=author,
            published_at=_normalize_date(published),
            raw_summary=summary,
        ))
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
        items.append(ParsedItem(
            url=url, title=title,
            author=hit.get("author"),
            published_at=hit.get("created_at"),
            raw_summary=f"HN points: {hit.get('points', 0)}",
        ))
    return items


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        from dateutil import parser as dateparser
        return dateparser.parse(raw).isoformat()
    except (ValueError, TypeError):
        return None
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_fetcher.py -v
```

Expected: all parser tests PASS.

- [x] **Step 7: Commit**

```bash
git add src/newsroom/fetcher.py tests/test_fetcher.py tests/fixtures/feed_*.xml tests/fixtures/feed_*.json
git commit -m "feat(fetcher): RSS/Atom/JSON parsers with uniform ParsedItem"
```

---

## Task 8: Fetcher — Orchestration

**Goal:** Top-level `fetch_due_sources(state)` that iterates enabled sources, runs conditional fetch, parses, inserts items, updates source state, handles errors.

**Files:**
- Modify: `src/newsroom/fetcher.py` (add orchestration)
- Modify: `tests/test_fetcher.py`

- [x] **Step 1: Write orchestration tests**

Append to `tests/test_fetcher.py`:

```python
from newsroom.fetcher import fetch_due_sources


def _insert_source_via_state(state, name="s", interval=3600, url="https://e.com/rss", feed_type="rss"):
    from newsroom.config import Source
    state.upsert_source(Source(
        name=name, category="ai", subcategory=None, url=url,
        feed_type=feed_type, interval_seconds=interval, enabled=True,
    ))


async def test_fetch_due_sources_inserts_items(state, httpx_mock: HTTPXMock, fixtures_dir: Path) -> None:
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


async def test_fetch_due_sources_skips_not_due(state, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    # Simulate recent fetch
    state.update_source_fetch_state(
        name="s", last_checked_at=datetime.now(timezone.utc), last_fetched_at=None,
    )
    results = await fetch_due_sources(state)
    assert results == []


async def test_fetch_due_sources_handles_304(state, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    httpx_mock.add_response(url="https://e.com/rss", status_code=304)
    results = await fetch_due_sources(state)
    assert results[0].items_inserted == 0
    assert results[0].status == 304


async def test_fetch_due_sources_handles_network_error(state, httpx_mock: HTTPXMock) -> None:
    _insert_source_via_state(state)
    httpx_mock.add_exception(httpx.ConnectError("refused"))
    results = await fetch_due_sources(state)
    assert results[0].error is not None
    row = state.get_source_by_name("s")
    assert row["consecutive_errors"] == 1
```

- [x] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_fetcher.py -v
```

Expected: new tests fail (`fetch_due_sources` not defined).

- [x] **Step 3: Implement orchestration in `src/newsroom/fetcher.py`**

Append:

```python
@dataclass
class FetchResult:
    """Summary of one source's fetch attempt."""
    source_name: str
    status: int | None
    items_inserted: int
    error: str | None = None


async def fetch_due_sources(
    state: "State",  # noqa: F821 (forward ref)
    *,
    category: str | None = None,
    source: str | None = None,
) -> list[FetchResult]:
    """Iterate all enabled sources, fetch due ones, insert items, update state.

    `category` and `source` are optional debug filters — only matching sources are
    considered. Production launchd invocations pass neither, so the full set runs.
    """
    from newsroom.state import State  # local import to avoid circular dep
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


async def _fetch_and_ingest_one(src_row: Any, state: "State") -> FetchResult:  # noqa: F821
    outcome = await fetch_one_raw(src_row)
    name = src_row["name"]
    now = datetime.now(timezone.utc)

    if outcome.error is not None:
        state.increment_source_error(name, outcome.error)
        return FetchResult(source_name=name, status=None, items_inserted=0, error=outcome.error)

    if outcome.status == 304:
        state.update_source_fetch_state(name=name, last_checked_at=now)
        state.reset_source_errors(name)
        return FetchResult(source_name=name, status=304, items_inserted=0)

    if outcome.status != 200:
        err = f"HTTP {outcome.status}"
        state.increment_source_error(name, err)
        return FetchResult(source_name=name, status=outcome.status, items_inserted=0, error=err)

    # 200 OK: parse + insert
    assert outcome.body is not None
    parsed = parse_feed(outcome.body, feed_type=src_row["feed_type"])
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
    return FetchResult(source_name=name, status=200, items_inserted=inserted)
```

- [x] **Step 4: Run all tests**

```bash
uv run pytest -v
```

Expected: everything passes.

- [x] **Step 5: Commit**

```bash
git add src/newsroom/fetcher.py tests/test_fetcher.py
git commit -m "feat(fetcher): orchestration loop with state updates"
```

---

## Task 9: Filter-arXiv Module

**Goal:** A small module that asks Haiku whether an arXiv paper is relevant to the user's themes. Returns True/False, updates the item's `arxiv_relevant` + status.

**Files:**
- Create: `src/newsroom/filter_arxiv.py`
- Create: `tests/test_filter_arxiv.py`
- Create: `config/prompts/filter_arxiv.md`

- [x] **Step 1: Write the prompt**

Create `config/prompts/filter_arxiv.md`:

```markdown
You are filtering arXiv paper abstracts for relevance to a specific set of AI/ML research interests.

**Themes of interest (high relevance):**
- LLM agents, tool-use, multi-step reasoning
- Memory architectures for long-context or persistent state
- Inference optimization, quantization, KV-cache efficiency
- Long-context models, retrieval-augmented generation, context compression
- RLHF, RLAIF, preference learning, constitutional AI
- AI safety, alignment, interpretability, evaluation
- Agent evaluation, benchmarks for reasoning / coding / tool-use

**Themes NOT of interest (low relevance):**
- Pure vision (unless multimodal with LLMs)
- Classical ML / non-neural approaches
- Domain-specific applications (medical imaging, finance, biology) unless they showcase novel LLM techniques
- Theoretical / math-heavy results without practical LLM impact

**Paper:**
- Title: {{ title }}
- Abstract: {{ summary }}

**Your task:** Decide if this paper is relevant. Respond with a JSON object:

```
{"relevant": true | false, "reason": "<one short sentence>"}
```

Be conservative — when in doubt, mark `false`. We prefer to miss some relevant papers rather than flood the user with noise.
```

- [x] **Step 2: Write failing tests**

Create `tests/test_filter_arxiv.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from newsroom.config import Source
from newsroom.filter_arxiv import filter_pending_arxiv_items
from newsroom.state import State


@pytest.fixture
def state_with_arxiv_item(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="arxiv-cs-cl", category="ai", subcategory="arxiv",
        url="https://arxiv.org/rss/cs.CL", feed_type="rss",
        interval_seconds=86400, enabled=True,
    ))
    src_id = state.get_source_by_name("arxiv-cs-cl")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1",
        url="https://arxiv.org/abs/2604.12345",
        title="Agent Memory with Vector Retrieval",
        author="Author", published_at=None,
        raw_summary="Abstract about memory in LLM agents...",
        category="ai",
    )
    return state


async def test_filter_marks_relevant_items_filtered_in(state_with_arxiv_item: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": True, "reason": "agent memory"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    row = state_with_arxiv_item.connection().execute(
        "SELECT * FROM items WHERE title LIKE '%Memory%'"
    ).fetchone()
    assert row["status"] == "filtered_in"
    assert row["arxiv_relevant"] == 1


async def test_filter_marks_irrelevant_filtered_out(state_with_arxiv_item: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": False, "reason": "medical imaging"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    row = state_with_arxiv_item.connection().execute("SELECT * FROM items").fetchone()
    assert row["status"] == "filtered_out"
    assert row["arxiv_relevant"] == 0


async def test_filter_only_processes_arxiv_subcategory(state_with_arxiv_item: State) -> None:
    # Add a non-arxiv item; filter should leave it alone
    state_with_arxiv_item.upsert_source(Source(
        name="simon-willison", category="ai", subcategory="curated",
        url="https://simonwillison.net/atom/everything/", feed_type="atom",
        interval_seconds=3600, enabled=True,
    ))
    src_id = state_with_arxiv_item.get_source_by_name("simon-willison")["id"]
    state_with_arxiv_item.insert_item(
        source_id=src_id, item_hash="h2", url="https://simonwillison.net/post",
        title="Post", author=None, published_at=None, raw_summary="body", category="ai",
    )
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"relevant": True, "reason": "ok"}
    await filter_pending_arxiv_items(state_with_arxiv_item, agent=mock_client)
    # Only 1 call — the arxiv item
    assert mock_client.ask.call_count == 1
```

- [x] **Step 3: Run tests to verify failure**

```bash
uv run pytest tests/test_filter_arxiv.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.filter_arxiv'`

- [x] **Step 4: Implement `src/newsroom/filter_arxiv.py`**

```python
"""Pre-scoring filter for arXiv items: Haiku decides relevance to user's themes."""
from __future__ import annotations

import logging

from newsroom.agent_client import AgentClient, ParseError

logger = logging.getLogger(__name__)


async def filter_pending_arxiv_items(state, agent: AgentClient | None = None) -> int:
    """Filter all 'new' items from arxiv sources. Returns count processed."""
    if agent is None:
        agent = AgentClient()
    conn = state.connection()
    arxiv_items = conn.execute(
        "SELECT items.* FROM items "
        "JOIN sources ON items.source_id = sources.id "
        "WHERE items.status = 'new' AND sources.subcategory = 'arxiv'"
    ).fetchall()

    processed = 0
    for item in arxiv_items:
        try:
            result = await agent.ask(
                prompt_name="filter_arxiv",
                variables={
                    "title": item["title"],
                    "summary": item["raw_summary"] or "",
                },
                model="claude-haiku-4-5",
                parse="json",
            )
            relevant = bool(result.get("relevant", False))
            state.mark_item_arxiv_filter(item_id=item["id"], relevant=relevant)
            processed += 1
        except (ParseError, Exception) as e:  # noqa: BLE001 — we want broad safety here
            logger.warning("arxiv filter failed for item %s: %s", item["id"], e)
            # Leave item in 'new' status → will retry next cycle
    return processed
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_filter_arxiv.py -v
```

Expected: all 3 tests PASS.

- [x] **Step 6: Commit**

```bash
git add src/newsroom/filter_arxiv.py tests/test_filter_arxiv.py config/prompts/filter_arxiv.md
git commit -m "feat(filter-arxiv): haiku-based relevance filter for arxiv papers"
```

---

## Task 10: Scorer Module

**Goal:** Score every `new` or `filtered_in` item with Haiku → importance 1–5 + short reason.

**Files:**
- Create: `src/newsroom/scorer.py`
- Create: `tests/test_scorer.py`
- Create: `config/prompts/score_item.md`

- [x] **Step 1: Write the prompt**

Create `config/prompts/score_item.md`:

```markdown
You are rating the importance of a single news item for a reader who follows AI/LLM/ML research and industry news.

**Importance scale:**
- **5 — Major event:** New model release from a top lab (Claude, GPT, Gemini, Llama), significant policy changes, paradigm-shifting research, industry-shaping announcements.
- **4 — Notable:** New feature from a major product, high-quality research paper with clear impact, widely discussed community post.
- **3 — Interesting:** Useful tutorial, solid technical post, mid-tier research, niche news with limited audience.
- **2 — Minor:** Routine updates, incremental improvements, minor releases.
- **1 — Trivia:** Off-topic, duplicates, promotional, noise.

**Item:**
- Source: {{ source_name }}
- Title: {{ title }}
- Author: {{ author }}
- Body: {{ summary }}

Respond with a JSON object:

```
{"importance": <1-5>, "reason": "<one short sentence>"}
```

Be calibrated — reserve 5 for truly major events. Most items should be 2–3.
```

- [x] **Step 2: Write failing tests**

Create `tests/test_scorer.py`:

```python
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from newsroom.config import Source
from newsroom.scorer import score_pending_items
from newsroom.state import State


@pytest.fixture
def state_with_items(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="anthropic", category="ai", subcategory="lab",
        url="https://www.anthropic.com/news/rss.xml", feed_type="rss",
        interval_seconds=3600, enabled=True,
    ))
    src_id = state.get_source_by_name("anthropic")["id"]
    for i in range(3):
        state.insert_item(
            source_id=src_id, item_hash=f"h{i}",
            url=f"https://a.com/{i}", title=f"Title {i}",
            author=None, published_at=None, raw_summary=f"summary {i}", category="ai",
        )
    return state


async def test_scorer_writes_importance(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.side_effect = [
        {"importance": 5, "reason": "major release"},
        {"importance": 3, "reason": "interesting post"},
        {"importance": 2, "reason": "minor update"},
    ]
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=10)
    rows = state_with_items.connection().execute(
        "SELECT * FROM items ORDER BY importance DESC"
    ).fetchall()
    assert [r["importance"] for r in rows] == [5, 3, 2]
    assert all(r["status"] == "scored" for r in rows)
    assert notifier_mock.maybe_notify.call_count == 3


async def test_scorer_limits_batch_size(state_with_items: State) -> None:
    mock_client = AsyncMock()
    mock_client.ask.return_value = {"importance": 3, "reason": "ok"}
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=2)
    assert mock_client.ask.call_count == 2
    remaining = state_with_items.list_items_by_status("new", limit=10)
    assert len(remaining) == 1


async def test_scorer_leaves_item_on_parse_error(state_with_items: State) -> None:
    from newsroom.agent_client import ParseError
    mock_client = AsyncMock()
    mock_client.ask.side_effect = ParseError("bad JSON")
    notifier_mock = AsyncMock()
    await score_pending_items(state_with_items, agent=mock_client, notifier=notifier_mock, limit=1)
    remaining = state_with_items.list_items_by_status("new", limit=10)
    assert len(remaining) == 3  # none scored
```

- [x] **Step 3: Run tests to verify failure**

```bash
uv run pytest tests/test_scorer.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.scorer'`

- [x] **Step 4: Implement `src/newsroom/scorer.py`**

```python
"""Score items for importance via Haiku, trigger notifications for high-importance."""
from __future__ import annotations

import logging
from typing import Any

from newsroom.agent_client import AgentClient, ParseError


logger = logging.getLogger(__name__)


SCORING_MODEL = "claude-haiku-4-5"


async def score_pending_items(
    state,
    *,
    agent: AgentClient | None = None,
    notifier: Any | None = None,
    limit: int = 100,
) -> int:
    """Score every `new` or `filtered_in` item. Returns count scored."""
    if agent is None:
        agent = AgentClient()

    conn = state.connection()
    pending = conn.execute(
        "SELECT items.*, sources.name AS source_name FROM items "
        "JOIN sources ON items.source_id = sources.id "
        "WHERE items.status IN ('new', 'filtered_in') "
        "ORDER BY items.fetched_at ASC LIMIT ?",
        (limit,),
    ).fetchall()

    scored = 0
    for item in pending:
        try:
            result = await agent.ask(
                prompt_name="score_item",
                variables={
                    "source_name": item["source_name"],
                    "title": item["title"],
                    "author": item["author"] or "Unknown",
                    "summary": item["raw_summary"] or "(no body)",
                },
                model=SCORING_MODEL,
                parse="json",
            )
            importance = int(result.get("importance", 2))
            if not 1 <= importance <= 5:
                importance = 2
            reason = str(result.get("reason", ""))
            state.mark_item_scored(
                item_id=item["id"], importance=importance,
                reason=reason, model=SCORING_MODEL,
            )
            scored += 1
            if notifier is not None:
                # Re-fetch updated row so notifier has importance filled in
                updated = state.connection().execute(
                    "SELECT items.*, sources.name AS source_name "
                    "FROM items JOIN sources ON items.source_id = sources.id "
                    "WHERE items.id = ?",
                    (item["id"],),
                ).fetchone()
                await notifier.maybe_notify(updated, state)
        except (ParseError, Exception) as e:  # noqa: BLE001
            logger.warning("scoring failed for item %s: %s", item["id"], e)
    return scored
```

- [x] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_scorer.py -v
```

Expected: all 3 tests PASS.

- [x] **Step 6: Commit**

```bash
git add src/newsroom/scorer.py tests/test_scorer.py config/prompts/score_item.md
git commit -m "feat(scorer): haiku importance scoring with notifier trigger"
```

---

## Task 11: Notifier Module

**Goal:** Decide whether an item warrants a push (threshold + quiet hours + category rules + dedup), then send via pync.

**Files:**
- Create: `src/newsroom/notifier.py`
- Create: `tests/test_notifier.py`

- [x] **Step 1: Write failing tests**

Create `tests/test_notifier.py`:

```python
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time

from newsroom.config import Source
from newsroom.notifier import (
    Notifier,
    compute_threshold_for_hour,
    meets_threshold,
)
from newsroom.state import State


def test_threshold_daytime() -> None:
    assert compute_threshold_for_hour(10, category="ai") == 4
    assert compute_threshold_for_hour(15, category="ai") == 4


def test_threshold_quiet_hours() -> None:
    assert compute_threshold_for_hour(23, category="ai") == 5
    assert compute_threshold_for_hour(3, category="ai") == 5


def test_threshold_arxiv_never_pushes() -> None:
    # arxiv subcategory should never push single items; effective threshold is impossible
    assert compute_threshold_for_hour(10, subcategory="arxiv") >= 6


def test_meets_threshold_true() -> None:
    item = {"importance": 5, "category": "ai"}
    assert meets_threshold(item, hour=23, subcategory=None)


def test_meets_threshold_false_in_quiet_hours() -> None:
    item = {"importance": 4, "category": "ai"}
    assert not meets_threshold(item, hour=23, subcategory=None)


async def test_notifier_sends_notification_for_high_importance(tmp_path: Path) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="a", category="ai", subcategory="lab",
        url="https://a.com/rss", feed_type="rss", interval_seconds=3600,
    ))
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1", url="https://a.com/x", title="Important",
        author=None, published_at=None, raw_summary="body", category="ai",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=5, reason="major", model="haiku")
    row = state.connection().execute(
        "SELECT items.*, sources.name AS source_name, sources.subcategory AS source_subcategory "
        "FROM items JOIN sources ON items.source_id = sources.id WHERE items.id = ?",
        (item_id,),
    ).fetchone()

    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    send_mock.assert_awaited_once()
    call_args = send_mock.call_args
    assert "Important" in call_args.kwargs.get("message", "") or "Important" in call_args.args[1]


async def test_notifier_skips_below_threshold(tmp_path: Path) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="a", category="ai", subcategory="lab",
        url="https://a.com/rss", feed_type="rss", interval_seconds=3600,
    ))
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1", url="https://a.com/x", title="Minor",
        author=None, published_at=None, raw_summary="body", category="ai",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=2, reason="minor", model="haiku")
    row = state.connection().execute(
        "SELECT items.*, sources.name AS source_name, sources.subcategory AS source_subcategory "
        "FROM items JOIN sources ON items.source_id = sources.id WHERE items.id = ?",
        (item_id,),
    ).fetchone()
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    send_mock.assert_not_awaited()


async def test_notifier_marks_item_notified(tmp_path: Path) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="a", category="ai", subcategory="lab",
        url="https://a.com/rss", feed_type="rss", interval_seconds=3600,
    ))
    src_id = state.get_source_by_name("a")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1", url="https://a.com/x", title="T",
        author=None, published_at=None, raw_summary="body", category="ai",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=5, reason="major", model="haiku")
    row = state.connection().execute(
        "SELECT items.*, sources.name AS source_name, sources.subcategory AS source_subcategory "
        "FROM items JOIN sources ON items.source_id = sources.id WHERE items.id = ?",
        (item_id,),
    ).fetchone()
    send_mock = AsyncMock()
    notifier = Notifier(send_fn=send_mock)
    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(row, state)
    updated = state.connection().execute(
        "SELECT notified_at FROM items WHERE id = ?", (item_id,),
    ).fetchone()
    assert updated["notified_at"] is not None
```

- [x] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_notifier.py -v
```

Expected: module not found.

- [x] **Step 3: Implement `src/newsroom/notifier.py`**

```python
"""macOS push notifications for high-importance items."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Notification category/label prefix
TITLE_PREFIX = {
    "ai": "AI",
}


def compute_threshold_for_hour(
    hour: int,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> int:
    """Effective importance threshold for pushing a notification."""
    # Base threshold
    quiet_hours = hour >= 22 or hour < 7
    base = 5 if quiet_hours else 4

    # arxiv sub-category: never single-push
    if subcategory == "arxiv":
        return 99

    return base


def meets_threshold(item: Any, *, hour: int, subcategory: str | None) -> bool:
    threshold = compute_threshold_for_hour(
        hour, category=item["category"], subcategory=subcategory,
    )
    importance = item["importance"] or 0
    return importance >= threshold


def _send_with_pync(title: str, message: str, open_url: str | None = None) -> None:
    """Real macOS notification via pync. Synchronous — wrap in executor from async code."""
    try:
        import pync  # pync is macOS-only
    except ImportError:
        logger.warning("pync not available; notification skipped")
        return
    pync.notify(message, title=title, open=open_url or "")


class Notifier:
    """Encapsulates push-notification logic. `send_fn` is async for test injection."""

    def __init__(
        self,
        *,
        send_fn: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        if send_fn is None:
            async def default_send(*, title: str, message: str, url: str | None) -> None:
                import anyio
                await anyio.to_thread.run_sync(lambda: _send_with_pync(title, message, url))
            send_fn = default_send
        self._send = send_fn

    async def maybe_notify(self, item: Any, state) -> None:
        """Decide whether to notify; if yes, send and mark."""
        # Already notified?
        if item["notified_at"]:
            return

        now = datetime.now(BERLIN_TZ)
        hour = now.hour
        subcategory = item["source_subcategory"]

        if not meets_threshold(item, hour=hour, subcategory=subcategory):
            return

        prefix = TITLE_PREFIX.get(item["category"], item["category"].capitalize())
        title = f"[{prefix}] {item['source_name']}"
        summary = (item["raw_summary"] or "")[:140].strip()
        message = f"{item['title']}\n\n{summary}" if summary else item["title"]

        try:
            await self._send(title=title, message=message, url=item["url"])
            state.mark_item_notified(item_id=item["id"])
        except Exception as e:  # noqa: BLE001
            logger.warning("notification send failed for item %s: %s", item["id"], e)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_notifier.py -v
```

Expected: all 7 tests PASS.

- [x] **Step 5: Commit**

```bash
git add src/newsroom/notifier.py tests/test_notifier.py
git commit -m "feat(notifier): threshold, quiet-hours, pync-based push"
```

---

## Task 12: Digester — Collection + Opus + Write

**Goal:** Generate morning and evening digests: collect scored items, call Opus, write markdown to `!AI/news/YYYY/MM/YYYY-MM-DD.md`.

**Files:**
- Create: `src/newsroom/digester.py`
- Create: `tests/test_digester.py`
- Create: `config/prompts/digest_morning.md`
- Create: `config/prompts/digest_evening.md`

- [x] **Step 1: Write morning-digest prompt**

Create `config/prompts/digest_morning.md`:

```markdown
You are writing the **morning** news digest for a reader who wants to catch up on what happened overnight in AI/LLM/ML.

**Style:** Compact, German headlines, English original quotes preserved where helpful (prefix `› ""`). Each item: headline + one-sentence key point + link.

**Items (grouped by sub-category, sorted by importance):**

{{ items_markdown }}

**Output structure:**

```markdown
# News-Digest {{ date_de }} (Morgen)

## AI / LLM — Labs
- **[5]** [Headline](url) · source · _reason why important_
- ...

## AI / LLM — Community & Curated
- ...

## AI / Claude Code
- ...
```

**Rules:**
- Omit empty sections.
- If an item has a `summary_path`, add `📄 [Tief-Zusammenfassung](summary_path)` at the end of that bullet.
- Deutsche Headlines; bei englischen Original-Titeln: verwende eine knappe deutsche Übersetzung, und zitiere ggf. den Originaltitel mit `› "…"`.
- Fachbegriffe wie LLM, RAG, Fine-Tuning, Embedding bleiben englisch.
- Maximal 3 Sätze pro Kernaussage. Kein Preamble, kein Abschluss. Nur Markdown-Content.
```

- [x] **Step 2: Write evening-digest prompt**

Create `config/prompts/digest_evening.md` as the same template, but with `(Abend)` and note "Tages-Zusammenfassung since this morning":

```markdown
You are writing the **evening** news digest for a reader who wants a day-end summary of AI/LLM/ML events since this morning.

**Style:** Compact, German headlines, English original quotes preserved where helpful (prefix `› ""`). Each item: headline + one-sentence key point + link.

**Items (grouped by sub-category, sorted by importance):**

{{ items_markdown }}

**Output structure:**

```markdown
## Abend-Digest

## AI / LLM — Labs
- **[5]** [Headline](url) · source · _reason_
...
```

**Rules:**
- Start output with `## Abend-Digest` (no top-level # — this will be appended to the morning file).
- Omit empty sections.
- If an item has a `summary_path`, add `📄 [Tief-Zusammenfassung](summary_path)` at the end of that bullet.
- Deutsche Headlines; bei englischen Original-Titeln: knappe deutsche Übersetzung + ggf. englischer Original-Titel als `› "…"`.
- Fachbegriffe wie LLM, RAG, Fine-Tuning, Embedding bleiben englisch.
- Maximal 3 Sätze pro Kernaussage. Kein Preamble. Nur Markdown-Content.
```

- [x] **Step 3: Write failing tests**

Create `tests/test_digester.py`:

```python
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from newsroom.config import Source
from newsroom.digester import (
    determine_slot, format_items_for_prompt, generate_digest, NoSlotError,
)
from newsroom.state import State


@pytest.fixture
def populated_state(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="anthropic", category="ai", subcategory="lab",
        url="https://www.anthropic.com/news/rss.xml", feed_type="rss",
        interval_seconds=3600, enabled=True,
    ))
    src_id = state.get_source_by_name("anthropic")["id"]
    for i, imp in enumerate([5, 4, 3]):
        state.insert_item(
            source_id=src_id, item_hash=f"h{i}",
            url=f"https://a.com/{i}", title=f"Title {i}",
            author=None, published_at="2026-04-19T02:00:00Z",
            raw_summary=f"summary {i}", category="ai",
        )
        item_id = state.list_items_by_status("new", limit=1)[0]["id"]
        state.mark_item_scored(item_id=item_id, importance=imp, reason=f"r{i}", model="haiku")
    return state


@freeze_time("2026-04-19 07:30")
def test_determine_slot_morning() -> None:
    assert determine_slot() == "morning"


@freeze_time("2026-04-19 20:15")
def test_determine_slot_evening() -> None:
    assert determine_slot() == "evening"


@freeze_time("2026-04-19 13:00")
def test_determine_slot_returns_none_outside_windows() -> None:
    with pytest.raises(NoSlotError):
        determine_slot()


def test_format_items_for_prompt_groups_by_subcategory(populated_state: State) -> None:
    # Add a second source with different subcategory
    populated_state.upsert_source(Source(
        name="simon", category="ai", subcategory="curated",
        url="https://simonwillison.net/atom/everything/", feed_type="atom",
        interval_seconds=3600, enabled=True,
    ))
    src_id = populated_state.get_source_by_name("simon")["id"]
    populated_state.insert_item(
        source_id=src_id, item_hash="h-simon",
        url="https://simonwillison.net/x", title="Simon Post",
        author="Simon", published_at=None, raw_summary="body", category="ai",
    )
    item_id = populated_state.list_items_by_status("new", limit=1)[0]["id"]
    populated_state.mark_item_scored(item_id=item_id, importance=4, reason="curated", model="haiku")
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "lab" in formatted.lower() or "Labs" in formatted
    assert "curated" in formatted.lower() or "Curated" in formatted


async def test_generate_digest_writes_file(populated_state: State, tmp_path: Path) -> None:
    output_root = tmp_path / "news"
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# News-Digest 19. April 2026 (Morgen)\n\n## AI / LLM\n- Dummy"
    await generate_digest(
        state=populated_state, slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root, agent=mock_agent,
    )
    out = output_root / "2026" / "04" / "2026-04-19.md"
    assert out.exists()
    body = out.read_text()
    assert "Morgen" in body


async def test_generate_digest_records_in_state(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Test"
    await generate_digest(
        state=populated_state, slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news", agent=mock_agent,
    )
    row = populated_state.get_digest("2026-04-19", "morning")
    assert row is not None
    assert row["item_count"] == 3
    assert row["model"] == "claude-opus-4-7"


async def test_generate_digest_marks_items_included(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Test"
    await generate_digest(
        state=populated_state, slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news", agent=mock_agent,
    )
    rows = populated_state.connection().execute(
        "SELECT * FROM items WHERE included_in_digest IS NOT NULL"
    ).fetchall()
    assert len(rows) == 3


async def test_generate_digest_evening_appends(populated_state: State, tmp_path: Path) -> None:
    output_root = tmp_path / "news"
    # Pre-create morning file
    morning_dir = output_root / "2026" / "04"
    morning_dir.mkdir(parents=True)
    (morning_dir / "2026-04-19.md").write_text("# Morgen Content\n")

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "## Abend-Digest\n\n- x"
    await generate_digest(
        state=populated_state, slot="evening",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root, agent=mock_agent,
    )
    body = (morning_dir / "2026-04-19.md").read_text()
    assert "# Morgen Content" in body
    assert "## Abend-Digest" in body
```

- [x] **Step 4: Run tests to verify failure**

```bash
uv run pytest tests/test_digester.py -v
```

Expected: `ModuleNotFoundError: No module named 'newsroom.digester'`

- [x] **Step 5: Implement `src/newsroom/digester.py`**

```python
"""Morning/evening digest generation via Opus."""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from newsroom.agent_client import AgentClient

logger = logging.getLogger(__name__)


BERLIN_TZ = ZoneInfo("Europe/Berlin")
DIGEST_MODEL = "claude-opus-4-7"
DEFAULT_OUTPUT_ROOT = Path.home() / "Documents" / "!AI" / "news"

MORNING_START_HOUR = 5
MORNING_END_HOUR = 12   # exclusive
EVENING_START_HOUR = 16
EVENING_END_HOUR = 24   # exclusive


class NoSlotError(Exception):
    """Current hour is outside morning and evening windows."""


def determine_slot(now: datetime | None = None) -> str:
    now = now or datetime.now(BERLIN_TZ)
    if MORNING_START_HOUR <= now.hour < MORNING_END_HOUR:
        return "morning"
    if EVENING_START_HOUR <= now.hour < EVENING_END_HOUR:
        return "evening"
    raise NoSlotError(f"hour {now.hour} is not within morning or evening windows")


def _cutoff_for_slot(slot: str, state) -> str:
    """ISO timestamp: items scored AFTER this are eligible."""
    # Last digest of the OTHER slot defines the cutoff
    other = "evening" if slot == "morning" else "morning"
    last = state.get_last_digest_generated_at(other)
    if last:
        return last
    # No prior digest: use 24h ago
    return (datetime.now(BERLIN_TZ) - timedelta(days=1)).isoformat()


def _resolve_cross_link(item_url: str, summaries_dir: Path) -> Path | None:
    """Check if an article_summaries file exists matching this URL."""
    if not summaries_dir.exists():
        return None
    for f in summaries_dir.glob("*.md"):
        # cheap check: URL appears in the file
        try:
            if item_url in f.read_text(errors="replace"):
                return f
        except OSError:
            continue
    return None


def format_items_for_prompt(items, summaries_dir: Path | None = None) -> str:
    """Group items by subcategory and produce the markdown payload for the prompt."""
    groups: dict[str, list] = defaultdict(list)
    for item in items:
        groups[item["source_subcategory"] or "other"].append(item)

    lines: list[str] = []
    for subcat in sorted(groups):
        lines.append(f"### {subcat}")
        for item in sorted(groups[subcat], key=lambda x: (-x["importance"], x["title"])):
            summary_link = ""
            if summaries_dir is not None:
                link = _resolve_cross_link(item["url"], summaries_dir)
                if link:
                    summary_link = f" | summary_path={link}"
            lines.append(
                f"- [{item['importance']}] {item['title']} · {item['source_name']} · "
                f"url={item['url']} · reason={item['score_reason']}{summary_link}"
            )
            body = (item["raw_summary"] or "").strip().replace("\n", " ")
            if body:
                lines.append(f"  > {body[:300]}")
        lines.append("")
    return "\n".join(lines)


async def generate_digest(
    *,
    state,
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
) -> Path:
    """Generate digest for the given slot+date. Returns path of written file."""
    if agent is None:
        agent = AgentClient()
    if summaries_dir is None:
        summaries_dir = Path.home() / "Documents" / "!AI" / "article_summaries"

    existing = state.get_digest(date.isoformat(), slot)
    if existing and not force:
        logger.info("digest already exists for %s/%s, skipping", date, slot)
        return Path(existing["file_path"])

    cutoff = _cutoff_for_slot(slot, state)
    items = state.list_items_for_digest(since_iso=cutoff)

    target_dir = output_root / f"{date.year:04d}" / f"{date.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{date.isoformat()}.md"

    if not items:
        # No items → write/append placeholder, still mark digest
        content = (
            f"# News-Digest {date.strftime('%d.%m.%Y')} ({slot.capitalize()})\n\n"
            "_Keine neuen Items seit dem letzten Digest._\n"
            if slot == "morning"
            else "\n\n---\n\n## Abend-Digest\n\n_Keine neuen Items seit Morgen-Digest._\n"
        )
        _write_digest_file(target_file, content, slot=slot)
        state.insert_digest(
            date=date.isoformat(), slot=slot, file_path=str(target_file),
            item_count=0, model=DIGEST_MODEL,
        )
        return target_file

    items_md = format_items_for_prompt(items, summaries_dir=summaries_dir)
    date_de = date.strftime("%-d. %B %Y")  # "19. April 2026" on macOS/Linux

    content = await agent.ask(
        prompt_name=f"digest_{slot}",
        variables={"items_markdown": items_md, "date_de": date_de},
        model=DIGEST_MODEL,
        parse="text",
    )

    _write_digest_file(target_file, content, slot=slot)

    for item in items:
        state.mark_item_digested(
            item_id=item["id"], digest_label=f"{date.isoformat()}-{slot}",
        )
    state.insert_digest(
        date=date.isoformat(), slot=slot, file_path=str(target_file),
        item_count=len(items), model=DIGEST_MODEL,
    )
    return target_file


def _write_digest_file(path: Path, content: str, *, slot: str) -> None:
    """Write or append digest content. Morning creates; evening appends."""
    if slot == "morning" or not path.exists():
        path.write_text(content if content.endswith("\n") else content + "\n")
    else:
        existing = path.read_text()
        separator = "\n\n---\n\n" if not existing.endswith("\n---\n\n") else ""
        path.write_text(existing + separator + (content if content.endswith("\n") else content + "\n"))
```

- [x] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_digester.py -v
```

Expected: all tests PASS.

- [x] **Step 7: Commit**

```bash
git add src/newsroom/digester.py tests/test_digester.py config/prompts/digest_morning.md config/prompts/digest_evening.md
git commit -m "feat(digester): morning/evening digest generation with cross-linking"
```

---

## Task 13: Digester — Fallback Raw Digest

**Goal:** If the Opus call fails after retries, write a structured "raw" digest without LLM synthesis so the user still gets something readable.

**Files:**
- Modify: `src/newsroom/digester.py`
- Modify: `tests/test_digester.py`

- [x] **Step 1: Append tests**

```python
async def test_generate_digest_falls_back_on_llm_error(populated_state: State, tmp_path: Path) -> None:
    from newsroom.agent_client import AgentError
    mock_agent = AsyncMock()
    mock_agent.ask.side_effect = AgentError("opus unavailable")
    await generate_digest(
        state=populated_state, slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news", agent=mock_agent,
    )
    out = tmp_path / "news" / "2026" / "04" / "2026-04-19.md"
    body = out.read_text()
    assert "Automatisch generiert" in body  # fallback marker
    assert "Title 0" in body  # items still listed
```

- [x] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_digester.py::test_generate_digest_falls_back_on_llm_error -v
```

Expected: FAIL (AgentError propagates).

- [x] **Step 3: Update `generate_digest` to catch AgentError and write fallback**

In `src/newsroom/digester.py`, modify the LLM-call section of `generate_digest`:

```python
    # Replace the `content = await agent.ask(...)` block with:
    try:
        content = await agent.ask(
            prompt_name=f"digest_{slot}",
            variables={"items_markdown": items_md, "date_de": date_de},
            model=DIGEST_MODEL,
            parse="text",
        )
        fallback_used = False
    except Exception as e:  # noqa: BLE001
        logger.warning("Opus digest call failed, using fallback: %s", e)
        content = _build_fallback_digest(items, slot=slot, date=date)
        fallback_used = True
```

Add helper at the bottom of the file:

```python
def _build_fallback_digest(items, *, slot: str, date: _date) -> str:
    """Emergency digest: no LLM synthesis, just a structured list."""
    header = (
        f"# News-Digest {date.strftime('%d.%m.%Y')} ({slot.capitalize()})\n\n"
        "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)\n\n"
        if slot == "morning"
        else "\n\n---\n\n## Abend-Digest\n\n"
             "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)\n\n"
    )
    groups: dict[str, list] = defaultdict(list)
    for item in items:
        groups[item["source_subcategory"] or "other"].append(item)

    lines = [header]
    for subcat in sorted(groups):
        lines.append(f"## {subcat.capitalize()}\n")
        for item in sorted(groups[subcat], key=lambda x: (-x["importance"], x["title"])):
            lines.append(
                f"- **[Importance {item['importance']}]** "
                f"[{item['title']}]({item['url']}) · {item['source_name']}"
            )
        lines.append("")
    lines.append(
        "\n_Generiert ohne LLM-Synthese. Bei Bedarf Kommando "
        "`newsroom digest --force` manuell erneut ausführen._\n"
    )
    return "\n".join(lines)
```

- [x] **Step 4: Run tests**

```bash
uv run pytest tests/test_digester.py -v
```

Expected: all PASS (including the new fallback test).

- [x] **Step 5: Commit**

```bash
git add src/newsroom/digester.py tests/test_digester.py
git commit -m "feat(digester): fallback raw digest on Opus failure"
```

---

## Task 14: CLI Wiring

**Goal:** Connect all subcommands in `cli.py` using typer, with proper `--dry-run`, `--force`, and other flags.

**Files:**
- Modify: `src/newsroom/cli.py`
- Create: `tests/test_cli.py`

- [x] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:

```python
from typer.testing import CliRunner

from newsroom.cli import app


runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "newsroom" in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["fetch", "score", "digest", "notify", "status", "init", "validate-config"]:
        assert cmd in result.stdout


def test_cli_fetch_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("NEWSROOM_SOURCES_PATH", str(tmp_path / "empty.yaml"))
    (tmp_path / "empty.yaml").write_text("sources: []\n")
    # init first
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["fetch", "--dry-run"])
    assert result.exit_code == 0


def test_cli_notify_test_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    # init DB
    runner.invoke(app, ["init"])
    # --test should succeed even without state (sends a ping)
    result = runner.invoke(app, ["notify", "--test"])
    # exit 0 or 1 depending on pync availability; should not raise
    assert result.exit_code in (0, 1)
```

- [x] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: most fail (commands don't exist yet).

- [x] **Step 3: Implement full `src/newsroom/cli.py`**

```python
"""CLI entry point wiring all subcommands via typer."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from newsroom import __version__
from newsroom.agent_client import AgentClient
from newsroom.config import load_sources
from newsroom.digester import determine_slot, generate_digest, NoSlotError
from newsroom.fetcher import fetch_due_sources, should_fetch
from newsroom.filter_arxiv import filter_pending_arxiv_items
from newsroom.notifier import Notifier
from newsroom.scorer import score_pending_items
from newsroom.state import DEFAULT_DB_PATH, State

app = typer.Typer(help="Daily Newsroom — local news-digest agent")
console = Console()
logger = logging.getLogger(__name__)


def _db_path() -> Path:
    return Path(os.environ.get("NEWSROOM_DB_PATH", str(DEFAULT_DB_PATH)))


def _sources_path() -> Path:
    default = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
    return Path(os.environ.get("NEWSROOM_SOURCES_PATH", str(default)))


def _bootstrap_state() -> State:
    state = State(_db_path())
    state.ensure_schema()
    # Sync sources.yaml into DB
    sources_path = _sources_path()
    if sources_path.exists():
        for src in load_sources(sources_path):
            state.upsert_source(src)
    return state


# ── Subcommands ───────────────────────────────────────────────────────────────

@app.command()
def version() -> None:
    """Print version."""
    console.print(f"newsroom {__version__}")


@app.command()
def init() -> None:
    """Initialize state DB (idempotent)."""
    state = _bootstrap_state()
    console.print(f"✓ State-DB ready at {_db_path()}")


@app.command("validate-config")
def validate_config() -> None:
    """Validate sources.yaml."""
    path = _sources_path()
    sources = load_sources(path)
    console.print(f"✓ {len(sources)} sources loaded from {path}")


@app.command()
def fetch(
    dry_run: bool = typer.Option(False, "--dry-run"),
    category: str | None = typer.Option(None, "--category", help="Fetch only this category"),
    source: str | None = typer.Option(None, "--source", help="Fetch only this source"),
) -> None:
    """Fetch all due sources, score new items, maybe push notifications."""
    state = _bootstrap_state()
    if dry_run:
        srcs = state.list_enabled_sources()
        due = [s for s in srcs if should_fetch(s)]
        if category:
            due = [s for s in due if s["category"] == category]
        if source:
            due = [s for s in due if s["name"] == source]
        table = Table(title="Sources due")
        table.add_column("Name"); table.add_column("Category"); table.add_column("Interval")
        for s in due:
            table.add_row(s["name"], s["category"], f"{s['interval_seconds']}s")
        console.print(table)
        return

    results = asyncio.run(_run_fetch_score_notify(state, category=category, source=source))
    table = Table(title="Fetch results")
    table.add_column("Source"); table.add_column("Status"); table.add_column("New items"); table.add_column("Error")
    for r in results:
        table.add_row(r.source_name, str(r.status), str(r.items_inserted), r.error or "")
    console.print(table)

    # Digest catchup check (safety net)
    _maybe_run_digest_catchup(state)


async def _run_fetch_score_notify(
    state: State, *, category: str | None, source: str | None,
):
    results = await fetch_due_sources(state, category=category, source=source)
    agent = AgentClient()
    await filter_pending_arxiv_items(state, agent=agent)
    notifier = Notifier()
    await score_pending_items(state, agent=agent, notifier=notifier, limit=100)
    return results


def _maybe_run_digest_catchup(state: State) -> None:
    """If near-morning or near-evening and no digest for today, generate it."""
    from newsroom.digester import BERLIN_TZ, EVENING_START_HOUR, MORNING_START_HOUR
    now = datetime.now(BERLIN_TZ)
    today = now.date().isoformat()
    for slot, target_hour in [("morning", MORNING_START_HOUR + 2),
                              ("evening", EVENING_START_HOUR + 2)]:
        if abs(now.hour - target_hour) <= 2:
            if state.get_digest(today, slot) is None:
                logger.info("Digest-catchup: generating missed %s digest for %s", slot, today)
                asyncio.run(generate_digest(state=state, slot=slot, date=now.date()))


@app.command()
def score(
    dry_run: bool = typer.Option(False, "--dry-run"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """Score pending items (standalone; normally chained from fetch)."""
    state = _bootstrap_state()
    if dry_run:
        pending = state.list_items_by_status("new", limit=limit)
        pending += state.list_items_by_status("filtered_in", limit=limit)
        console.print(f"Would score {len(pending)} pending items")
        return
    agent = AgentClient()
    notifier = Notifier()
    count = asyncio.run(score_pending_items(state, agent=agent, notifier=notifier, limit=limit))
    console.print(f"Scored {count} items")


@app.command()
def digest(
    dry_run: bool = typer.Option(False, "--dry-run"),
    time_slot: str | None = typer.Option(None, "--time", help="morning|evening"),
    date_str: str | None = typer.Option(None, "--date", help="YYYY-MM-DD (default: today)"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Generate morning or evening digest for today (auto-detects slot from time)."""
    state = _bootstrap_state()
    slot = time_slot
    if slot is None:
        try:
            slot = determine_slot()
        except NoSlotError:
            console.print("Outside morning/evening windows; no digest generated.")
            return
    date = _date.fromisoformat(date_str) if date_str else _date.today()
    if dry_run:
        from newsroom.digester import _cutoff_for_slot, format_items_for_prompt
        cutoff = _cutoff_for_slot(slot, state)
        items = state.list_items_for_digest(since_iso=cutoff)
        console.print(f"Would generate {slot} digest for {date} with {len(items)} items")
        return
    path = asyncio.run(generate_digest(state=state, slot=slot, date=date, force=force))
    console.print(f"✓ Digest written: {path}")


@app.command()
def notify(
    test: bool = typer.Option(False, "--test", help="Send a test notification"),
) -> None:
    """Notification utilities (mainly --test)."""
    if test:
        from newsroom.notifier import _send_with_pync
        _send_with_pync("[Newsroom · Info]", "Notification test — wenn du das siehst, funktioniert pync.")
        console.print("Test notification sent (check Notification Center).")
        return
    console.print("Use --test to send a test notification.")


@app.command()
def status() -> None:
    """Print current state summary (implemented in Task 15)."""
    from newsroom.status import print_status
    state = _bootstrap_state()
    print_status(state, console=console)
```

- [x] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli.py -v
```

Note: the `status` command will fail to import until Task 15. If blocking, stub it temporarily:

```python
@app.command()
def status() -> None:
    console.print("Status (implemented in Task 15)")
```

and restore the real import after Task 15.

- [x] **Step 5: Commit**

```bash
git add src/newsroom/cli.py tests/test_cli.py
git commit -m "feat(cli): wire subcommands with typer"
```

---

## Task 15: Status Command

**Goal:** `newsroom status` prints a comprehensive, rich-formatted overview.

**Files:**
- Create: `src/newsroom/status.py`
- Modify: `src/newsroom/cli.py` (restore real `status` import)

- [x] **Step 1: Implement `src/newsroom/status.py`**

```python
"""Status command: overview of auth, DB, sources, recent activity."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table


def print_status(state, *, console: Console) -> None:
    console.print(Panel.fit("daily-newsroom · Status", style="bold"))

    # Auth
    creds_path = _read_keychain_oauth()
    if creds_path:
        console.print(f"Auth: ✓ {creds_path.get('subscriptionType', '?')} "
                      f"(tier {creds_path.get('rateLimitTier', '?')})")
    else:
        console.print("Auth: [red]✗ no Claude Code credentials found[/red]")

    # DB
    db_size_mb = Path(state.db_path).stat().st_size / 1024 / 1024 if Path(state.db_path).exists() else 0
    item_count = state.connection().execute("SELECT COUNT(*) FROM items").fetchone()[0]
    console.print(f"State-DB: ✓ {db_size_mb:.1f} MB, {item_count} items")

    # Logs
    log_dir = Path.home() / "Library" / "Logs" / "newsroom"
    log_size = sum(f.stat().st_size for f in log_dir.glob("*.log")) / 1024 / 1024 if log_dir.exists() else 0
    console.print(f"Logs: ~/Library/Logs/newsroom/ ({log_size:.1f} MB)")

    # Sources table
    table = Table(title="Sources")
    table.add_column("Name"); table.add_column("Last fetched")
    table.add_column("Errors"); table.add_column("Status")
    now = datetime.now()
    for src in state.list_enabled_sources():
        last = src["last_fetched_at"]
        age = "never"
        if last:
            try:
                delta = now.astimezone() - datetime.fromisoformat(last)
                age = f"{int(delta.total_seconds() / 60)} min ago"
            except ValueError:
                age = last
        errors = src["consecutive_errors"]
        status = "✓" if errors == 0 else "⚠"
        table.add_row(src["name"], age, str(errors), status)
    console.print(table)

    # Today's activity
    today = datetime.now().date().isoformat()
    fetched_today = state.connection().execute(
        "SELECT COUNT(*) FROM items WHERE DATE(fetched_at) = ?", (today,),
    ).fetchone()[0]
    scored_high = state.connection().execute(
        "SELECT COUNT(*) FROM items WHERE DATE(scored_at) = ? AND importance >= 4",
        (today,),
    ).fetchone()[0]
    notified = state.connection().execute(
        "SELECT COUNT(*) FROM items WHERE DATE(notified_at) = ?", (today,),
    ).fetchone()[0]
    console.print(f"\nToday: {fetched_today} fetched, {scored_high} scored ≥4, {notified} pushed")

    last_morning = state.get_digest(today, "morning")
    last_evening = state.get_digest(today, "evening")
    if last_morning:
        console.print(f"Morning digest: ✓ {last_morning['item_count']} items")
    if last_evening:
        console.print(f"Evening digest: ✓ {last_evening['item_count']} items")


def _read_keychain_oauth() -> dict | None:
    """Fetch the Claude Code OAuth record from macOS Keychain (read-only)."""
    import subprocess
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        return json.loads(raw).get("claudeAiOauth", {})
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return None
```

- [x] **Step 2: Test manually**

```bash
uv run python -m newsroom init
uv run python -m newsroom status
```

Expected: panel with auth status, DB info, sources table.

- [x] **Step 3: Commit**

```bash
git add src/newsroom/status.py src/newsroom/cli.py
git commit -m "feat(cli): status command with rich tables"
```

---

## Task 16: Logging Setup

**Goal:** Configure RichHandler for interactive output and JSONL events file for structured logging.

**Files:**
- Create: `src/newsroom/logging_setup.py`
- Modify: `src/newsroom/cli.py` (call `configure_logging` at entry)

- [x] **Step 1: Implement `src/newsroom/logging_setup.py`**

```python
"""Logging configuration — Rich for stdout, JSONL for structured events."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.logging import RichHandler


LOG_DIR = Path.home() / "Library" / "Logs" / "newsroom"
JSONL_PATH = LOG_DIR / "events.jsonl"


class JsonlFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    # Avoid duplicating handlers on repeat calls
    if root.handlers:
        return

    rich_handler = RichHandler(
        rich_tracebacks=True,
        show_path=False,
        show_time=True,
    )
    rich_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(rich_handler)

    jsonl_handler = RotatingFileHandler(
        JSONL_PATH, maxBytes=10 * 1024 * 1024, backupCount=4, encoding="utf-8",
    )
    jsonl_handler.setFormatter(JsonlFormatter())
    jsonl_handler.setLevel(logging.INFO)
    root.addHandler(jsonl_handler)
```

- [x] **Step 2: Call from CLI entry**

In `src/newsroom/cli.py`, add at top-level (not inside a command):

```python
from newsroom.logging_setup import configure_logging

@app.callback()
def _cli_entry(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    configure_logging(verbose=verbose)
```

- [x] **Step 3: Verify**

```bash
uv run python -m newsroom init
ls ~/Library/Logs/newsroom/
```

Expected: `events.jsonl` file exists.

- [x] **Step 4: Commit**

```bash
git add src/newsroom/logging_setup.py src/newsroom/cli.py
git commit -m "feat(logging): rich stdout + jsonl events"
```

---

## Task 17: Prompts + Production Source Config

**Goal:** Finalize all prompts (morning/evening, scoring, arxiv-filter already written in earlier tasks) and write the complete `config/sources.yaml` for Phase 1.

**Files:**
- Create: `config/sources.yaml`
- Review + polish: `config/prompts/*.md` (already exist)

- [ ] **Step 1: Write `config/sources.yaml`**

```yaml
sources:
  # Tier 1: arXiv (daily)
  - name: arxiv-cs-cl
    category: ai
    subcategory: arxiv
    url: https://arxiv.org/rss/cs.CL
    feed_type: rss
    interval_seconds: 86400
    enabled: true
  - name: arxiv-cs-lg
    category: ai
    subcategory: arxiv
    url: https://arxiv.org/rss/cs.LG
    feed_type: rss
    interval_seconds: 86400
    enabled: true
  - name: arxiv-cs-ai
    category: ai
    subcategory: arxiv
    url: https://arxiv.org/rss/cs.AI
    feed_type: rss
    interval_seconds: 86400
    enabled: true

  # Tier 1: Lab blogs
  - name: anthropic-news
    category: ai
    subcategory: lab
    url: https://www.anthropic.com/news/rss.xml
    feed_type: rss
    interval_seconds: 3600
    enabled: true
  - name: openai-blog
    category: ai
    subcategory: lab
    url: https://openai.com/news/rss.xml
    feed_type: rss
    interval_seconds: 3600
    enabled: true
  - name: deepmind-blog
    category: ai
    subcategory: lab
    url: https://deepmind.google/discover/blog/rss.xml
    feed_type: rss
    interval_seconds: 3600
    enabled: true
  - name: meta-ai-blog
    category: ai
    subcategory: lab
    url: https://ai.meta.com/blog/rss/
    feed_type: rss
    interval_seconds: 7200
    enabled: true
  - name: huggingface-blog
    category: ai
    subcategory: lab
    url: https://huggingface.co/blog/feed.xml
    feed_type: rss
    interval_seconds: 7200
    enabled: true

  # Tier 2: Curated
  - name: simon-willison
    category: ai
    subcategory: curated
    url: https://simonwillison.net/atom/everything/
    feed_type: atom
    interval_seconds: 3600
    enabled: true
  - name: sebastian-raschka
    category: ai
    subcategory: curated
    url: https://magazine.sebastianraschka.com/feed
    feed_type: rss
    interval_seconds: 21600
    enabled: true
  - name: import-ai
    category: ai
    subcategory: curated
    url: https://importai.substack.com/feed
    feed_type: rss
    interval_seconds: 21600
    enabled: true
  - name: latent-space
    category: ai
    subcategory: curated
    url: https://www.latent.space/feed
    feed_type: rss
    interval_seconds: 21600
    enabled: true
  - name: the-batch
    category: ai
    subcategory: curated
    url: https://www.deeplearning.ai/the-batch/feed/
    feed_type: rss
    interval_seconds: 43200
    enabled: true

  # Tier 3: Community
  - name: hackernews-ai
    category: ai
    subcategory: community
    url: http://hn.algolia.com/api/v1/search_by_date?query=AI+OR+LLM+OR+Claude&tags=story&numericFilters=points>100
    feed_type: json
    interval_seconds: 1800
    enabled: true
  - name: reddit-localllama
    category: ai
    subcategory: community
    url: https://www.reddit.com/r/LocalLLaMA/.rss
    feed_type: rss
    interval_seconds: 7200
    enabled: true

  # Claude Code
  - name: claude-code-releases
    category: ai
    subcategory: claude-code
    url: https://api.github.com/repos/anthropics/claude-code/releases
    feed_type: json
    interval_seconds: 43200
    enabled: true
```

- [ ] **Step 2: GitHub-releases JSON parser (quick extension)**

The existing `_parse_json_hn` only handles Algolia JSON. Add a GitHub-releases variant:

Edit `src/newsroom/fetcher.py` and change `parse_feed` JSON branch:

```python
def _parse_json_hn(raw: bytes) -> list[ParsedItem]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    # Algolia format: {"hits": [...]}
    if isinstance(data, dict) and "hits" in data:
        return _parse_json_hn_algolia(data)
    # GitHub releases: list of release objects
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
        items.append(ParsedItem(
            url=url, title=title, author=hit.get("author"),
            published_at=hit.get("created_at"),
            raw_summary=f"HN points: {hit.get('points', 0)}",
        ))
    return items


def _parse_json_github_releases(releases: list) -> list[ParsedItem]:
    items: list[ParsedItem] = []
    for rel in releases:
        url = rel.get("html_url")
        title = rel.get("name") or rel.get("tag_name")
        if not url or not title:
            continue
        items.append(ParsedItem(
            url=url, title=title, author=rel.get("author", {}).get("login"),
            published_at=rel.get("published_at"),
            raw_summary=(rel.get("body") or "")[:500],
        ))
    return items
```

- [ ] **Step 3: Validate**

```bash
uv run python -m newsroom validate-config
```

Expected: `✓ 16 sources loaded from config/sources.yaml`

- [ ] **Step 4: Commit**

```bash
git add config/sources.yaml src/newsroom/fetcher.py
git commit -m "feat: phase-1 source config + github-releases parser"
```

---

## Task 18: launchd Plists + Shell Wrappers + Install Scripts

**Goal:** Produce the three launchd plists, the `run.sh` wrapper, and the `install.sh`/`uninstall.sh` scripts.

**Files:**
- Create: `config/launchd/de.svenpoeche.newsroom.fetch.plist`
- Create: `config/launchd/de.svenpoeche.newsroom.digest-morning.plist`
- Create: `config/launchd/de.svenpoeche.newsroom.digest-evening.plist`
- Create: `scripts/run.sh`
- Create: `scripts/install.sh`
- Create: `scripts/uninstall.sh`

- [ ] **Step 1: Write `scripts/run.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Activate asdf if present so `uv` is in PATH
if [ -f "$HOME/.asdf/asdf.sh" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.asdf/asdf.sh"
fi

exec uv run python -m newsroom "$@"
```

```bash
chmod +x scripts/run.sh
```

- [ ] **Step 2: Write `config/launchd/de.svenpoeche.newsroom.fetch.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>de.svenpoeche.newsroom.fetch</string>
    <key>ProgramArguments</key>
    <array>
        <string>{{PROJECT_ROOT}}/scripts/run.sh</string>
        <string>fetch</string>
    </array>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>LANG</key><string>de_DE.UTF-8</string>
    </dict>
    <key>StandardOutPath</key><string>{{HOME}}/Library/Logs/newsroom/fetch.log</string>
    <key>StandardErrorPath</key><string>{{HOME}}/Library/Logs/newsroom/fetch.err.log</string>
    <key>WorkingDirectory</key><string>{{PROJECT_ROOT}}</string>
    <key>ThrottleInterval</key><integer>60</integer>
    <key>ProcessType</key><string>Background</string>
    <key>Nice</key><integer>10</integer>
</dict>
</plist>
```

- [ ] **Step 3: Write `config/launchd/de.svenpoeche.newsroom.digest-morning.plist`**

Copy the fetch plist and change:
- `Label` → `de.svenpoeche.newsroom.digest-morning`
- `ProgramArguments` second string → `digest`
- Replace `<key>StartInterval</key><integer>300</integer>` with:

```xml
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key><integer>7</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>
```

- Remove `<key>RunAtLoad</key><true/>` (or set to `<false/>`).
- Change log paths to `digest-morning.log` / `digest-morning.err.log`.

- [ ] **Step 4: Write `digest-evening.plist`**

Same as morning but `Hour` = `20`, label `digest-evening`, log files named accordingly.

- [ ] **Step 5: Write `scripts/install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/newsroom"
STATE_DIR="$HOME/Library/Application Support/daily-newsroom"

echo "Installing newsroom from $PROJECT_ROOT"

mkdir -p "$LAUNCH_DIR" "$LOG_DIR" "$STATE_DIR"

cd "$PROJECT_ROOT"

# 1. Initialize DB & validate config
uv run python -m newsroom init
uv run python -m newsroom validate-config

# 2. Deploy plists with variable substitution
for plist in config/launchd/*.plist; do
    name=$(basename "$plist")
    target="$LAUNCH_DIR/$name"

    # Unload old, if any
    launchctl unload "$target" 2>/dev/null || true

    sed \
      -e "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" \
      -e "s|{{HOME}}|$HOME|g" \
      "$plist" > "$target"

    launchctl load "$target"
    echo "  ✓ $name loaded"
done

echo
echo "Install complete. Run 'newsroom status' to verify."
echo "Check launchd entries: launchctl list | grep newsroom"
```

```bash
chmod +x scripts/install.sh
```

- [ ] **Step 6: Write `scripts/uninstall.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

LAUNCH_DIR="$HOME/Library/LaunchAgents"

for plist in "$LAUNCH_DIR"/de.svenpoeche.newsroom.*.plist; do
    [ -e "$plist" ] || continue
    name=$(basename "$plist")
    launchctl unload "$plist" 2>/dev/null || true
    rm "$plist"
    echo "  ✓ removed $name"
done

echo
echo "State DB at '$HOME/Library/Application Support/daily-newsroom/state.db' kept."
echo "Logs at '$HOME/Library/Logs/newsroom/' kept."
echo "Delete manually if desired."
```

```bash
chmod +x scripts/uninstall.sh
```

- [ ] **Step 7: Dry-run install (optional, actual install deferred)**

```bash
# Verify plists parse correctly
plutil config/launchd/*.plist
```

Expected: all output `OK`.

- [ ] **Step 8: Commit**

```bash
git add config/launchd scripts/run.sh scripts/install.sh scripts/uninstall.sh
git commit -m "feat: launchd plists + install/uninstall scripts"
```

---

## Task 19: Integration Tests + README + CLAUDE.md

**Goal:** End-to-end pipeline test, then documentation.

**Files:**
- Create: `tests/test_integration.py`
- Create: `README.md`
- Create: `CLAUDE.md`

- [ ] **Step 1: Write integration test**

Create `tests/test_integration.py`:

```python
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

from newsroom.config import Source
from newsroom.digester import generate_digest
from newsroom.fetcher import fetch_due_sources
from newsroom.filter_arxiv import filter_pending_arxiv_items
from newsroom.notifier import Notifier
from newsroom.scorer import score_pending_items
from newsroom.state import State


async def test_end_to_end_pipeline(
    tmp_path: Path, fixtures_dir: Path, httpx_mock: HTTPXMock,
) -> None:
    # 1. State + config
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(Source(
        name="anthropic", category="ai", subcategory="lab",
        url="https://www.anthropic.com/news/rss.xml", feed_type="rss",
        interval_seconds=3600, enabled=True,
    ))

    # 2. Mock HTTP response
    httpx_mock.add_response(
        url="https://www.anthropic.com/news/rss.xml",
        content=(fixtures_dir / "feed_rss_sample.xml").read_bytes(),
        headers={"ETag": 'W/"test"'},
    )

    # 3. Fetch
    fetch_results = await fetch_due_sources(state)
    assert fetch_results[0].items_inserted == 2

    # 4. Mock agent
    agent_mock = AsyncMock()
    agent_mock.ask.side_effect = [
        {"importance": 5, "reason": "major"},
        {"importance": 3, "reason": "ok"},
        "# Morning digest\n\n## Lab\n- Item 1\n- Item 2",
    ]

    # 5. Score
    notifier = Notifier(send_fn=AsyncMock())
    await score_pending_items(state, agent=agent_mock, notifier=notifier, limit=10)
    scored = state.connection().execute(
        "SELECT COUNT(*) FROM items WHERE status='scored'"
    ).fetchone()[0]
    assert scored == 2

    # 6. Generate digest
    await generate_digest(
        state=state, slot="morning", date=date(2026, 4, 19),
        output_root=tmp_path / "news", agent=agent_mock,
    )
    out = tmp_path / "news" / "2026" / "04" / "2026-04-19.md"
    assert out.exists()
    assert "Morning digest" in out.read_text()

    # 7. Verify digest marked in state
    assert state.get_digest("2026-04-19", "morning") is not None
```

- [ ] **Step 2: Run integration test**

```bash
uv run pytest tests/test_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Write `README.md`**

```markdown
# daily-newsroom

A local, macOS-native news-digest agent. Fetches AI/LLM/ML sources every 5 min,
scores for importance via Claude Haiku, pushes macOS notifications for breaking
news, and generates morning/evening markdown digests in `~/Documents/!AI/news/`
via Claude Opus.

**Status:** Phase 1 (AI/LLM/ML only). Phase 2 (Weltgeschehen, Dresden) and
Phase 3 (Tech, Science, APOD) planned.

## Requirements

- macOS (uses launchd, macOS Keychain, Notification Center)
- `asdf` with Python 3.12
- `uv`
- Installed Claude Code with Max 20x (or Team) subscription, logged in
- `terminal-notifier` for macOS notifications: `brew install terminal-notifier`

## Install

```bash
git clone <repo-url>
cd daily-newsroom
uv sync
./scripts/install.sh
```

Verify:

```bash
uv run python -m newsroom status
launchctl list | grep newsroom
```

## Usage

All production triggers are via launchd. For manual / debug runs:

```bash
uv run python -m newsroom fetch              # fetch due sources, score, maybe push
uv run python -m newsroom fetch --dry-run    # show what would be fetched
uv run python -m newsroom score              # score pending items (normally chained)
uv run python -m newsroom digest             # generate digest for current slot
uv run python -m newsroom digest --time morning --force  # force morning digest
uv run python -m newsroom notify --test      # test notification
uv run python -m newsroom status             # overview of system health
```

## Configuration

- `config/sources.yaml` — feed URLs, intervals, enabled flags
- `config/prompts/*.md` — LLM prompts (editable, hot-reloadable)
- `config/launchd/*.plist` — launchd schedules

After editing `sources.yaml`:

```bash
uv run python -m newsroom validate-config
uv run python -m newsroom init   # sync DB with new config
```

## Layout

See `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md` for the full design spec.

## Troubleshooting

- **No notifications appearing:** `brew install terminal-notifier`, then `newsroom notify --test`.
- **`claude` CLI not found:** update `PATH` in `config/launchd/*.plist` to match your Homebrew prefix.
- **Auth expired:** run `claude /login` once to refresh the OAuth token.
- **Logs:** `~/Library/Logs/newsroom/{fetch,digest-morning,digest-evening}.log`

## Uninstall

```bash
./scripts/uninstall.sh
```

State DB and logs are kept by default; delete manually if desired:

```bash
rm -rf ~/Library/Application\ Support/daily-newsroom
rm -rf ~/Library/Logs/newsroom
```

## Development

```bash
uv run pytest                    # fast tests
uv run pytest -m real_llm        # smoke tests against real API (manual)
uv run ruff check && uv run ruff format
uv run vulture src/
```
```

- [ ] **Step 4: Write `CLAUDE.md`**

```markdown
# daily-newsroom — Project Guidance for Claude

## Architecture Summary

"Dumb trigger, smart script." launchd fires three times (fetch every 5 min,
digest 07:00, digest 20:00); the `newsroom` CLI converges state from SQLite,
never persistent daemon.

Full design: `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`.

## Invariants — DO NOT break

- **Items flow through status:** `new → (filtered_in|filtered_out) → scored → notified? → included_in_digest`.
  Each status transition is a SQLite transaction.
- **All inter-module communication is via state DB**, never direct Python calls between
  fetcher/scorer/digester/notifier. Exception: `agent_client` is a library for LLM callers.
- **launchd-invoked commands are argumentless.** `newsroom fetch`, `newsroom digest` —
  the script determines due work itself. CLI flags are for debug only.
- **Idempotency is sacred.** Any subcommand must be safe to re-run; crashes mid-flight
  should not produce duplicates. Dedup via `item_hash`, idempotency via `digests` UNIQUE(date,slot).
- **Digest slot determination uses `Europe/Berlin` explicitly.** Never UTC for user-facing time.

## Common Gotchas

- `claude-agent-sdk` auth: it spawns `claude` CLI subprocess → Keychain OAuth.
  launchd plists MUST set PATH to include the `claude` binary location.
- The `pync` package is a macOS-only wrapper around `terminal-notifier`. In tests, inject
  `send_fn` into `Notifier(…)` to avoid hitting the real Notification Center.
- Always `import` `feedparser` inside functions — it has slow top-level imports that bloat
  cold-start time of every launchd trigger. (Actually, imported at module level in `fetcher.py`
  is fine — this note is a reminder if you see perf issues.)
- The cross-link lookup in `digester._resolve_cross_link` does a full-text scan of
  `!AI/article_summaries/`. If that directory grows to thousands of files, introduce a
  pre-built URL index.

## Development Conventions

- Python 3.12, `uv` for dependency management (never edit pyproject.toml by hand for deps).
- Ruff for format + lint (config in pyproject.toml).
- Tests: `pytest` with `freezegun`, `pytest-httpx`, async support. Fixtures in `tests/fixtures/`.
- TDD: write failing test, implement, pass, commit.
- Branch naming (if multi-branch work): ticket-key only (not used for this solo project).
- Coverage target: 70% in `src/newsroom/`.

## Phase Roadmap

- **Phase 1 (this):** AI/LLM/ML only. 15 sources + Claude Code releases. See
  `docs/superpowers/plans/2026-04-19-daily-newsroom-phase1.md`.
- **Phase 2:** Add Weltgeschehen + Dresden (incl. Dresden-Science: MPI-CBG, MPI-PKS, HZDR, TU Dresden).
- **Phase 3:** Tech, Wissenschaft (Physik/Chemie/Astro), APOD.

## Non-Goals — DO NOT add

- No cloud component, no hosting, no third-party services beyond LLM calls to Anthropic.
- No mobile app, no web UI (deferred).
- No auto-replies or auto-posts externally.
- No external telemetry.
- No paywall circumvention.

If any of these temptations arise, check the spec §10.3 Explicitly Never before writing code.
```

- [ ] **Step 5: Commit**

```bash
git add tests/test_integration.py README.md CLAUDE.md
git commit -m "docs: integration test + README + CLAUDE.md"
```

---

## Final Step: Run Full Test Suite + Manual Verification

- [ ] **Run full tests**

```bash
uv run pytest -v
uv run ruff check
uv run ruff format --check
uv run vulture src/ --min-confidence 80
```

Expected: all tests pass, ruff clean, vulture reports no dead code (or only known-safe things).

- [ ] **Manual verification checklist (see spec §9.2 DoD)**

```
[ ] uv run python -m newsroom init
[ ] uv run python -m newsroom validate-config
[ ] uv run python -m newsroom notify --test → notification appears
[ ] uv run python -m newsroom fetch --dry-run → expected sources
[ ] uv run python -m newsroom fetch → items fetched, scored
[ ] uv run python -m newsroom status → all green
[ ] ./scripts/install.sh
[ ] launchctl list | grep newsroom → 3 entries, status 0
[ ] Wait 5 min, check ~/Library/Logs/newsroom/fetch.log grew
[ ] uv run python -m newsroom digest --time morning --force → file in !AI/news/
[ ] Open digest in editor, verify format + cross-links
[ ] Let Mac sleep 30 min, wake up → fetch catches up
[ ] Next 07:00 → automatic digest appears in !AI/news/
```

- [ ] **7-day live test**

Leave the system running for one week. Daily check: `newsroom status` shows 0 errors, digests are generated, notifications feel appropriate.

Completion signals from §9.4 Success Metrics:

- Signal-to-noise ≥60% (informal)
- Digest readability: read both digests on ≥5 of 7 days
- Reliability: 0 missed digests, <5 error notifications

---

## Self-Review Notes

**Spec coverage:** Every section of the design spec (§2 through §10) maps to at least one task:

| Spec Section | Implementing Tasks |
|---|---|
| §2 Architecture | Tasks 1, 3, 18 |
| §3 Components | Tasks 1–16 |
| §4 Data Flow | Tasks 6–14 |
| §5 State (SQLite) | Tasks 3, 4 |
| §6 Scheduling | Task 18 |
| §7 Error Handling | Tasks 8, 10, 11, 13 (fallback) |
| §8 Testing | Every task + Task 19 |
| §9 DoD & Scope | Final Step (manual verification) |
| §10 Out-of-scope | Not implemented (documented in CLAUDE.md) |

**Placeholder scan:** No "TBD", "TODO", "implement later", "write tests for the above" markers. `--category` and `--source` filter flags in `fetch` are fully implemented via `fetch_due_sources(state, category=..., source=...)`.

**Type consistency:** Verified `Source` fields match across `config.py`, `state.py` DB schema, and tests. `ParsedItem.item_hash`, `State.insert_item(item_hash=...)` consistent. `Notifier.maybe_notify(row, state)` signature consistent across scorer and notifier.

**Module boundaries:** fetcher → state (writes items), scorer → state (reads new, writes scored) + notifier (via injected ref), digester → state (reads scored, writes digests). No cross-module Python calls outside these agreed paths.
