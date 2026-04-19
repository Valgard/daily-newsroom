"""macOS push notifications for high-importance items."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Notification category/label prefix
TITLE_PREFIX = {
    "ai": "AI",
}

QUIET_HOUR_START = 22
QUIET_HOUR_END = 7
THRESHOLD_QUIET = 5
THRESHOLD_DAYTIME = 4
THRESHOLD_NEVER = 99

# Spec §4.3: suppress duplicate pushes in the same category within this window.
BUNDLING_WINDOW_MINUTES = 15


def compute_threshold_for_hour(
    hour: int,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> int:
    """Effective importance threshold for pushing a notification.

    `category` is reserved for Phase 2 per-category overrides (e.g.
    weltgeschehen-breaking would lower the threshold); unused today.
    """
    # arxiv sub-category: never single-push (only via digest)
    if subcategory == "arxiv":
        return THRESHOLD_NEVER

    quiet_hours = hour >= QUIET_HOUR_START or hour < QUIET_HOUR_END
    return THRESHOLD_QUIET if quiet_hours else THRESHOLD_DAYTIME


def meets_threshold(item: Any, *, hour: int, subcategory: str | None) -> bool:
    threshold = compute_threshold_for_hour(
        hour,
        category=item["category"],
        subcategory=subcategory,
    )
    importance = item["importance"] or 0
    return importance >= threshold


def _send_with_pync(title: str, message: str, open_url: str | None = None) -> None:
    """Real macOS notification via pync. Synchronous — wrap in executor from async code."""
    try:
        import pync  # pync is macOS-only  # noqa: PLC0415
    except ImportError:
        logger.warning("pync not available; notification skipped")
        return
    pync.notify(message, title=title, open=open_url or "")


class Notifier:
    """Encapsulates push-notification logic. `send_fn` is async for test injection."""

    def __init__(
        self,
        *,
        send_fn: Callable[..., Awaitable[None]] | None = None,
    ) -> None:
        if send_fn is None:

            async def default_send(*, title: str, message: str, url: str | None) -> None:
                import anyio  # noqa: PLC0415

                await anyio.to_thread.run_sync(lambda: _send_with_pync(title, message, url))

            send_fn = default_send
        self._send = send_fn

    async def maybe_notify(self, item: Any, state: Any) -> None:
        """Decide whether to notify; if yes, send and mark.

        Spec §4.3 bundling: if another item in the same category has already
        been pushed within the last 15 minutes, suppress this push (the item
        is still marked as notified so the scorer does not retry it, and it
        still flows through to the digest).
        """
        # Already notified?
        if item["notified_at"] is not None:
            return

        now = datetime.now(BERLIN_TZ)
        hour = now.hour
        subcategory = item["source_subcategory"]

        if not meets_threshold(item, hour=hour, subcategory=subcategory):
            return

        recent_pushes = state.count_recent_notifications_by_category(
            item["category"], within_minutes=BUNDLING_WINDOW_MINUTES
        )
        if recent_pushes >= 1:
            logger.info(
                "suppressed push for item %s: category %r already notified within %dmin",
                item["id"],
                item["category"],
                BUNDLING_WINDOW_MINUTES,
            )
            state.mark_item_notified(item_id=item["id"])
            return

        prefix = TITLE_PREFIX.get(item["category"], item["category"].capitalize())
        title = f"[{prefix}] {item['source_name']}"
        summary = (item["raw_summary"] or "")[:140].strip()
        message = f"{item['title']}\n\n{summary}" if summary else item["title"]

        try:
            await self._send(title=title, message=message, url=item["url"])
            state.mark_item_notified(item_id=item["id"])
        except Exception as e:  # noqa: BLE001 — per-notification failures shouldn't abort batch
            logger.warning("notification send failed for item %s: %s", item["id"], e)
