"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from newsroom.state import State

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _isolate_parse_failure_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep parse-failure dumps out of the real ~/Library/Logs/newsroom/.

    PARSE_FAILURE_DIR is a module constant, so tmp_path does not cover it on its own.
    A test that forgets to patch it writes into the user's actual log directory
    without ever turning red. It lives here rather than in test_digester.py because
    test_integration.py calls generate_digest too.
    """
    monkeypatch.setattr("newsroom.digester.PARSE_FAILURE_DIR", tmp_path / "parse-failures")


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "test.db")
    s.ensure_schema()
    return s
