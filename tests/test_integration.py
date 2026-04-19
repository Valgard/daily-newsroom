from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

from pytest_httpx import HTTPXMock

from newsroom.config import Source
from newsroom.digester import generate_digest
from newsroom.fetcher import fetch_due_sources
from newsroom.notifier import Notifier
from newsroom.scorer import score_pending_items
from newsroom.state import State


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
        "# Morning digest\n\n## Lab\n- Item 1\n- Item 2",
    ]

    # 5. Score
    notifier = Notifier(send_fn=AsyncMock())
    await score_pending_items(state, agent=agent_mock, notifier=notifier, limit=10)
    scored = (
        state.connection().execute("SELECT COUNT(*) FROM items WHERE status='scored'").fetchone()[0]
    )
    assert scored == 2

    # 6. Generate digest
    await generate_digest(
        state=state,
        slot="morning",
        date=date(2026, 4, 19),
        output_root=tmp_path / "news",
        agent=agent_mock,
    )
    out = tmp_path / "news" / "2026" / "04" / "2026-04-19.md"
    assert out.exists()
    assert "Morning digest" in out.read_text()

    # 7. Verify digest marked in state
    assert state.get_digest("2026-04-19", "morning") is not None
