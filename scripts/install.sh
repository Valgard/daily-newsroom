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
