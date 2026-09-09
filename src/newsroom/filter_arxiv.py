"""Pre-scoring filter for arXiv items: Haiku decides relevance to user's themes."""

from __future__ import annotations

import logging

from newsroom.agent_client import AgentClient

logger = logging.getLogger(__name__)


async def filter_pending_arxiv_items(state, agent: AgentClient | None = None) -> int:
    """Filter all 'new' items from arxiv sources. Returns count processed."""
    if agent is None:
        agent = AgentClient()
    arxiv_items = state.list_pending_arxiv_items()
    total = len(arxiv_items)

    processed = 0
    for seen, item in enumerate(arxiv_items, start=1):
        try:
            result = await agent.ask(
                prompt_name="filter_arxiv",
                variables={
                    "title": item["title"],
                    "summary": item["raw_summary"] or "",
                },
                model="claude-haiku-4-5",
                parse="json",
                # Same reasoning as the scorer: the next cycle is the cheaper retry.
                retry_parse=False,
            )
            relevant = bool(result.get("relevant", False))
            state.mark_item_arxiv_filter(item_id=item["id"], relevant=relevant)
            processed += 1
        except Exception as e:  # noqa: BLE001 — broad safety: per-item failures (including ParseError) shouldn't abort the batch
            logger.warning("arxiv filter failed for item %s: %s", item["id"], e)
            # Leave item in 'new' status → will retry next cycle
        # Live throughput heartbeat every 10 items; final boundary is summarized
        # by cli.py's "filter phase: ... arxiv items processed" line.
        if seen % 10 == 0 and seen < total:
            logger.info("filter progress: %d/%d", seen, total)
    return processed
