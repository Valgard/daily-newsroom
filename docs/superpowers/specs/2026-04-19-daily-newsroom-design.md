# Daily Newsroom — Design Spec

**Status:** Draft (awaiting user approval)
**Date:** 2026-04-19
**Author:** Sven Pöche (via brainstorming with Claude)
**Type:** Design Spec for a local, scheduled news-digest agent
**Project Root:** `/Users/valgard/Projects/private/daily-newsroom/`

---

## 1. Context & Goals

### 1.1 Problem

The user wants to stay informed about multiple topic streams — AI/LLM/ML research and releases, world news, Dresden-local news, computer tech, and science (physics, chemistry, astronomy, astrophysics) — without manually checking fifteen+ sources per day. The desired end state is:

- **During the day:** macOS push notifications when something important happens.
- **Twice daily:** a readable markdown digest (morning + evening) summarizing what's new.

The existing `~/Documents/!AI/` knowledge-management system (with `article_summaries/` and `Glossar/`) is the integration target — the news digest should fit into this structure, not create a parallel system.

### 1.2 Goals

- **Low-friction information intake:** zero manual source checking, push only when genuinely important, digest for overview.
- **Local-first:** no cloud components, no third-party hosting, no dependencies beyond the Anthropic API.
- **Respectful of attention:** aggressive filtering and LLM-based importance scoring prevents notification spam.
- **Integrates with existing workflow:** cross-links to `article_summaries/`, reuses the same editor/Obsidian patterns.
- **Phase-1 Vertical Slice:** AI/LLM/ML (including Claude Code sub-category) end-to-end before scaling to other categories.

### 1.3 Non-Goals

See §10 for the full out-of-scope list. Key non-goals:

- No web UI, no mobile app, no cloud component.
- No social-media monitoring of specific users.
- No auto-posting / auto-reply.
- No paywall circumvention.
- No telemetry to third parties.

---

## 2. Architecture Overview

The system follows a **"dumb trigger, smart script"** pattern: macOS `launchd` acts as a stateless clockwork, and a Python CLI script (`newsroom`) holds all logic. The agent converges state rather than processing events — if a trigger is missed (Mac asleep), the next trigger catches up by reading the state DB.

```
┌───────────────────────────────────────────────────────────────────┐
│                         macOS launchd                             │
│  ┌──────────────────┐  ┌────────────────┐  ┌────────────────┐     │
│  │ fetch (every 5m) │  │ digest (07:00) │  │ digest (20:00) │     │
│  └────────┬─────────┘  └───────┬────────┘  └───────┬────────┘     │
└───────────┼────────────────────┼────────────────────┼─────────────┘
            │                    │                    │
            ▼                    ▼                    ▼
┌───────────────────────────────────────────────────────────────────┐
│               newsroom  (Python CLI, uv-managed)                  │
│                                                                   │
│   ┌─────────┐   ┌─────────┐   ┌─────────┐   ┌─────────┐           │
│   │ fetcher │→  │ scorer  │→  │ digester│   │ notifier│           │
│   │(HTTP)   │   │(Haiku)  │   │ (Opus)  │   │(pync)   │           │
│   └────┬────┘   └────┬────┘   └────┬────┘   └────▲────┘           │
│        │             │             │              │               │
│        └─────────────┴─────────────┴──────────────┘               │
│                          ▲                                        │
│                          │                                        │
│                 ┌────────┴───────┐                                │
│                 │  state.db      │  sources.yaml                  │
│                 │  (SQLite)      │  (config)                      │
│                 └────────────────┘                                │
└───────────────────────────────────────────────────────────────────┘
            │                                    │
            ▼                                    ▼
┌───────────────────────────┐     ┌──────────────────────────┐
│ ~/Documents/!AI/news/     │     │ macOS Notification Center│
│   YYYY/MM/YYYY-MM-DD.md   │     │ (via pync/terminal-      │
│                           │     │  notifier)               │
└───────────────────────────┘     └──────────────────────────┘
```

### 2.1 Core Properties

- **Stateless per trigger:** each `launchd` invocation reads `state.db`, decides what to do, writes new state, exits.
- **No daemon:** no long-running process; relies on `launchd` OS guarantees.
- **Convergence over event-chain:** `fetch → score → digest` is derived from state ("what is due?", "what is unscored?"), not driven by events. Survives sleep, crash, downtime.
- **External boundaries:** (1) RSS/HTTP feeds inbound, (2) Claude API via subscription, (3) Filesystem (`!AI/news/`), (4) macOS Notification Center. Everything else is local.

### 2.2 Authentication

The Claude Agent SDK uses `~/.claude/` credentials indirectly: it spawns the `claude` CLI binary as a subprocess, and the CLI binary reads the macOS Keychain entry `Claude Code-credentials` (OAuth token with `subscriptionType: max`, `rateLimitTier: default_claude_max_20x`).

**Cost:** €0 additional (covered by existing Max 20x subscription).

**Caveats:**
- OAuth token has expiry (typically ~30 days); refresh happens automatically on `claude` CLI use. The agent detects expiry and raises a `[Newsroom · Fehler]` notification prompting re-login.
- `launchd` plists must set `PATH` explicitly to include the `claude` binary location (Homebrew-installed, typically `/opt/homebrew/bin`).

### 2.3 Two-Profile Escape Hatch

If the agent's load interferes with interactive Claude Code usage, the user can separate billing via `CLAUDE_CONFIG_DIR`:

```bash
CLAUDE_CONFIG_DIR=~/.claude-team claude /login   # log into Team Premium Seat
```

Then set `CLAUDE_CONFIG_DIR=/Users/valgard/.claude-team` in the launchd plist's `EnvironmentVariables`. The agent uses Team Premium Seat, interactive Claude Code stays on Max 20x. Both subscriptions are paid by the user's employer (Mayflower GmbH), so either routing is cost-neutral.

---

## 3. Components & Responsibilities

### 3.1 Project Structure

```
/Users/valgard/Projects/private/daily-newsroom/
├── pyproject.toml              # uv-managed
├── uv.lock
├── README.md
├── CLAUDE.md                   # project guidance for future Claude sessions
├── .python-version             # asdf-managed
├── docs/
│   └── superpowers/
│       ├── specs/
│       │   └── 2026-04-19-daily-newsroom-design.md   # THIS FILE
│       └── plans/
│           └── (generated by writing-plans skill)
├── config/
│   ├── sources.yaml            # feed definitions, per category
│   ├── prompts/
│   │   ├── score_item.md
│   │   ├── filter_arxiv.md
│   │   ├── digest_morning.md
│   │   └── digest_evening.md
│   └── launchd/
│       ├── de.svenpoeche.newsroom.fetch.plist
│       ├── de.svenpoeche.newsroom.digest-morning.plist
│       └── de.svenpoeche.newsroom.digest-evening.plist
├── src/
│   └── newsroom/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py
│       ├── fetcher.py
│       ├── scorer.py
│       ├── filter_arxiv.py
│       ├── digester.py
│       ├── notifier.py
│       ├── state.py
│       ├── config.py
│       └── agent_client.py
├── scripts/
│   ├── run.sh                  # launchd wrapper (asdf + uv bootstrap)
│   ├── install.sh
│   └── uninstall.sh
├── tests/
│   ├── fixtures/
│   ├── test_fetcher.py
│   ├── test_scorer.py
│   ├── test_digester.py
│   ├── test_notifier.py
│   ├── test_state.py
│   └── test_integration.py
└── .gitignore
```

### 3.2 Module Responsibilities

| Module | Responsibility |
|---|---|
| `cli.py` | Subcommand dispatch (`fetch`, `score`, `digest`, `notify`, `status`, `init`, `validate-config`), argument parsing, exit codes. Typer-based. |
| `fetcher.py` | RSS/HTTP parsing, conditional GET (ETag / If-Modified-Since), scheduling logic (`should_fetch`), item extraction. |
| `scorer.py` | Per-item Haiku call → importance 1–5 + short reason. |
| `filter_arxiv.py` | Haiku-based relevance filter for arXiv papers, applied before scoring. |
| `digester.py` | Opus call: assembles scored items, generates markdown digest, writes to `!AI/news/YYYY/MM/YYYY-MM-DD.md`. Handles cross-linking. |
| `notifier.py` | Push-threshold check (importance, time-of-day, dedup), macOS notification via pync. |
| `state.py` | SQLite connection factory, schema migrations, all DB queries, transaction handling. |
| `config.py` | YAML loader with Pydantic validation, path resolution. |
| `agent_client.py` | Thin wrapper around `claude-agent-sdk`: prompt loading from `config/prompts/`, retry logic, structured-output (JSON) parsing. |

### 3.3 Inter-Module Contract

Modules communicate **only via the state DB**, not via direct Python calls. Exception: `agent_client.py` is imported as a library by `scorer.py`, `filter_arxiv.py`, and `digester.py`.

```
fetcher       → writes items.status = 'new'
filter_arxiv  → updates items.arxiv_relevant
scorer        → writes items.importance, status = 'scored'
notifier      → writes items.notified_at
digester      → writes items.included_in_digest
```

### 3.4 Dependencies

**Runtime:**

| Package | Purpose |
|---|---|
| `claude-agent-sdk` | LLM calls via subscription |
| `feedparser` | RSS/Atom parsing (tolerant of malformed feeds) |
| `httpx` | HTTP client (async, ETag support) |
| `pync` | macOS notifications (wrapper around terminal-notifier) |
| `pydantic` | Config validation |
| `typer` | CLI framework |
| `rich` | Terminal output (tables, progress, colors) + `RichHandler` for logging |
| `python-dateutil` | Tolerant date parsing from feeds |
| `sqlite3` (stdlib) | State DB |

**Dev:**

| Package | Purpose |
|---|---|
| `ruff` | Formatter + linter |
| `vulture` | Dead-code detection |
| `pytest` | Test runner |
| `pytest-asyncio` | Async test support |
| `pytest-mock` | Mocking helpers |
| `freezegun` | Time mocking in tests |
| `pytest-httpx` | HTTP response mocking |

---

## 4. Data Flow

Items progress through a **status machine** in the state DB; each module operates only on items in a specific status.

```
new → (arxiv?) → filtered_in | filtered_out
new / filtered_in → scored → notified? → included_in_digest
```

### 4.1 Flow 1 — `newsroom fetch` (launchd, every 5 min)

1. `config.load_sources("config/sources.yaml")`
2. `state.connect()` → verify schema, auto-migrate if needed.
3. For each source:
    - `should_fetch(source)`: skip if `now - last_checked_at < interval_seconds`.
    - GET source URL with `If-Modified-Since` + `If-None-Match` headers:
        - **304 Not Modified** → update `last_checked_at`, skip.
        - **200 OK** → parse feed, extract items.
        - **4xx/5xx** → log, increment `consecutive_errors`.
    - For each item: compute `item_hash`, `INSERT ... ON CONFLICT DO NOTHING`.
    - Update source: `last_fetched_at`, `etag`, `last_modified`.
4. **Chain into scoring:** `scorer.score_pending_items(limit=100)` runs in the same process.
5. **Digest catchup check** (safety net for wake-from-sleep, see §6.5): if current time is within `07:00 ± 2h` or `20:00 ± 2h` (Europe/Berlin) and the corresponding digest for today is missing from the `digests` table, invoke `digester.generate_digest(slot)` in-process.
6. Exit 0.

### 4.2 Flow 2 — Scoring (in-process after fetch)

1. Fetch items `WHERE status IN ('new', 'filtered_in') LIMIT 100`.
2. For each item:
    - If `status == 'new'` and `subcategory == 'arxiv'`: `filter_arxiv.filter_one(item)`.
      - If `arxiv_relevant=False`: set `status='filtered_out'`, continue (skip scoring).
      - If `arxiv_relevant=True`: set `status='filtered_in'`, continue to scoring.
    - (Non-arxiv items proceed directly to scoring from `status='new'`.)
    - `agent_client.ask('score_item', vars={item})` → returns `{importance: 1-5, reason: "…"}`.
    - Parse JSON, write `items.importance`, `items.score_reason`, `status='scored'`.
    - `notifier.maybe_notify(item)`.
3. Commit transaction.

### 4.3 Flow 3 — Notification Decision

1. Compute threshold from current hour:
    - `07:00–21:59` → threshold = 4
    - `22:00–06:59` → threshold = 5 (quiet hours)
2. Apply category overrides:
    - `weltgeschehen-breaking`: threshold − 1 (Phase 2)
    - `arxiv`: threshold += 99 (never single-pushed, only digest)
3. If `item.importance < threshold` → return.
4. Dedup check: last notification for same category within 15 min?
    - Yes → bundled notification ("AI News: Claude 5 released + 2 more").
    - No → single notification.
5. `pync.notify(title, message, open_url=item.url)`.
6. Write `items.notified_at = now()`.

### 4.4 Flow 4 — `newsroom digest` (launchd, 07:00 + 20:00)

1. Determine slot from current hour **in `Europe/Berlin` local time** (not UTC — DST transitions handled explicitly via `zoneinfo.ZoneInfo("Europe/Berlin")`):
    - `05:00–11:59` → 'morning'
    - `16:00–23:59` → 'evening'
    - Otherwise → log, exit 0.
2. Idempotency check: `SELECT FROM digests WHERE date=today AND slot=slot`. If exists and not `--force`, exit 0.
3. Collect items: morning = scored since yesterday's evening; evening = scored since today's morning. Order by `importance DESC, published_at DESC`.
4. Cross-link: for each item, check if matching URL exists in `~/Documents/!AI/article_summaries/`. If yes, attach badge.
5. `agent_client.ask('digest_' + slot, vars={items})` → Opus generates markdown.
6. Compose path: `~/Documents/!AI/news/YYYY/MM/YYYY-MM-DD.md`.
7. Write file:
    - Morning: create file with header.
    - Evening: append `\n---\n## Abend-Digest\n\n` + body.
8. `UPDATE items SET included_in_digest = slot`.
9. `INSERT INTO digests (date, slot, file_path, item_count, model, …)`.
10. `pync.notify("Digest bereit: 12 Items", open=file_path)`.
11. Exit 0.

### 4.5 End-to-End Example

```
[23:30] newsroom fetch    → 8 new items → scoring → 2 importance=5, 3 importance=4, 3 importance=2
         Quiet hours (22+): only importance=5 pushes.
         pync.notify: "[AI] Anthropic Blog: Claude 5 released" ✓

[06:45] Mac wakes from sleep
[06:50] launchd fires fetch → 12 more items scored

[07:00] newsroom digest    → slot='morning', 20 items since yesterday 20:00
         Opus generates markdown → writes !AI/news/2026/04/2026-04-19.md
         pync.notify: "Morgen-Digest bereit (20 Items)" ✓

[09:30] newsroom fetch     → 3 new items, 1 importance=4
         Threshold 4 during daytime → push
         pync.notify: "[AI] Simon Willison: …" ✓

[20:00] newsroom digest    → slot='evening', 14 items since 07:00
         Appends to !AI/news/2026/04/2026-04-19.md
         pync.notify: "Abend-Digest bereit (14 Items)" ✓
```

### 4.6 Invariants

1. **Idempotent:** every subcommand can be invoked multiple times without producing duplicates. The 5-minute fetch trigger runs up to 288×/day; the vast majority are no-ops that exit in <1 s.
2. **Crash-safe:** each status transition is a DB transaction. Crash mid-scoring → items stay on `new`, reprocessed next trigger.
3. **Sleep-safe:** 3-hour sleep → launchd fires once on wake → all-due sources get processed in one pass. No events lost.
4. **Push timing:** best-effort, not realtime. An item appears as push at most 5 min after its RSS publication. Digests are strictly slot-based.

---

## 5. State Management (SQLite)

### 5.1 Location & Mode

- File: `~/Library/Application Support/daily-newsroom/state.db`
- Journal mode: WAL (for concurrent reads).
- Expected size: <10 MB after months of operation (with 90-day item retention).

### 5.2 Schema

```sql
CREATE TABLE schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT UNIQUE NOT NULL,
    category            TEXT NOT NULL,
    subcategory         TEXT,
    url                 TEXT NOT NULL,
    feed_type           TEXT NOT NULL,   -- 'rss' | 'atom' | 'json' | 'html-scrape'
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
);

CREATE TABLE items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    item_hash           TEXT UNIQUE NOT NULL,  -- sha256(url + "\n" + title)[:16]
    url                 TEXT NOT NULL,
    title               TEXT NOT NULL,
    author              TEXT,
    published_at        TEXT,
    raw_summary         TEXT,
    category            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'new',
        -- 'new' | 'filtered_in' | 'filtered_out' | 'scored' | 'archived'
    importance          INTEGER,
    score_reason        TEXT,
    score_model         TEXT,
    scored_at           TEXT,
    arxiv_relevant      INTEGER,
    notified_at         TEXT,
    included_in_digest  TEXT,
    fetched_at          TEXT NOT NULL DEFAULT (datetime('now')),
    cross_link_summary  TEXT
);

CREATE TABLE digests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    slot          TEXT NOT NULL,         -- 'morning' | 'evening'
    file_path     TEXT NOT NULL,
    item_count    INTEGER NOT NULL,
    model         TEXT NOT NULL,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(date, slot)
);

CREATE INDEX idx_items_status ON items(status) WHERE status IN ('new', 'filtered_in');
CREATE INDEX idx_items_source ON items(source_id);
CREATE INDEX idx_items_published ON items(published_at DESC);
CREATE INDEX idx_items_digest_pending ON items(status, importance)
    WHERE status = 'scored' AND included_in_digest IS NULL;
CREATE INDEX idx_sources_enabled ON sources(enabled, last_checked_at) WHERE enabled = 1;
```

### 5.3 Dedup Strategy

- **Item dedup key:** `sha256(url + "\n" + title)[:16]` (16 hex chars = 64 bits, sufficient for <1M items).
- Rationale for URL+title (not URL alone): HN/Reddit has multiple submissions of the same URL (different submitters — keep both); arXiv re-publishes same paper across categories (same title → dedup).
- Query: `INSERT … ON CONFLICT(item_hash) DO NOTHING`.

### 5.4 Conditional HTTP

```python
headers = {}
if source.etag:
    headers["If-None-Match"] = source.etag
if source.last_modified:
    headers["If-Modified-Since"] = source.last_modified

response = await httpx.get(source.url, headers=headers)

if response.status_code == 304:
    state.update_source(source.id, last_checked_at=now())
    return []
elif response.status_code == 200:
    items = parse_feed(response.content)
    state.update_source(
        source.id,
        last_checked_at=now(), last_fetched_at=now(),
        etag=response.headers.get("ETag"),
        last_modified=response.headers.get("Last-Modified"),
    )
    return items
```

### 5.5 Source-Level Error Handling

```
HTTP 429       → disable_until = now() + Retry-After or 1h
HTTP 5xx       → consecutive_errors++
HTTP 4xx       → consecutive_errors++ (410 ⇒ immediate disable)
Timeout        → consecutive_errors++
Parse error    → log raw, consecutive_errors++

≥10 consecutive_errors  → enabled=0, user notification "Source X disabled"
Successful fetch        → consecutive_errors=0, last_error=null
```

### 5.6 Retention

- **Items:** 90 days. Afterwards: `UPDATE items SET status='archived', raw_summary=NULL` (keep metadata for stats/recall, drop body).
- **Digests:** forever (files also in `!AI/news/`).
- **Sources:** forever.
- Cleanup runs once daily on the first fetch after midnight.

### 5.7 Schema Migrations

Minimal in-house system (no Alembic — overkill for one file):

```python
MIGRATIONS = {
    1: ["CREATE TABLE schema_version …", "CREATE TABLE sources …", …],
    2: ["ALTER TABLE items ADD COLUMN cross_link_summary TEXT"],
}

def ensure_schema(conn):
    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    for v in sorted(MIGRATIONS):
        if v > current:
            for stmt in MIGRATIONS[v]:
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
            conn.commit()
```

---

## 6. Scheduling

Two scheduling levels interact:

1. **External (launchd):** dumb tick — three `.plist` files.
2. **Internal (`fetcher.should_fetch`):** per-source frequency logic based on YAML config + DB state.

### 6.1 launchd Plists

**`de.svenpoeche.newsroom.fetch.plist`** (main ticker, every 5 min):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>de.svenpoeche.newsroom.fetch</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/valgard/Projects/private/daily-newsroom/scripts/run.sh</string>
        <string>fetch</string>
    </array>
    <key>StartInterval</key><integer>300</integer>
    <key>RunAtLoad</key><true/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>LANG</key><string>de_DE.UTF-8</string>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/valgard/Library/Logs/newsroom/fetch.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/valgard/Library/Logs/newsroom/fetch.err.log</string>
    <key>WorkingDirectory</key>
    <string>/Users/valgard/Projects/private/daily-newsroom</string>
    <key>ThrottleInterval</key><integer>60</integer>
    <key>ProcessType</key><string>Background</string>
    <key>Nice</key><integer>10</integer>
</dict>
</plist>
```

**`de.svenpoeche.newsroom.digest-morning.plist`** and **`digest-evening.plist`** are analogous but use `StartCalendarInterval` at 07:00 / 20:00 and pass `digest` as the subcommand. `RunAtLoad` is `false` for digests.

### 6.2 `scripts/run.sh` (launchd wrapper)

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

[ -f "$HOME/.asdf/asdf.sh" ] && . "$HOME/.asdf/asdf.sh"

exec uv run python -m newsroom "$@"
```

Rationale: launchd doesn't load user shell profiles; asdf needs stateful loading; a single wrapper for all three plists keeps the setup DRY.

### 6.3 Internal Frequency Logic

```python
def should_fetch(source: Source, now: datetime) -> bool:
    if not source.enabled:
        return False
    if source.disabled_until and now < source.disabled_until:
        return False
    if source.last_checked_at is None:
        return True
    elapsed = (now - source.last_checked_at).total_seconds()
    return elapsed >= source.interval_seconds
```

### 6.4 Jitter

To avoid all hourly sources firing at the same minute, each source gets a pseudo-random offset:

```python
offset_seconds = hash(source.name) % source.interval_seconds
```

This desynchronizes fetches across the interval.

### 6.5 Wake-from-Sleep

- `StartInterval` (fetch): launchd fires **once** after wake; the fetch run then processes all due sources in one pass.
- `StartCalendarInterval` (digest): launchd does **not** automatically catch up a missed scheduled time.

**Safety net:** the `fetch` handler also runs a digest-catchup check. If the current time is within `07:00 ± 2h` or `20:00 ± 2h` and the corresponding digest for today is missing, the digest is generated in-process.

### 6.6 `scripts/install.sh` (sketch)

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/newsroom"

mkdir -p "$LAUNCH_DIR" "$LOG_DIR"
mkdir -p "$HOME/Library/Application Support/daily-newsroom"

uv run python -m newsroom init
uv run python -m newsroom validate-config

for plist in config/launchd/*.plist; do
    name=$(basename "$plist")
    target="$LAUNCH_DIR/$name"
    launchctl unload "$target" 2>/dev/null || true
    sed "s|{{PROJECT_ROOT}}|$PROJECT_ROOT|g" "$plist" > "$target"
    launchctl load "$target"
    echo "  ✓ $name loaded"
done
```

---

## 7. Error Handling & Resilience

### 7.1 Error Categories

- **Transient** (network, HTTP 5xx, rate limits) → retry on next trigger, silent logging.
- **Persistent** (auth expired, dead source, disk full, broken config) → user notification + graceful degradation.

### 7.2 Error Catalogue

| Error | Action | User-visible? |
|---|---|---|
| Network offline | skip all fetches, exit 0 | No |
| HTTP 429 (source) | disable_until = now + Retry-After or 1h | No |
| HTTP 5xx | consecutive_errors++, retry next tick | After 10 errors |
| HTTP 4xx | consecutive_errors++ (410 ⇒ immediate disable) | After 3× 410 |
| Parse error (malformed XML) | log raw, consecutive_errors++ | After 5× |
| Claude API rate limit | pause scoring, items stay on `new` | No |
| OAuth token expired | state flag + notification | **Yes — relogin prompt** |
| `claude` CLI missing | PATH check at startup, exit 1 | **Yes** |
| Disk full / write fail | notification, exit 1 | **Yes** |
| Digest-file write fail | rollback digests insert, notification | **Yes** |
| Opus call fails (digest) | fallback digest (raw item list), notification | **Yes** |
| Crash mid-transaction | WAL auto-recovery on next open | No |
| Clock skew / DST | use local time `Europe/Berlin` explicitly | No |

### 7.3 Retry Strategy

**Exponential backoff for LLM calls only** (via `agent_client.py`):

```python
@retry(
    retry=retry_if_exception_type(anthropic.RateLimitError),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(3),
    reraise=True
)
```

**No inline retry for source fetches.** Rationale: "If Heise is down now, three attempts in 30s is no better than one attempt in 5 min at the next launchd tick." Prevents self-inflicted DDoS.

### 7.4 Notification Severity

```
[Newsroom · Info]     →  grey, no sound
[Newsroom · Warnung]  →  yellow, default sound
[Newsroom · Fehler]   →  red, critical sound, sticky until dismissed
```

The `Newsroom ·` prefix enables per-kanal filtering in macOS notification settings.

### 7.5 Graceful-Degradation Digest

If all retries of the Opus digest call fail, write a "raw" fallback digest:

```markdown
# News-Digest 2026-04-19 (Abend)

⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)

## AI / LLM / ML

- **[Importance 5]** [Claude 5 released](https://anthropic.com/...) · Anthropic · 19:42
- **[Importance 4]** [Sebastian Raschka: LLM Scaling Update](...) · 18:30
…

_Generiert ohne LLM-Synthese. Bei Bedarf Kommando `newsroom digest --force` manuell erneut ausführen._
```

### 7.6 Health Check — `newsroom status`

```
┌─────────────────────────────────────────────────────────────┐
│ daily-newsroom · Status                                     │
├─────────────────────────────────────────────────────────────┤
│ Auth:      ✓ Max 20x (expires 2026-05-18, 29 Tage)          │
│ State-DB:  ✓ 4.2 MB, 12.843 items (90d retention)           │
│ Logs:      ✓ ~/Library/Logs/newsroom/ (14 MB)               │
│                                                             │
│ Sources (15 aktiv, 0 disabled):                             │
│   ✓ arxiv-cs-cl         letzter fetch vor 3h, 0 errors      │
│   ✓ anthropic-news      letzter fetch vor 42min, 0 errors   │
│   ⚠ r/LocalLLaMA        3 consecutive errors (429)          │
│   …                                                         │
│                                                             │
│ Heute: 47 items gefetcht, 12 gescort ≥4, 3 notifications    │
│ Letzter Digest: morning (07:02, 31 Items)                   │
└─────────────────────────────────────────────────────────────┘
```

### 7.7 Logging

- `stdout`: `rich.logging.RichHandler` (human-readable).
- `fetch.log`, `fetch.err.log`: launchd-captured streams (plain).
- `events.jsonl`: one structured event per line, weekly rotation, 4-week retention. Enables ad-hoc `grep`/`jq` queries.

---

## 8. Testing & Verification

### 8.1 Test Pyramid

```
                                 ▲
                                / \
                               /   \
                              /     \   Smoke (5–10)
                             /       \  real LLM, on-demand
                            /─────────\
                           /           \
                          / Integration \ (~10–15)
                         /   (mock SDK)  \
                        /─────────────────\
                       /                   \
                      /     Unit tests      \ (~50–80)
                     /  (fast, isolated)     \
                    ▼─────────────────────────▼
```

### 8.2 Unit Tests

Fixtures in `tests/fixtures/`: anonymized RSS samples per source, `sources.fixture.yaml`, sample items.

Coverage target: **≥70 %** in `src/newsroom/`.

### 8.3 Integration Tests

End-to-end pipeline with `pytest-httpx` (mock RSS) and a mock `agent_client` (sequence-of-responses pattern). Asserts file output and model selection.

### 8.4 Smoke Tests (real LLM)

Tagged with `@pytest.mark.real_llm`, skipped by default. Run on demand with `pytest -m real_llm`. Purpose: catch prompt regression after prompt edits.

### 8.5 Dry-Run Modes

Every subcommand supports `--dry-run`:
- `newsroom fetch --dry-run` — show which sources would be fetched, no HTTP.
- `newsroom score --dry-run` — show pending items, no LLM call.
- `newsroom digest --dry-run` — show which items would be included, no write.
- `newsroom notify --test` — send a test notification.

### 8.6 Manual Verification Checklist (after install)

```
[ ] newsroom init runs without error
[ ] newsroom validate-config accepts sources.yaml
[ ] newsroom notify --test shows up in Notification Center
[ ] newsroom fetch --dry-run lists expected sources
[ ] newsroom fetch (real) fetches + scores + pushes (if breaking present)
[ ] newsroom status shows green on Auth, Sources, DB
[ ] launchctl list | grep newsroom shows 3 entries, status 0
[ ] ~/Library/Logs/newsroom/fetch.log grows over 5 min
[ ] newsroom digest --time morning --force writes file in !AI/news/
[ ] File readable in editor, cross-links functional
[ ] Mac sleep 30min → wake: missed fetches caught up
[ ] Next morning 07:00: automatic digest appears in !AI/news/
```

### 8.7 No CI

- Solo-use tool, single Mac.
- macOS-specific features (pync, launchd, Keychain) untestable on Linux runners.
- Maintenance cost > value.

Optional local pre-commit hook runs `ruff`, `ruff-format --check`, `pytest`.

---

## 9. Phase 1 Scope & Definition-of-Done

### 9.1 Phase 1 Content: AI/LLM/ML (incl. Claude Code)

**Sources (15 total):**

| Source | URL | Interval | Subcategory |
|---|---|---|---|
| arxiv-cs-cl | `https://arxiv.org/rss/cs.CL` | 24h | arxiv |
| arxiv-cs-lg | `https://arxiv.org/rss/cs.LG` | 24h | arxiv |
| arxiv-cs-ai | `https://arxiv.org/rss/cs.AI` | 24h | arxiv |
| anthropic-news | `https://www.anthropic.com/news/rss.xml` | 1h | lab |
| openai-blog | `https://openai.com/news/rss.xml` (fallback: scrape) | 1h | lab |
| deepmind | `https://deepmind.google/discover/blog/rss.xml` | 1h | lab |
| meta-ai | `https://ai.meta.com/blog/rss/` | 2h | lab |
| huggingface | `https://huggingface.co/blog/feed.xml` | 2h | lab |
| simon-willison | `https://simonwillison.net/atom/everything/` | 1h | curated |
| sebastian-raschka | `https://magazine.sebastianraschka.com/feed` | 6h | curated |
| import-ai | `https://importai.substack.com/feed` | 6h | curated |
| latent-space | `https://www.latent.space/feed` | 6h | curated |
| the-batch | `https://www.deeplearning.ai/the-batch/feed/` | 12h | curated |
| hackernews-ai | `http://hn.algolia.com/api/v1/search_by_date?query=AI+OR+LLM+OR+Claude&tags=story&numericFilters=points>100` | 30min | community |
| reddit-localllama | `https://www.reddit.com/r/LocalLLaMA/.rss` | 2h | community |
| claude-code-releases | `https://api.github.com/repos/anthropics/claude-code/releases` | 12h | claude-code |

**LLM prompts** (editable in `config/prompts/`):

- `score_item.md` — Haiku importance scoring (1–5 + short reason, JSON output).
- `filter_arxiv.md` — Haiku arXiv relevance filter. User themes: Agents, Memory, Inference, Long-Context, RLHF/RLAIF, Tool-Use, Alignment.
- `digest_morning.md` — Opus digest generator (focus: what happened overnight).
- `digest_evening.md` — Opus digest generator (focus: day summary).

### 9.2 Definition of Done

Phase 1 is complete when **all** apply:

**Functional:**
- [ ] `newsroom fetch`, `score`, `digest`, `notify`, `status`, `init`, `validate-config` exist with `--help`.
- [ ] All 15 Phase-1 sources fetched successfully at least once (green in `newsroom status`).
- [ ] Importance scoring: every fetched item scored within its fetch run.
- [ ] arXiv filter: items with `arxiv_relevant=false` skip scoring.
- [ ] Morning digest generated automatically at 07:00, in `!AI/news/YYYY/MM/YYYY-MM-DD.md`.
- [ ] Evening digest appended at 20:00.
- [ ] macOS push notifications fire for importance ≥4 (≥5 during quiet hours 22:00–07:00).
- [ ] Clicking a push opens the original URL.
- [ ] Cross-link: items with existing `article_summaries/` match are badged in the digest.

**Non-functional:**
- [ ] `pytest` green, ≥70 % coverage in `src/newsroom/`.
- [ ] `ruff check` + `ruff format --check` green.
- [ ] `vulture src/` reports no dead code.
- [ ] Typer CLI has autocompletion.
- [ ] 7-day continuous run with 0 manual intervention, `newsroom status` shows 0 errors at end, digests daily complete.

**Documentation:**
- [ ] `README.md` covers install, usage, troubleshooting.
- [ ] `CLAUDE.md` documents conventions.
- [ ] This spec committed at `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`.
- [ ] Implementation plan in `docs/superpowers/plans/…` (generated by writing-plans skill).

**Operability:**
- [ ] `scripts/install.sh` works on a fresh home directory.
- [ ] `scripts/uninstall.sh` cleans up.
- [ ] State DB and logs survive reboot.

### 9.3 Timeline

Realistic: 2–3 weeks part-time (~2h/day) or ~1 week full-time.

| Period | Focus |
|---|---|
| Week 1, Days 1–2 | Skeleton, uv setup, CLI structure, state DB + migrations + unit tests |
| Week 1, Days 3–4 | Fetcher with conditional GET, per-feed-format parser, fixture tests |
| Week 1, Day 5 | Agent-client wrapper (prompt loading, retry, JSON extract) |
| Week 1, Days 6–7 | Scorer + arXiv filter with real-LLM smoke tests |
| Week 2, Days 1–2 | Digester (Opus prompt iteration = largest time sink), cross-linking, markdown writing |
| Week 2, Day 3 | Notifier + pync, dedup/quiet-hours |
| Week 2, Day 4 | launchd plists, install script, wrapper shell |
| Week 2, Day 5 | Integration tests, dry-run modes, `newsroom status` |
| Week 2, Days 6–7 + Week 3 | 7-day live test, bugfixes, prompt tuning |

### 9.4 Success Metrics

After the 7-day live test:

1. **Push signal-to-noise:** target ≥60 % of pushes judged worthwhile. Measured informally.
2. **Digest readability:** target — user actually reads both digests on ≥5 of 7 days.
3. **System reliability:** 0 missed digests, <5 error notifications over 7 days.

All three green → proceed to Phase 2 (Weltgeschehen + Dresden + Science-Local).
Signal-to-noise <60 % → prompt and threshold tuning before Phase 2.
Digest readability low → format revision (likely too many items → more aggressive filtering).

---

## 10. Out-of-Scope

### 10.1 Deferred to Phase 2/3

- **Phase 2:** categories *Weltgeschehen*, *Dresden* (incl. Wissenschaft-Lokal: MPI-CBG, MPI-PKS, HZDR, TU Dresden Exzellenzcluster).
- **Phase 2:** dedicated *Weltgeschehen-Breaking* push path with lower threshold (importance ≥3).
- **Phase 3:** categories *Computer/Tech*, *Wissenschaft* (physics/chemistry/astro/astrophysics).
- **Phase 3:** APOD as daily-ritual sub-category in morning digest.
- **Phase 3:** webhook-based breaking news (RSSHub or similar) if pure polling is insufficient.

### 10.2 Backlog (no plans, but possible)

- Web UI / local app (browse digest, mark-as-read, find-similar).
- iOS access (Syncthing/iCloud sync of `!AI/news/`, or separate ntfy mobile target).
- RSS feed output (publish digest as RSS for external readers).
- Multi-modal (TTS audio digest, screenshot extraction from Twitter screenshots).
- Auto-trigger of `/summarize-article` on importance-5 items.
- Notification feedback loop (`newsroom feedback [id] good|bad`) for implicit prompt tuning.
- Statistics dashboard (topic trend detection).
- Shared digests (weekly team summary).
- Twitter/X integration (unrealistic given API costs).
- YouTube channels (transcript scrape from AI YouTubers).

### 10.3 Explicitly Never

- **Cloud component** — no server, no hosting, no third-party services beyond LLM calls to Anthropic.
- **Surveillance-style monitoring** of specific individuals (e.g., targeted scraping of a single Twitter user).
- **Auto-posts / auto-replies** — the agent reads and summarizes; it never writes externally.
- **External telemetry** — no analytics, no "anonymous" metrics, no crash reports off-device.
- **Sponsor/ad slots in the digest** — sponsored content from sources is filtered or visibly marked, never transparently relayed.
- **Paywall circumvention** — paywalled headlines and teasers are reported as-is; the agent does not bypass paywalls. (Manual `/summarize-article` workflows with Medium fallbacks remain unaffected.)

---

## 11. Open Questions / Future Decisions

- **Prompt tuning loop:** should prompt edits be versioned alongside code, or tracked in a separate log with A/B results? Decision deferred until after Phase 1, when we have real data on prompt performance.
- **Secondary notification target:** if the user wants mobile access later, decide between Syncthing-based file sync, ntfy.sh, or Pushover. Evaluation in Phase 2/3 if desire arises.
- **Team-Seat switch:** if Max 20x rate limits noticeably impact interactive Claude Code use during heavy agent activity, migrate agent to `CLAUDE_CONFIG_DIR=~/.claude-team` (Team Premium Seat). Trivial config change.

---

## 12. References

- Claude Agent SDK: <https://docs.claude.com/en/docs/agent-sdk>
- launchd Plist Format: <https://www.launchd.info/>
- feedparser: <https://feedparser.readthedocs.io/>
- pync: <https://github.com/SeTeM/pync>
- Existing knowledge-management target: `~/Documents/!AI/article_summaries/`, `~/Documents/!AI/Glossar/`
- Reference example of `claude-agent-sdk` use on user's machine: `/Users/valgard/Projects/private/claude/sagekit-split/filter.py`
