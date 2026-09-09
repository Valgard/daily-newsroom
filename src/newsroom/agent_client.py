"""Thin wrapper around claude-agent-sdk with prompt loading and JSON parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

ParseMode = Literal["json", "text"]
DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent.parent / "config" / "prompts"


class ParseError(Exception):
    """LLM response could not be parsed as expected.

    Carries the *full* response in `raw_response`: the message truncates the
    candidate at 200 chars, which regularly cut off the actual break point.
    """

    def __init__(self, message: str, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


class AgentError(Exception):
    """LLM call failed after retries."""


def render_prompt(path: Path, variables: dict[str, Any]) -> str:
    """Load a prompt file and substitute {{ variable }} tokens.

    Uses a simple Jinja-like syntax but without full Jinja to avoid the dependency.
    Missing variable → KeyError.

    Variable names must match ``\\w+`` (alphanumeric + underscore). Dotted
    or hyphenated tokens like ``{{ item.title }}`` or ``{{ source-name }}`` pass
    through unchanged (they do not match the regex). Use a flat dict key.
    """
    template = path.read_text()

    def sub(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key not in variables:
            raise KeyError(
                f"prompt {path.name} references {{{{ {key} }}}} but variable not provided"
            )
        return str(variables[key])

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, template)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM response text.

    Handles: raw JSON, JSON wrapped in ```json fences, JSON with leading/trailing prose.
    """
    text = text.strip()
    # Fenced JSON block
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1)
    else:
        # Greedy match: first { … last } across the whole text
        obj_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not obj_match:
            raise ParseError(f"no JSON object found in response: {text[:200]!r}", raw_response=text)
        candidate = obj_match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ParseError(
            f"invalid JSON in response: {e}; candidate={candidate[:200]!r}",
            raw_response=text,
        ) from e


class AgentClient:
    """Call Claude via claude-agent-sdk with prompt templating."""

    def __init__(self, prompts_dir: Path = DEFAULT_PROMPTS_DIR) -> None:
        self.prompts_dir = prompts_dir

    @retry(
        # ParseError is retryable too: the model is non-deterministic, so a malformed
        # response is usually fixed by asking again. Without it a single stray quote
        # in the JSON went straight to a degraded digest (~7% of runs).
        retry=retry_if_exception_type((AgentError, ParseError)),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def ask(
        self,
        *,
        prompt_name: str,
        variables: dict[str, Any],
        model: str,
        parse: ParseMode = "json",
    ) -> Any:
        """Run a prompt and return parsed result."""
        prompt_path = self.prompts_dir / f"{prompt_name}.md"
        prompt_text = render_prompt(prompt_path, variables)

        options = ClaudeAgentOptions(
            allowed_tools=[],  # no tools needed for scoring/filtering/summarization
            model=model,
        )
        last_result: str | None = None
        try:
            async for msg in query(prompt=prompt_text, options=options):
                if isinstance(msg, ResultMessage):
                    last_result = msg.result
        except Exception as e:  # SDK errors are broad
            raise AgentError(f"agent call failed: {e}") from e

        if last_result is None:
            raise AgentError("agent returned no ResultMessage")

        if parse == "json":
            return _extract_json(last_result)
        elif parse == "text":
            return last_result
        else:
            raise ValueError(f"unknown parse mode: {parse}")
