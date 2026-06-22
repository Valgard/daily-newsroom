# daily-newsroom

A local, macOS-native news-digest agent. Fetches AI/LLM/ML and world-news sources
every 5 min, scores for importance via Claude Haiku, pushes macOS notifications for
breaking news, and generates morning/evening markdown digests in `~/Documents/!AI/news/`
— item content comes from Claude Opus as JSON, the digest structure is rendered by Python.

**Status:** Phase 2a live (AI/LLM/ML + Weltgeschehen). Phase 2b (Dresden) and
Phase 3 (Tech, Science, APOD) planned.

## Requirements

- macOS (uses launchd, macOS Keychain, Notification Center)
- `asdf` with Python 3.12
- `uv`
- Installed Claude Code with Max 20x (or Team) subscription, logged in
- `terminal-notifier` for macOS notifications: `brew install terminal-notifier`

## Install

~~~bash
git clone <repo-url>
cd daily-newsroom
uv sync
./scripts/install.sh
~~~

Verify:

~~~bash
uv run python -m newsroom status
launchctl list | grep newsroom
~~~

## Usage

All production triggers are via launchd. For manual / debug runs:

~~~bash
uv run python -m newsroom fetch              # fetch due sources, score, maybe push
uv run python -m newsroom fetch --dry-run    # show what would be fetched
uv run python -m newsroom score              # score pending items (normally chained)
uv run python -m newsroom digest             # generate digest for current slot
uv run python -m newsroom digest --time morning --force  # force morning digest
uv run python -m newsroom notify --test      # test notification
uv run python -m newsroom status             # overview of system health
~~~

## Configuration

- `config/sources.yaml` — feed URLs, intervals, enabled flags
- `config/prompts/*.md` — LLM prompts (editable, hot-reloadable)
- `config/launchd/*.plist` — launchd schedules

After editing `sources.yaml`:

~~~bash
uv run python -m newsroom validate-config
uv run python -m newsroom init   # sync DB with new config
~~~

## Layout

See `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md` for the full design spec.

## Troubleshooting

- **No notifications appearing:** `brew install terminal-notifier`, then `newsroom notify --test`.
- **`claude` CLI not found:** update `PATH` in `config/launchd/*.plist` to match your Homebrew prefix.
- **Auth expired:** run `claude /login` once to refresh the OAuth token.
- **Logs:** `~/Library/Logs/newsroom/{fetch,digest-morning,digest-evening}.log`

## Uninstall

~~~bash
./scripts/uninstall.sh
~~~

State DB and logs are kept by default; delete manually if desired:

~~~bash
rm -rf ~/Library/Application\ Support/daily-newsroom
rm -rf ~/Library/Logs/newsroom
~~~

## Development

~~~bash
uv run pytest                    # fast tests
uv run pytest -m real_llm        # smoke tests against real API (manual)
uv run ruff check && uv run ruff format
uv run vulture src/
~~~
