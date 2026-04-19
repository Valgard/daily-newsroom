"""Shared pytest fixtures."""

from pathlib import Path

import pytest

from newsroom.state import State

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def state(tmp_path: Path) -> State:
    s = State(tmp_path / "test.db")
    s.ensure_schema()
    return s
