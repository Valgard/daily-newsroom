"""CLI integration tests."""

from __future__ import annotations

from typer.testing import CliRunner

from newsroom.cli import app

runner = CliRunner()


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "newsroom" in result.stdout


def test_cli_help() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["fetch", "score", "digest", "notify", "status", "init", "validate-config"]:
        assert cmd in result.stdout


def test_cli_fetch_dry_run(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("NEWSROOM_SOURCES_PATH", str(tmp_path / "empty.yaml"))
    (tmp_path / "empty.yaml").write_text("sources: []\n")
    # init first
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["fetch", "--dry-run"])
    assert result.exit_code == 0


def test_cli_notify_test_command(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("NEWSROOM_DB_PATH", str(tmp_path / "t.db"))
    # init DB
    runner.invoke(app, ["init"])
    # --test should succeed even without state (sends a ping)
    result = runner.invoke(app, ["notify", "--test"])
    # exit 0 or 1 depending on pync availability; should not raise
    assert result.exit_code in (0, 1)
