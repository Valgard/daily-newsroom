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
