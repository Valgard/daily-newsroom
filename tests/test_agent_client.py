from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage

from newsroom.agent_client import AgentClient, AgentError, ParseError, render_prompt


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


async def test_ask_raises_agent_error_on_empty_stream(fixtures_dir: Path) -> None:
    async def _empty_stream():
        return
        yield  # make this a generator function

    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _empty_stream()
        with pytest.raises(AgentError, match="no ResultMessage"):
            # Call __wrapped__ to bypass tenacity retries (avoids 5-60s backoff delays)
            await client.ask.__wrapped__(
                client,
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="text",
            )


async def test_ask_wraps_sdk_exception_as_agent_error(fixtures_dir: Path) -> None:
    async def _exploding_stream():
        raise RuntimeError("boom")
        yield  # unreachable but makes this an async generator

    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _exploding_stream()
        with pytest.raises(AgentError, match="agent call failed"):
            # Call __wrapped__ to bypass tenacity retries (avoids 5-60s backoff delays)
            await client.ask.__wrapped__(
                client,
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="text",
            )


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
