"""Smoke test: print_status must not crash on a minimal empty State."""

from pathlib import Path

from rich.console import Console

from newsroom.state import State
from newsroom.status import print_status


def test_print_status_does_not_crash_on_empty_state(tmp_path: Path, capsys) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    console = Console(force_terminal=False, no_color=True, width=80)
    print_status(state, console=console)
    # Just verify it ran — actual content varies by environment (keychain, log dir)
    assert True  # no exception == pass
