"""Score items for importance via Haiku, trigger notifications for high-importance."""

from __future__ import annotations

import logging
from typing import Any

from newsroom.agent_client import AgentClient

logger = logging.getLogger(__name__)

SCORING_MODEL = "claude-haiku-4-5"


async def score_pending_items(
    state,
    *,
    agent: AgentClient | None = None,
    notifier: Any | None = None,
    limit: int = 100,
) -> int:
    """Score every 'new' or 'filtered_in' item. Returns count scored."""
    if agent is None:
        agent = AgentClient()

    pending = state.list_items_for_scoring(limit=limit)
    scored = 0
    for item in pending:
        try:
            result = await agent.ask(
                prompt_name="score_item",
                variables={
                    "source_name": item["source_name"],
                    "title": item["title"],
                    "author": item["author"] or "Unknown",
                    "summary": item["raw_summary"] or "(no body)",
                },
                model=SCORING_MODEL,
                parse="json",
            )
            importance = int(result.get("importance", 2))
            if not 1 <= importance <= 5:  # noqa: PLR2004
                importance = 2
            reason = str(result.get("reason", ""))
            state.mark_item_scored(
                item_id=item["id"],
                importance=importance,
                reason=reason,
                model=SCORING_MODEL,
            )
            scored += 1
            if notifier is not None:
                updated = state.get_item_with_source(item["id"])
                await notifier.maybe_notify(updated, state)
        except Exception as e:  # noqa: BLE001 — broad: per-item failures shouldn't abort the batch
            logger.warning("scoring failed for item %s: %s", item["id"], e)
    return scored
