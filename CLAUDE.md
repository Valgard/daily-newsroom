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
- `newsroom status` column "Last fetched" is `sources.last_fetched_at`, which only
  advances when the feed actually delivers **new items**. A quiet blog can read as
  "28h ago" while being probed hourly — the real probe cadence lives in
  `sources.last_checked_at`. Don't diagnose "fetcher stuck" from the status table
  alone; cross-check with `last_checked_at` via SQL or the error column.

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
