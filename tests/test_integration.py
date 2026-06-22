from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from freezegun import freeze_time
from pytest_httpx import HTTPXMock

from newsroom.config import Source
from newsroom.digester import generate_digest
from newsroom.fetcher import fetch_due_sources
from newsroom.notifier import Notifier
from newsroom.scorer import score_pending_items
from newsroom.state import State


@freeze_time("2026-04-19 10:00:00")
async def test_end_to_end_pipeline(
    tmp_path: Path,
    fixtures_dir: Path,
    httpx_mock: HTTPXMock,
) -> None:
    # 1. State + config
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

    # 2. Mock HTTP response
    httpx_mock.add_response(
        url="https://www.anthropic.com/news/rss.xml",
        content=(fixtures_dir / "feed_rss_sample.xml").read_bytes(),
        headers={"ETag": 'W/"test"'},
    )

    # 3. Fetch
    fetch_results = await fetch_due_sources(state)
    assert fetch_results[0].items_inserted == 2

    # 4. Mock agent
    agent_mock = AsyncMock()
    agent_mock.ask.side_effect = [
        {"importance": 5, "reason": "major"},
        {"importance": 3, "reason": "ok"},
        {"items": []},
    ]

    # 5. Score
    notifier = Notifier(send_fn=AsyncMock())
    await score_pending_items(state, agent=agent_mock, notifier=notifier, limit=10)
    scored = (
        state.connection().execute("SELECT COUNT(*) FROM items WHERE status='scored'").fetchone()[0]
    )
    assert scored == 2

    # 6. Generate digest
    written = await generate_digest(
        state=state,
        slot="morning",
        date=date(2026, 4, 19),
        output_root=tmp_path / "news",
        agent=agent_mock,
    )
    assert len(written) > 0
    out = written[0]
    assert out.exists()
    assert "# News-Digest 19. April 2026 — AI/LLM/ML (Morgen)" in out.read_text()

    # 7. Verify digest marked in state
    assert state.get_digest("2026-04-19", "morning") is not None


# ── Phase 2a integration ──────────────────────────────────────────────────────


@freeze_time("2026-05-08 22:30:00")
async def test_mixed_world_and_ai_digest_writes_both_sections(
    tmp_path: Path,
) -> None:
    """End-to-end: 2 ai + 2 world items → digest file has Welt H2 above AI H2."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()

    for name, cat, sub in [
        ("anthropic", "ai", "lab"),
        ("tagesschau-news", "world", "news"),
    ]:
        state.upsert_source(
            Source(
                name=name,
                category=cat,
                subcategory=sub,
                url=f"https://example.com/{name}.rss",
                feed_type="rss",
                interval_seconds=3600,
                enabled=True,
            )
        )

    ai_id = state.get_source_by_name("anthropic")["id"]
    world_id = state.get_source_by_name("tagesschau-news")["id"]

    for i, (sid, cat, title) in enumerate(
        [
            (world_id, "world", "Bundestag verabschiedet X"),
            (world_id, "world", "EZB senkt Zins"),
            (ai_id, "ai", "Claude 5 released"),
            (ai_id, "ai", "OpenAI o3 GA"),
        ]
    ):
        state.insert_item(
            source_id=sid,
            item_hash=f"h{i}",
            url=f"https://example.com/{i}",
            title=title,
            author=None,
            published_at="2026-05-08T08:00:00Z",
            raw_summary=f"body {i}",
            category=cat,
        )

    for item in state.list_items_by_status("new", limit=10):
        state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="haiku")

    mock_agent = AsyncMock()

    # Collect item IDs after scoring so we can key the LLM response
    items_in_db = state.list_items_for_digest(since_iso="2026-01-01T00:00:00")
    mock_agent.ask.return_value = {
        "items": [
            {"id": it["id"], "headline": it["title"], "prose": "Prosa."} for it in items_in_db
        ]
    }

    output_root = tmp_path / "news"
    await generate_digest(
        state=state,
        slot="evening",
        date=datetime(2026, 5, 8).date(),
        output_root=output_root,
        agent=mock_agent,
    )

    # The agent received items_markdown with both H2s in the right order
    items_md = mock_agent.ask.call_args.kwargs["variables"]["items_markdown"]
    welt_idx = items_md.find("## Weltgeschehen")
    ai_idx = items_md.find("## AI/LLM/ML")
    assert welt_idx >= 0 and ai_idx >= 0
    assert welt_idx < ai_idx, "Welt H2 must come before AI H2 in items_markdown"

    # Each category gets its own file; both must exist with the right H1
    month = output_root / "2026" / "05"
    world_file = month / "2026-05-08_world.md"
    ai_file = month / "2026-05-08_ai.md"
    assert world_file.exists(), "world digest file must exist"
    assert ai_file.exists(), "ai digest file must exist"

    world_body = world_file.read_text()
    ai_body = ai_file.read_text()
    assert "— Weltgeschehen (Abend)" in world_body
    assert "— AI/LLM/ML (Abend)" in ai_body
    # No in-file category section header (moved to H1)
    assert "## Weltgeschehen" not in world_body
    assert "## AI/LLM/ML" not in ai_body
    # Items land in the right file
    assert "Bundestag verabschiedet X" in world_body
    assert "Claude 5 released" in ai_body
    assert "Bundestag verabschiedet X" not in ai_body
