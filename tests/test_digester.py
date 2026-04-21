from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from newsroom.agent_client import AgentError
from newsroom.config import Source
from newsroom.digester import (
    NoSlotError,
    determine_slot,
    format_items_for_prompt,
    generate_digest,
)
from newsroom.state import State


@pytest.fixture
def populated_state(tmp_path: Path) -> State:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="anthropic",
            category="ai",
            subcategory="lab",
            url="https://www.anthropic.com/news/rss.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("anthropic")["id"]
    for i, imp in enumerate([5, 4, 3]):
        state.insert_item(
            source_id=src_id,
            item_hash=f"h{i}",
            url=f"https://a.com/{i}",
            title=f"Title {i}",
            author=None,
            published_at="2026-04-19T02:00:00Z",
            raw_summary=f"summary {i}",
            category="ai",
        )
        item_id = state.list_items_by_status("new", limit=1)[0]["id"]
        state.mark_item_scored(item_id=item_id, importance=imp, reason=f"r{i}", model="haiku")
    return state


@freeze_time("2026-04-19 07:30")
def test_determine_slot_morning() -> None:
    assert determine_slot() == "morning"


@freeze_time("2026-04-19 20:15")
def test_determine_slot_evening() -> None:
    assert determine_slot() == "evening"


@freeze_time("2026-04-19 13:00")
def test_determine_slot_returns_none_outside_windows() -> None:
    with pytest.raises(NoSlotError):
        determine_slot()


def test_format_items_for_prompt_groups_by_subcategory(populated_state: State) -> None:
    # Add a second source with different subcategory
    populated_state.upsert_source(
        Source(
            name="simon",
            category="ai",
            subcategory="curated",
            url="https://simonwillison.net/atom/everything/",
            feed_type="atom",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = populated_state.get_source_by_name("simon")["id"]
    populated_state.insert_item(
        source_id=src_id,
        item_hash="h-simon",
        url="https://simonwillison.net/x",
        title="Simon Post",
        author="Simon",
        published_at=None,
        raw_summary="body",
        category="ai",
    )
    item_id = populated_state.list_items_by_status("new", limit=1)[0]["id"]
    populated_state.mark_item_scored(item_id=item_id, importance=4, reason="curated", model="haiku")
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "lab" in formatted.lower() or "Labs" in formatted
    assert "curated" in formatted.lower() or "Curated" in formatted


def test_format_items_for_prompt_includes_published_at(populated_state: State) -> None:
    """Digest prompt needs published_at so Opus can render relative time."""
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    # Fixture items all have published_at="2026-04-19T02:00:00Z"
    assert "published=2026-04-19T02:00:00Z" in formatted


def test_format_items_for_prompt_preserves_body_up_to_1200_chars(populated_state: State) -> None:
    """Body truncation is 1200 chars (was 300) — newsletter-style needs more material."""
    src_id = populated_state.get_source_by_name("anthropic")["id"]
    long_body = ("Claude 5 introduces native Model Context Protocol support. " * 30).strip()
    assert len(long_body) > 1200  # noqa: PLR2004  sanity check on fixture
    populated_state.insert_item(
        source_id=src_id,
        item_hash="h-long",
        url="https://a.com/long",
        title="Long body item",
        author=None,
        published_at="2026-04-19T03:00:00Z",
        raw_summary=long_body,
        category="ai",
    )
    new_items = populated_state.list_items_by_status("new", limit=10)
    long_item = next(i for i in new_items if i["title"] == "Long body item")
    populated_state.mark_item_scored(
        item_id=long_item["id"], importance=5, reason="major", model="haiku"
    )
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    # 800 chars of body must survive truncation (old 300-char limit would have failed this)
    assert long_body[:800] in formatted


async def test_generate_digest_writes_file(populated_state: State, tmp_path: Path) -> None:
    output_root = tmp_path / "news"
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# News-Digest 19. April 2026 (Morgen)\n\n## AI / LLM\n- Dummy"
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root,
        agent=mock_agent,
    )
    out = output_root / "2026" / "04" / "2026-04-19.md"
    assert out.exists()
    body = out.read_text()
    assert "Morgen" in body


async def test_generate_digest_records_in_state(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Test"
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    row = populated_state.get_digest("2026-04-19", "morning")
    assert row is not None
    assert row["item_count"] == 3
    assert row["model"] == "claude-opus-4-7"


async def test_generate_digest_marks_items_included(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Test"
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    rows = (
        populated_state.connection()
        .execute("SELECT * FROM items WHERE included_in_digest IS NOT NULL")
        .fetchall()
    )
    assert len(rows) == 3


async def test_generate_digest_evening_appends(populated_state: State, tmp_path: Path) -> None:
    output_root = tmp_path / "news"
    # Pre-create morning file
    morning_dir = output_root / "2026" / "04"
    morning_dir.mkdir(parents=True)
    (morning_dir / "2026-04-19.md").write_text("# Morgen Content\n")

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "## Abend-Digest\n\n- x"
    await generate_digest(
        state=populated_state,
        slot="evening",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root,
        agent=mock_agent,
    )
    body = (morning_dir / "2026-04-19.md").read_text()
    assert "# Morgen Content" in body
    assert "## Abend-Digest" in body


async def test_generate_digest_falls_back_on_llm_error(
    populated_state: State, tmp_path: Path
) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.side_effect = AgentError("opus unavailable")
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    out = tmp_path / "news" / "2026" / "04" / "2026-04-19.md"
    body = out.read_text()
    assert "Automatisch generiert" in body  # fallback marker
    assert "Title 0" in body  # items still listed


# ── --force regeneration (fixes: IntegrityError on existing digest row;
#    evening appending instead of replacing) ───────────────────────────


async def test_generate_digest_skips_opus_when_claim_fails(
    populated_state: State, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loser of the slot-claim race must abort cheaply — no Opus call, no crash.

    Simulates: another process claimed the slot between our initial get_digest()
    check and our own claim attempt. The claim method returns False; the function
    must return without ever invoking the LLM.
    """
    monkeypatch.setattr(populated_state, "claim_digest_slot", lambda **_kw: False)
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Should never be produced"

    path = await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    mock_agent.ask.assert_not_called()
    # Return value is a Path — caller can still locate the winner's output.
    assert path is not None


async def test_generate_digest_force_regenerates_existing(
    populated_state: State, tmp_path: Path
) -> None:
    """--force must delete the old DB row + unmark items + rewrite the file."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# First run content"
    # First run: normal generation
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    first_row = populated_state.get_digest("2026-04-19", "morning")
    assert first_row is not None

    # Second run with force=True — must succeed, not raise IntegrityError
    mock_agent.ask.return_value = "# Second run content"
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        force=True,
    )
    second_row = populated_state.get_digest("2026-04-19", "morning")
    assert second_row is not None
    # Same (date, slot) UNIQUE but new content in file
    file_body = (tmp_path / "news" / "2026" / "04" / "2026-04-19.md").read_text()
    assert "Second run content" in file_body
    assert "First run content" not in file_body


async def test_generate_digest_force_evening_replaces_previous_evening(
    populated_state: State, tmp_path: Path
) -> None:
    """--force on evening must truncate the old '## Abend-Digest' section before append."""
    output_root = tmp_path / "news"
    morning_dir = output_root / "2026" / "04"
    morning_dir.mkdir(parents=True)
    (morning_dir / "2026-04-19.md").write_text(
        "# News-Digest Morgen\n\nMorning body.\n\n---\n\n## Abend-Digest\n\nOld evening body.\n"
    )

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "## Abend-Digest\n\nNew evening body.\n"

    # Need to seed DB with matching existing digest + items so the force path activates
    populated_state.insert_digest(
        date="2026-04-19",
        slot="evening",
        file_path=str(morning_dir / "2026-04-19.md"),
        item_count=3,
        model="claude-opus-4-7",
    )

    await generate_digest(
        state=populated_state,
        slot="evening",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root,
        agent=mock_agent,
        force=True,
    )

    body = (morning_dir / "2026-04-19.md").read_text()
    # Morning section preserved
    assert "Morning body." in body
    # Old evening dropped, new evening present
    assert "Old evening body." not in body
    assert "New evening body." in body
    # Exactly one '## Abend-Digest' header
    assert body.count("## Abend-Digest") == 1
