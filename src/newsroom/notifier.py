"""macOS push notifications for high-importance items."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Notification category/label prefix
TITLE_PREFIX = {
    "ai": "AI",
    "world": "Welt",
}

# Custom notification sound (file in ~/Library/Sounds/<NAME>.aiff).
# Set to "default" to use the macOS system default.
NOTIFICATION_SOUND = "newsroom-ping"

QUIET_HOUR_START = 22
QUIET_HOUR_END = 7

# Spec §4.3: suppress duplicate pushes in the same category within this window.
BUNDLING_WINDOW_MINUTES = 15

# (category, subcategory) → (day_threshold, quiet_threshold)
# Resolution order: exact match → (category, "*") wildcard → DEFAULT_THRESHOLD.
# arxiv (imp=5 only) was the 7d90fbc if-clause; it is now a regular row.
NOTIFICATION_THRESHOLDS: dict[tuple[str, str], tuple[int, int]] = {
    ("world", "breaking"): (3, 4),
    ("world", "*"): (4, 5),  # news, analysis
    ("ai", "arxiv"): (5, 5),  # paradigm-shifting only
    ("ai", "*"): (4, 5),
}
DEFAULT_THRESHOLD: tuple[int, int] = (4, 5)


def compute_threshold_for_hour(
    hour: int,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> int:
    """Effective importance threshold for pushing a notification.

    Resolves via NOTIFICATION_THRESHOLDS — specific (category, subcategory)
    first, then (category, "*") wildcard, then DEFAULT_THRESHOLD. Unknown
    categories push at Phase-1 default until they get an explicit table row.
    """
    table = (
        NOTIFICATION_THRESHOLDS.get((category or "", subcategory or ""))
        or NOTIFICATION_THRESHOLDS.get((category or "", "*"))
        or DEFAULT_THRESHOLD
    )
    day, quiet = table
    quiet_hours = hour >= QUIET_HOUR_START or hour < QUIET_HOUR_END
    return quiet if quiet_hours else day


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
    pync.notify(message, title=title, open=open_url or "", sound=NOTIFICATION_SOUND)


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
            state.mark_item_notified(item_id=item["id"], pushed=False)
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

    async def notify_digest_ready(
        self,
        *,
        category_label: str,
        item_count: int,
        file_path: Path | str,
    ) -> None:
        """System-Notification bei fertigem Kategorie-Digest.

        Anders als `maybe_notify`: keine Threshold-, Quiet-Hour- oder Bundling-Logik.
        Digests sind System-Events, keine Item-Pushes — selten genug (max 2/Tag pro
        Kategorie), und ein gewünschtes Resultat-Signal. Eine Notification je
        nicht-leerer Kategorie; `file_path` ist die zugehörige Kategorie-Datei.

        `file_path` wird als `file://`-URI an `terminal-notifier -open` übergeben,
        damit ein Klick auf das Banner die MD-Datei im Default-Handler öffnet
        (Path muss absolut sein — die Pfade aus `digester` sind das).
        """
        title = "Newsroom"
        message = f"{category_label}-Digest bereit ({item_count} Items)"
        url = Path(file_path).as_uri()
        try:
            await self._send(title=title, message=message, url=url)
        except Exception as e:  # noqa: BLE001 — best-effort; never abort the digest
            logger.warning("digest notification send failed: %s", e)
