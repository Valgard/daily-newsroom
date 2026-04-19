#!/usr/bin/env bash
# Launchd entry point. Resolves asdf shims so `uv` is callable.
# asdf 0.16+ is a native Go binary with no asdf.sh source script — shims
# must be on PATH explicitly. Older versions shipped ~/.asdf/asdf.sh;
# we source it as a fallback for pre-0.16 setups.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# asdf 0.16+ (Go-rewrite): no asdf.sh; shims must be on PATH
if [ -d "$HOME/.asdf/shims" ]; then
    export PATH="$HOME/.asdf/shims:$PATH"
fi

# asdf pre-0.16 fallback
if [ -f "$HOME/.asdf/asdf.sh" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.asdf/asdf.sh"
fi

exec uv run python -m newsroom "$@"
