# daily-newsroom — Project Guidance for Claude

## Architecture Summary

"Dumb trigger, smart script." launchd fires three times (fetch every 5 min,
digest 07:00, digest 20:00); the `newsroom` CLI converges state from SQLite,
never persistent daemon.

Full design: `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`.

Digest rendering is deterministic: the LLM returns typed JSON item content and
Python renders all markdown structure. Key ADR:
`docs/adrs/0001-deterministic-digest-rendering.md`.

Digest output is split by top-level category: `generate_digest` returns
`list[Path]`, one file per non-empty category (`{date}_{category}.md`). The H1
carries the category label (`# News-Digest <date> — <Label> (Morgen|Abend)`).
Item headlines are H2; there are no `## category` section headers inside a file.
One notification fires per non-empty category file. An empty slot produces no
file. `digests.file_path` stores a JSON array; one row per `(date, slot)` — no
schema change.

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
- `feedparser` is imported at module level in `fetcher.py` — that is fine. If launchd
  cold-start time ever becomes a problem, consider importing it lazily inside functions instead.
- The cross-link lookup in `digester._resolve_cross_link` does a full-text scan of
  `!AI/article_summaries/`. If that directory grows to thousands of files, introduce a
  pre-built URL index.
- `newsroom status` column "Last fetched" is `sources.last_fetched_at`, which only
  advances when the feed actually delivers **new items**. A quiet blog can read as
  "28h ago" while being probed hourly — the real probe cadence lives in
  `sources.last_checked_at`. Don't diagnose "fetcher stuck" from the status table
  alone; cross-check with `last_checked_at` via SQL or the error column.
- **Score-prompt routing is per-category.** `scorer.py` builds the prompt
  name as `f"score_item_{source_category}"`. A new category (e.g. `dresden`,
  `tech`) MUST come with a matching `config/prompts/score_item_<category>.md`
  file or the agent_client will raise `KeyError` / `FileNotFoundError` on
  every item from that category. Side effect: items with `category` typos
  (e.g. `worldd`) silently fail with a log warning, not a hard error.
- **Digest rendering is deterministic (Python, not the LLM).** `generate_digest`
  calls the LLM with `parse="json"`; it returns only per-item content
  `{"items": [{"id", "headline", "prose", "quote?"}]}`. Python (`_render_digest`
  / `_render_item`) renders ALL structure: `##` item headlines, the
  `- [ ] interessiert mich` checkbox, the `[Weiterlesen →] · *source · time ·
  Importance N*` meta line, and the H1 (which includes the category label). The
  LLM never emits markdown structure — a format change is a code/test edit, not
  prompt tuning. There are NO `## category` section headers inside a digest file
  (the file itself is scoped to one category). See
  `docs/adrs/0001-deterministic-digest-rendering.md`.
- **One digest file per top-level category per slot.** `generate_digest` returns
  `list[Path]` — one `{date}_{category}.md` per non-empty category. An empty
  category produces no file and no notification. `digests.file_path` is a JSON
  array (one row per `(date, slot)`, no schema change). One `notify_digest_ready`
  call fires per non-empty category file.
- **The `---` morning/evening separator is owned by `_write_digest_file`** — not
  the renderer or the prompt. `_render_digest` must never emit `---`; adding one
  in the prompt or renderer creates a double-separator bug.
- **Missing/unknown LLM item ids degrade gracefully — and say so.** An item absent
  from the JSON response renders from the DB row (`headline` = `title`, `prose` =
  markup-stripped `raw_summary`, truncated to 1200 chars *after* stripping), so no
  item is ever dropped. Degradation is always visible to the reader, in one of two
  forms, and the two are mutually exclusive per file:
  - **Some items missing** → each affected item carries `⚠️ ohne
    LLM-Zusammenfassung` in its meta line. No banner: it would claim the whole
    file is degraded.
  - **No content for any item in the file** → `BANNER_NO_CONTENT` at the top, and
    no per-item markers.

  **The verdict is per category, not per run.** Each category is its own file, so a
  run that is globally partial can hold one healthy file and one entirely without
  content. Compute it over `cat_items` inside the category loop — never over
  `items`.

  Three distinct banners, three distinct causes — keep them apart, a wrong one
  sends every later diagnosis down the wrong path: `BANNER_UNREACHABLE` (the call
  failed), `BANNER_UNPARSEABLE` (answer arrived, would not parse),
  `BANNER_NO_CONTENT` (answer parsed, held no item content).

  **`contents_by_id` holds only what is renderable, not everything that arrived.**
  `_usable_contents` drops entries without a numeric id, without a non-blank
  headline, or without any body text (`prose` or `quote`). Membership decides
  "healthy", so present-but-unusable would render an empty `##`, discard the DB
  fallback, and — with `headline` missing entirely — raise `KeyError` in
  `_render_item` *after* the slot is claimed, losing it until someone runs
  `--force`. Never widen this back to a plain `"id" in c` test.

  `_plain_text` does three things, each load-bearing:
  - Strips tags **before** unescaping, so `&lt;p&gt;` that an author escaped on
    purpose survives as text instead of being read as a tag and dropped.
  - Requires a letter, `/`, `!` or `?` after the `<` (`_HTML_TAG_RE`), so prose
    like "gilt wenn a < b" keeps its text instead of losing everything up to the
    next `>`.
  - Escapes a leading markdown sigil (`#`, `>`, `-`, `+`, `*`, `|`, `1.`). Tags
    used to shield first position; stripping them exposes it, and a paragraph
    starting "# 1 Grund" would open a heading mid-file.

  It is not a sanitiser — nothing here is rendered in a browser. The prompt body in
  `format_items_for_prompt` is deliberately **not** stripped; markup is ~41% of a
  `raw_summary` at the median, so doing it there would change what the model sees
  on every healthy run and belongs in its own change.

## Known Architecture Deferrals

Pragmatic Phase-1 compromises that violate a stated invariant. Safe today, owed a
refactor before Phase-2 scope grows.

- **Digest catchup calls `generate_digest()` directly from the fetch path**
  (`cli.py::_maybe_run_digest_catchup`). This crosses the
  "fetcher/scorer/digester/notifier communicate only via state DB" invariant.
  Race-safe since the `claim_digest_slot` fix (commit `b1b84a4`), but still a
  direct Python call between modules. Phase-2 refactor: fetcher writes a
  `missed_slot` marker to state; a dedicated `newsroom digest-catchup` command
  (or the regular `digest` command itself) consumes markers.

- **Digester calls `Notifier.notify_digest_ready()` directly after slot finalization**
  (`digester.py::generate_digest`, optional injected `notifier` param). Same
  invariant violation class as the catchup deferral above. Implements spec
  §4.4 step 10 — a notification is fired once per finalized non-empty digest.
  Race-safe via the existing `claim_digest_slot` mechanism (only the slot
  winner reaches the notify call). Phase-2 refactor: replace the direct call
  with a `digest_events` table that a dedicated notifier process consumes;
  fold this together with the catchup-deferral refactor into one change.

## Development Conventions

- Python 3.12, `uv` for dependency management (never edit pyproject.toml by hand for deps).
- Ruff for format + lint (config in pyproject.toml).
- Tests: `pytest` with `freezegun`, `pytest-httpx`, async support. Fixtures in `tests/fixtures/`.
- TDD: write failing test, implement, pass, commit.
- Branch naming (if multi-branch work): ticket-key only (not used for this solo project).
- Coverage target: 70% in `src/newsroom/`.
- **Run tests/lint with the asdf prefix:** `ASDF_PYTHON_VERSION=3.12.9 uv run pytest …`
  and `… uv run ruff …`. Without it asdf errors "No version is set for command python3"
  (the interpreter is not pinned via `.tool-versions`).
- **Design specs go to `docs/specs/YYYY-MM-DD-<slug>-design.md`** — a PostToolUse hook
  blocks writes to `docs/superpowers/specs/` (pre-existing specs there stay tracked).
  ADRs go to `docs/adrs/NNN-<slug>.md` (MADR 4.0); the raw spec is deleted and the ADR
  added in one atomic commit (global CLAUDE.md C+D pattern).

## Phase Roadmap

- **Phase 1 (shipped):** AI/LLM/ML, 15 sources, twice-daily digest with H1 slot headers (`# News-Digest <date> (Morgen|Abend)`), arxiv-imp=5-only push policy. Live since 2026-04-21.
- **Phase 2a (shipped 2026-05-08, digest split 2026-06-22):** Weltgeschehen — second top-level category alongside `ai`. 5–7 sources in breaking/news/analysis subcategories. Per-category score prompt; subcategory-driven push thresholds via `NOTIFICATION_THRESHOLDS` table. Digest output is now split by category: one file per non-empty category per slot (`{date}_{category}.md`), H1 carries the category label — replacing the former single-file two-section layout. The tagesschau-breaking-importance heuristic is deferred to Phase 2b. See spec `docs/superpowers/specs/2026-05-07-daily-newsroom-phase2a-design.md` and plan `docs/superpowers/plans/2026-05-07-daily-newsroom-phase2a.md`.
- **Phase 2b (next):** Dresden + Dresden-Science (TU Dresden, MPI-CBG, MPI-PKS, HZDR Excellence Cluster). Builds on Phase-2a mechanism — mostly YAML + prompt edits.
- **Phase 3:** Tech, Wissenschaft (Physik/Chemie/Astro), APOD.

## Non-Goals — DO NOT add

- No cloud component, no hosting, no third-party services beyond LLM calls to Anthropic.
- No mobile app, no web UI (deferred).
- No auto-replies or auto-posts externally.
- No external telemetry.
- No paywall circumvention.

If any of these temptations arise, check the spec §10.3 Explicitly Never before writing code.
