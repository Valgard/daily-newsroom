"""Pre-scoring filter for arXiv items: Haiku decides relevance to user's themes."""

from __future__ import annotations

import logging

from newsroom.agent_client import AgentClient

logger = logging.getLogger(__name__)


async def filter_pending_arxiv_items(state, agent: AgentClient | None = None) -> int:
    """Filter all 'new' items from arxiv sources. Returns count processed."""
    if agent is None:
        agent = AgentClient()
    conn = state.connection()
    arxiv_items = conn.execute(
        "SELECT items.* FROM items "
        "JOIN sources ON items.source_id = sources.id "
        "WHERE items.status = 'new' AND sources.subcategory = 'arxiv'"
    ).fetchall()

    processed = 0
    for item in arxiv_items:
        try:
            result = await agent.ask(
                prompt_name="filter_arxiv",
                variables={
                    "title": item["title"],
                    "summary": item["raw_summary"] or "",
                },
                model="claude-haiku-4-5",
                parse="json",
            )
            relevant = bool(result.get("relevant", False))
            state.mark_item_arxiv_filter(item_id=item["id"], relevant=relevant)
            processed += 1
        except Exception as e:  # noqa: BLE001 — broad safety: per-item failures (including ParseError) shouldn't abort the batch
            logger.warning("arxiv filter failed for item %s: %s", item["id"], e)
            # Leave item in 'new' status → will retry next cycle
    return processed
