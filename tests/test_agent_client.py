from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage

from newsroom.agent_client import AgentClient, ParseError, render_prompt


def test_render_prompt_substitutes_variables(fixtures_dir: Path) -> None:
    result = render_prompt(fixtures_dir / "prompt_test_echo.md", {"value": "hello"})
    assert "hello" in result
    assert "{{" not in result


def test_render_prompt_fails_on_missing_variable(fixtures_dir: Path) -> None:
    with pytest.raises(KeyError):
        render_prompt(fixtures_dir / "prompt_test_echo.md", {})


async def test_ask_parses_json(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream('{"echoed": "hi"}')
        result = await client.ask(
            prompt_name="prompt_test_echo",
            variables={"value": "hi"},
            model="claude-haiku-4-5",
            parse="json",
        )
    assert result == {"echoed": "hi"}


async def test_ask_raises_on_invalid_json(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream("not-json-at-all")
        with pytest.raises(ParseError):
            await client.ask(
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="json",
            )


async def test_ask_returns_raw_text_when_parse_none(fixtures_dir: Path) -> None:
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream("# Markdown\n\nBody.")
        result = await client.ask(
            prompt_name="prompt_test_echo",
            variables={"value": "x"},
            model="claude-opus-4-7",
            parse="text",
        )
    assert result == "# Markdown\n\nBody."


async def _mock_stream(text: str):
    """Yield a real ResultMessage so isinstance checks pass."""
    yield ResultMessage(
        subtype="result",
        duration_ms=0,
        duration_api_ms=0,
        is_error=False,
        num_turns=1,
        session_id="test-session",
        result=text,
    )
