import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from newsroom.agent_client import AgentError
from newsroom.config import Source
from newsroom.digester import (
    NoSlotError,
    _relative_time,
    _render_digest,
    _render_item,
    determine_slot,
    format_items_for_prompt,
    generate_digest,
)
from newsroom.notifier import Notifier
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


def _item_row(**over):  # noqa: ANN001, ANN002, ANN003
    base = {
        "id": 1,
        "importance": 3,
        "title": "Title",
        "source_name": "zeit-politik",
        "published_at": "2026-04-19T02:00:00Z",
        "url": "https://e.x/a",
        "score_reason": "r",
        "raw_summary": "body",
        "source_category": "world",
    }
    base.update(over)
    return base


def test_render_item_full_with_quote_and_cross_link(tmp_path: Path) -> None:
    now = datetime(2026, 4, 19, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    link = tmp_path / "deep.md"
    out = _render_item(
        _item_row(),
        {"headline": "Eine Headline", "prose": "Ein Absatz.", "quote": '"Zitat."'},
        link,
        now,
    )
    assert out == (
        "### Eine Headline\n\n"
        "- [ ] interessiert mich\n\n"
        "Ein Absatz.\n\n"
        '› "Zitat."\n\n'
        f"[Weiterlesen →](https://e.x/a) · *zeit-politik · heute 04:00 · Importance 3*"
        f" · 📄 [Tief-Zusammenfassung]({link})"
    )


def test_render_item_minimal_no_quote_no_cross_link_empty_prose() -> None:
    now = datetime(2026, 4, 19, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    out = _render_item(_item_row(), {"headline": "H", "prose": ""}, None, now)
    assert out == (
        "### H\n\n"
        "- [ ] interessiert mich\n\n"
        "[Weiterlesen →](https://e.x/a) · *zeit-politik · heute 04:00 · Importance 3*"
    )


def test_relative_time_today_yesterday_older() -> None:
    now = datetime(2026, 4, 19, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    # 02:00Z in April = 04:00 Berlin (CEST, UTC+2)
    assert _relative_time("2026-04-19T02:00:00Z", now) == "heute 04:00"
    assert _relative_time("2026-04-18T05:00:00Z", now) == "gestern 07:00"
    assert _relative_time("2026-04-10T05:00:00Z", now) == "10.04. 07:00"


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


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_preserves_input_order(populated_state: State) -> None:
    """Items must render flat in the given input order — no subcat grouping,
    no re-sorting. Sort responsibility lives in SQL (`list_items_for_digest`).

    Phase-2a addition: AI-only output is wrapped in a single ``## AI/LLM/ML``
    H2 header (the category-boundary insertion fires once at the start of the
    only category present). The Welt H2 is omitted by the empty-section rule.
    """
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

    # Phase 2a: one ## AI/LLM/ML header at the top, no subcategory headers.
    assert formatted.count("## AI/LLM/ML") == 1
    assert "### lab" not in formatted
    assert "### curated" not in formatted
    # AI-only digest must NOT emit a Welt header (empty-section omission).
    assert "## Weltgeschehen" not in formatted

    # Each input item appears exactly once, in the SQL-defined order.
    titles_in_order = [item["title"] for item in items]
    positions = [formatted.find(t) for t in titles_in_order]
    assert all(p >= 0 for p in positions), "every item title must be rendered"
    assert positions == sorted(positions), "items must appear in input order"


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_includes_published_at(populated_state: State) -> None:
    """Digest prompt needs published_at so Opus can render relative time."""
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    # Fixture items all have published_at="2026-04-19T02:00:00Z"
    assert "published=2026-04-19T02:00:00Z" in formatted


@freeze_time("2026-04-19 22:30:00")
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


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_writes_file(populated_state: State, tmp_path: Path) -> None:
    output_root = tmp_path / "news"
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
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


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_records_in_state(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
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


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_marks_items_included(populated_state: State, tmp_path: Path) -> None:
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
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
    mock_agent.ask.return_value = {"items": []}
    await generate_digest(
        state=populated_state,
        slot="evening",
        date=datetime(2026, 4, 19).date(),
        output_root=output_root,
        agent=mock_agent,
    )
    body = (morning_dir / "2026-04-19.md").read_text()
    assert "# Morgen Content" in body
    assert "# News-Digest 19. April 2026 (Abend)" in body


@freeze_time("2026-04-19 22:30:00")
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
    # Phase-2a: the LLM-outage fallback path must also emit the ## category
    # boundary header (the populated_state fixture has only ai items, so just
    # one ## AI/LLM/ML wrapper appears and no ## Weltgeschehen).
    assert "## AI/LLM/ML" in body
    assert "## Weltgeschehen" not in body


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_fallback_emits_interest_checkbox(
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
    body = (tmp_path / "news" / "2026" / "04" / "2026-04-19.md").read_text()
    # one unchecked "interessiert mich" box per item (shared renderer, not old fallback format)
    assert body.count("[ ] interessiert mich") == 3
    assert "Importance" in body  # degraded render still carries importance metadata


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_renders_llm_content_with_checkbox(
    populated_state: State, tmp_path: Path
) -> None:
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {
        "items": [{"id": it["id"], "headline": f"H{it['id']}", "prose": "P."} for it in items]
    }
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    body = (tmp_path / "news" / "2026" / "04" / "2026-04-19.md").read_text()
    assert "### H" in body
    assert body.count("- [ ] interessiert mich") == len(items)
    assert "[Weiterlesen →]" in body
    assert "Importance" in body


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


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_force_regenerates_existing(
    populated_state: State, tmp_path: Path
) -> None:
    """--force must delete the old DB row + unmark items + rewrite the file."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {
        "items": [{"id": 1, "headline": "First-Run-Headline", "prose": "P."}]
    }
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
    mock_agent.ask.return_value = {
        "items": [{"id": 1, "headline": "Second-Run-Headline", "prose": "P."}]
    }
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
    assert "Second-Run-Headline" in file_body
    assert "First-Run-Headline" not in file_body


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_force_evening_replaces_previous_evening(
    populated_state: State, tmp_path: Path
) -> None:
    """--force on evening must truncate the old '... (Abend)' section before append."""
    output_root = tmp_path / "news"
    morning_dir = output_root / "2026" / "04"
    morning_dir.mkdir(parents=True)
    (morning_dir / "2026-04-19.md").write_text(
        "# News-Digest 19. April 2026 (Morgen)\n\nMorning body.\n\n"
        "---\n\n# News-Digest 19. April 2026 (Abend)\n\nOld evening body.\n"
    )

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {
        "items": [{"id": 1, "headline": "New-Evening-Headline", "prose": "P."}]
    }

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
    # Old evening dropped, new evening rendered by _render_digest
    assert "Old evening body." not in body
    assert "New-Evening-Headline" in body
    # Exactly one evening top-header
    assert body.count("(Abend)") == 1


# ── digest-ready notification (spec §4.4 step 10) ─────────────────────


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_notifies_when_items_present(
    populated_state: State, tmp_path: Path
) -> None:
    """Successful non-empty digest fires exactly one notify_digest_ready call."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    out = await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )

    notify_send.assert_awaited_once()
    kwargs = notify_send.call_args.kwargs
    assert kwargs["title"] == "Newsroom"
    assert kwargs["message"] == "Morgen-Digest bereit (3 Items)"
    assert kwargs["url"] == out.as_uri()


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_silent_on_empty_slot(state: State, tmp_path: Path) -> None:
    """Empty digest (no scored items in window) does NOT notify."""
    mock_agent = AsyncMock()
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=state,  # empty fixture from conftest
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )

    notify_send.assert_not_awaited()
    # File-Placeholder is still written
    assert (tmp_path / "news" / "2026" / "04" / "2026-04-19.md").exists()


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_no_notify_on_idempotent_skip(
    populated_state: State, tmp_path: Path
) -> None:
    """Second call without --force returns early and does NOT notify a second time."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    # First call: real digest, real notify
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1

    # Second call: idempotent skip — must NOT increment
    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1  # unchanged


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_no_notify_on_race_loser(
    populated_state: State,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When claim_digest_slot returns False (another process won the race),
    generate_digest must return early WITHOUT calling notify."""
    monkeypatch.setattr(populated_state, "claim_digest_slot", lambda **_kw: False)
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = "# Should never be produced"
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    mock_agent.ask.assert_not_called()
    notify_send.assert_not_awaited()


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_force_regen_notifies_again(
    populated_state: State, tmp_path: Path
) -> None:
    """--force regeneration fires another notify_digest_ready."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
    )
    assert notify_send.await_count == 1

    await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
        notifier=notifier,
        force=True,
    )
    assert notify_send.await_count == 2  # noqa: PLR2004


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_no_notifier_works(populated_state: State, tmp_path: Path) -> None:
    """Omitting the notifier param (default None) is allowed and does not crash."""
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}

    out = await generate_digest(
        state=populated_state,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        output_root=tmp_path / "news",
        agent=mock_agent,
    )
    assert out.exists()


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_emits_digest_finalized_event(
    populated_state: State, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Successful non-empty digest emits a `digest_finalized` lifecycle event so
    events.jsonl can be aggregated by `jq` (matches the fetch/score/run pattern
    introduced in commit 1443b61). Without this, a successful digest leaves no
    trace in events.jsonl — the 2026-05-06 notification-diagnosis took 30 min
    longer because the structured log was silent on the digest path.
    """
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}

    with caplog.at_level(logging.INFO, logger="newsroom.digester"):
        await generate_digest(
            state=populated_state,
            slot="morning",
            date=datetime(2026, 4, 19).date(),
            output_root=tmp_path / "news",
            agent=mock_agent,
        )

    finalized = [r for r in caplog.records if getattr(r, "event", None) == "digest_finalized"]
    assert len(finalized) == 1, f"expected one digest_finalized record, got {len(finalized)}"
    assert finalized[0].slot == "morning"
    assert finalized[0].item_count == 3


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_emits_digest_finalized_event_for_empty_slot(
    state: State, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Empty slot finalization also emits `digest_finalized` (with item_count=0).
    Both branches must be visible — otherwise an empty digest looks identical to
    a crashed digest in events.jsonl.
    """
    mock_agent = AsyncMock()

    with caplog.at_level(logging.INFO, logger="newsroom.digester"):
        await generate_digest(
            state=state,
            slot="morning",
            date=datetime(2026, 4, 19).date(),
            output_root=tmp_path / "news",
            agent=mock_agent,
        )

    finalized = [r for r in caplog.records if getattr(r, "event", None) == "digest_finalized"]
    assert len(finalized) == 1
    assert finalized[0].slot == "morning"
    assert finalized[0].item_count == 0


@freeze_time("2026-04-19 22:30:00")
async def test_generate_digest_emits_digest_notify_dispatched_event(
    populated_state: State, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """When a notifier is injected and the digest is non-empty, the dispatch of
    notify_digest_ready emits `digest_notify_dispatched`. This is what was
    missing in the 2026-05-06 incident: there was no log line proving the notify
    path even ran — diagnosis required reading code instead of grep'ing logs.
    """
    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"items": []}
    notify_send = AsyncMock()
    notifier = Notifier(send_fn=notify_send)

    with caplog.at_level(logging.INFO, logger="newsroom.digester"):
        await generate_digest(
            state=populated_state,
            slot="morning",
            date=datetime(2026, 4, 19).date(),
            output_root=tmp_path / "news",
            agent=mock_agent,
            notifier=notifier,
        )

    dispatched = [
        r for r in caplog.records if getattr(r, "event", None) == "digest_notify_dispatched"
    ]
    assert len(dispatched) == 1
    assert dispatched[0].slot == "morning"
    assert dispatched[0].item_count == 3


# ── Phase-2a §5.1: category-boundary H2 headers ───────────────────────


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_emits_world_then_ai_top_level_headers(
    populated_state: State, tmp_path: Path
) -> None:
    """Mixed digest: ## Weltgeschehen above world items, ## AI/LLM/ML above ai items."""
    populated_state.upsert_source(
        Source(
            name="tagesschau-news",
            category="world",
            subcategory="news",
            url="https://www.tagesschau.de/index~rss2.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = populated_state.get_source_by_name("tagesschau-news")["id"]
    populated_state.insert_item(
        source_id=src_id,
        item_hash="h-w",
        url="https://www.tagesschau.de/x",
        title="Bundestag-Item",
        author=None,
        published_at="2026-04-19T08:00:00Z",
        raw_summary="welt body",
        category="world",
    )
    item_id = populated_state.list_items_by_status("new", limit=1)[0]["id"]
    populated_state.mark_item_scored(item_id=item_id, importance=4, reason="welt", model="haiku")
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)

    welt_idx = formatted.find("## Weltgeschehen")
    ai_idx = formatted.find("## AI/LLM/ML")
    bundestag_idx = formatted.find("Bundestag-Item")
    title_0_idx = formatted.find("Title 0")  # AI item from the populated_state fixture

    assert welt_idx >= 0, "Welt H2 missing"
    assert ai_idx >= 0, "AI H2 missing"
    assert welt_idx < bundestag_idx < ai_idx < title_0_idx, (
        "expected order: ## Weltgeschehen, welt items, ## AI/LLM/ML, ai items"
    )


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_ai_only_wraps_in_ai_header(
    populated_state: State, tmp_path: Path
) -> None:
    """AI-only digest gets one ## AI/LLM/ML header, no Welt header."""
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "## AI/LLM/ML" in formatted
    assert "## Weltgeschehen" not in formatted


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_world_only_omits_ai_header(tmp_path: Path) -> None:
    """Welt-only digest gets one ## Weltgeschehen header, no AI header."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="tagesschau-news",
            category="world",
            subcategory="news",
            url="https://www.tagesschau.de/index~rss2.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=src_id,
        item_hash="h-w",
        url="https://t.de/x",
        title="World Only",
        author=None,
        published_at="2026-04-19T08:00:00Z",
        raw_summary="body",
        category="world",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=4, reason="r", model="haiku")
    items = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "## Weltgeschehen" in formatted
    assert "## AI/LLM/ML" not in formatted


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_includes_id(populated_state: State) -> None:
    """Each item line should start with id={id}; url= and summary_path= removed."""
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    for item in items:
        assert f"id={item['id']}" in formatted
    assert "summary_path=" not in formatted
    assert "url=" not in formatted


def test_render_digest_groups_degrades_and_omits_separator() -> None:
    now = datetime(2026, 4, 19, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    items = [
        _item_row(id=1, source_category="world", title="W1"),
        _item_row(id=2, source_category="ai", title="A1", raw_summary="ai summary"),
    ]
    contents = {
        1: {"headline": "Welt-Headline", "prose": "Welt-Prosa."}
    }  # id=2 missing -> degraded
    cross_links = {1: None, 2: None}
    out = _render_digest(
        items,
        contents,
        cross_links,
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        now=now,
    )
    assert out.startswith(
        "# News-Digest 19. April 2026 (Morgen)\n\n## Weltgeschehen\n\n### Welt-Headline"
    )
    assert "## AI/LLM/ML" in out
    assert "### A1" in out  # degraded headline = title
    assert "ai summary" in out  # degraded prose = raw_summary
    assert "---" not in out  # separator owned by _write_digest_file
    assert out.count("- [ ] interessiert mich") == 2


def test_render_digest_banner_after_header() -> None:
    now = datetime(2026, 4, 19, 22, 30, tzinfo=ZoneInfo("Europe/Berlin"))
    items = [_item_row(id=1, source_category="ai", title="A1")]
    out = _render_digest(
        items,
        {},
        {1: None},
        slot="morning",
        date=datetime(2026, 4, 19).date(),
        now=now,
        banner="⚠️ Test-Banner",
    )
    assert out.startswith("# News-Digest 19. April 2026 (Morgen)\n\n⚠️ Test-Banner\n\n## AI/LLM/ML")
