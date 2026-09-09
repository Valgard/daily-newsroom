from pathlib import Path
from unittest.mock import patch

import pytest
from claude_agent_sdk import ResultMessage
from tenacity import wait_none

from newsroom.agent_client import AgentClient, AgentError, ParseError, render_prompt


@pytest.fixture
def instant_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop tenacity's 5-60s backoff so retry behaviour itself stays testable.

    The other tests bypass the decorator via `__wrapped__` to stay fast; tests that
    assert on retrying must go *through* it, and would otherwise sleep 15s.
    """
    monkeypatch.setattr(AgentClient.ask.retry, "wait", wait_none())


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
            # Call __wrapped__ to bypass tenacity retries (avoids 5-60s backoff delays)
            await client.ask.__wrapped__(
                client,
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="json",
            )


async def test_ask_retries_on_invalid_json(fixtures_dir: Path, instant_retry: None) -> None:
    """A malformed response is retryable: the model is non-deterministic, so ask again."""
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.side_effect = lambda **_: _mock_stream("not-json-at-all")
        with pytest.raises(ParseError):
            await client.ask(
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="json",
            )
    assert mock_query.call_count == 3


async def test_ask_recovers_when_retry_returns_valid_json(
    fixtures_dir: Path, instant_retry: None
) -> None:
    """One malformed response must not force a degraded digest — the retry saves it."""
    responses = iter(["not-json-at-all", '{"echoed": "hi"}'])
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.side_effect = lambda **_: _mock_stream(next(responses))
        result = await client.ask(
            prompt_name="prompt_test_echo",
            variables={"value": "x"},
            model="claude-haiku-4-5",
            parse="json",
        )
    assert result == {"echoed": "hi"}
    assert mock_query.call_count == 2


async def test_ask_rejects_json_array_as_parse_error(fixtures_dir: Path) -> None:
    """An array satisfies "valid JSON" but not the object contract callers rely on.

    Left through, `result.get("items")` raises AttributeError — no ParseError, so no
    retry, and the digest blames an outage that never happened.
    """
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream('```json\n[{"id": 1}]\n```')
        with pytest.raises(ParseError, match="expected JSON object"):
            # Call __wrapped__ to bypass tenacity retries (avoids backoff delays)
            await client.ask.__wrapped__(
                client,
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="json",
            )


async def test_parse_error_carries_full_raw_response(fixtures_dir: Path) -> None:
    """The log truncates at 200 chars; the exception must carry the whole response."""
    broken = '{"items": [{"id": 1, "headline": "Er nannte es "Zäsur" fuer die CDU"}]}' + "x" * 500
    client = AgentClient(prompts_dir=fixtures_dir)
    with patch("newsroom.agent_client.query") as mock_query:
        mock_query.return_value = _mock_stream(broken)
        with pytest.raises(ParseError) as exc_info:
            # Call __wrapped__ to bypass tenacity retries (avoids 5-60s backoff delays)
            await client.ask.__wrapped__(
                client,
                prompt_name="prompt_test_echo",
                variables={"value": "x"},
                model="claude-haiku-4-5",
                parse="json",
            )
    assert exc_info.value.raw_response == broken


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
