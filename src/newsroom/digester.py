"""Morning/evening digest generation via Opus."""

from __future__ import annotations

import logging
import re
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from newsroom.agent_client import AgentClient

if TYPE_CHECKING:
    from newsroom.notifier import Notifier

logger = logging.getLogger(__name__)


BERLIN_TZ = ZoneInfo("Europe/Berlin")
DIGEST_MODEL = "claude-opus-4-7"
DEFAULT_OUTPUT_ROOT = Path.home() / "Documents" / "!AI" / "news"

MORNING_START_HOUR = 5
MORNING_END_HOUR = 12  # exclusive
EVENING_START_HOUR = 16
EVENING_END_HOUR = 24  # exclusive

SLOT_LABEL_DE = {"morning": "Morgen", "evening": "Abend"}
# Top-level category → digest H2 label. Unknown categories fall back to
# `.capitalize()` at the call site (Phase-2b/3 readiness: `dresden` → "Dresden").
CATEGORY_LABEL = {"world": "Weltgeschehen", "ai": "AI/LLM/ML"}


def _format_top_header(date: _date, slot: str) -> str:
    """Canonical digest top-header: ``# News-Digest <date_de> (Morgen|Abend)``.

    Single source of truth for the H1 across all rendering paths: the main
    render (`_render_digest`), the empty-digest branch, and the degraded
    LLM-outage path.
    """
    date_de = date.strftime("%-d. %B %Y")
    return f"# News-Digest {date_de} ({SLOT_LABEL_DE[slot]})"


def _relative_time(published_at: str, now: datetime) -> str:
    """Render an item's publish time relative to ``now``, in Europe/Berlin.

    today -> ``heute HH:MM``; yesterday -> ``gestern HH:MM``;
    older  -> ``DD.MM. HH:MM``.
    """
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(BERLIN_TZ)
    today = now.astimezone(BERLIN_TZ).date()
    day = dt.date()
    hm = dt.strftime("%H:%M")
    if day == today:
        return f"heute {hm}"
    if day == today - timedelta(days=1):
        return f"gestern {hm}"
    return f"{dt.strftime('%d.%m.')} {hm}"


def _render_item(item, content: dict, cross_link: Path | None, now: datetime) -> str:  # noqa: ANN001
    """Render one digest entry deterministically from a DB row + LLM content."""
    parts = [f"### {content['headline']}", "- [ ] interessiert mich"]
    prose = (content.get("prose") or "").strip()
    if prose:
        parts.append(prose)
    quote = (content.get("quote") or "").strip()
    if quote:
        parts.append(f"› {quote}")
    reltime = _relative_time(item["published_at"], now)
    meta = (
        f"[Weiterlesen →]({item['url']}) · "
        f"*{item['source_name']} · {reltime} · Importance {item['importance']}*"
    )
    if cross_link is not None:
        meta += f" · 📄 [Tief-Zusammenfassung]({cross_link})"
    parts.append(meta)
    return "\n\n".join(parts)


class NoSlotError(Exception):
    """Current hour is outside morning and evening windows."""


def determine_slot(now: datetime | None = None) -> str:
    now = now or datetime.now(BERLIN_TZ)
    if MORNING_START_HOUR <= now.hour < MORNING_END_HOUR:
        return "morning"
    if EVENING_START_HOUR <= now.hour < EVENING_END_HOUR:
        return "evening"
    raise NoSlotError(f"hour {now.hour} is not within morning or evening windows")


def _cutoff_for_slot(slot: str, state) -> str:  # noqa: ANN001
    """ISO timestamp: items scored AFTER this are eligible."""
    # Last digest of the OTHER slot defines the cutoff
    other = "evening" if slot == "morning" else "morning"
    last = state.get_last_digest_generated_at(other)
    if last:
        return last
    # No prior digest: use 24h ago
    return (datetime.now(BERLIN_TZ) - timedelta(days=1)).isoformat()


def _resolve_cross_link(item_url: str, summaries_dir: Path) -> Path | None:
    """Check if an article_summaries file exists matching this URL."""
    if not summaries_dir.exists():
        return None
    for f in summaries_dir.glob("*.md"):
        # cheap check: URL appears in the file
        try:
            if item_url in f.read_text(errors="replace"):
                return f
        except OSError:
            continue
    return None


ITEM_BODY_MAX_CHARS = 1200


def _render_digest(
    items,  # noqa: ANN001
    contents_by_id: dict[int, dict],
    cross_links: dict[int, Path | None],
    *,
    slot: str,
    date: _date,
    now: datetime,
    banner: str | None = None,
) -> str:
    """Render a full digest body (no trailing newline, no evening separator)."""
    parts = [_format_top_header(date, slot)]
    if banner:
        parts.append(banner)
    current_category: str | None = None
    for item in items:
        cat = item["source_category"]
        if cat != current_category:
            parts.append(f"## {CATEGORY_LABEL.get(cat, cat.capitalize())}")
            current_category = cat
        content = contents_by_id.get(item["id"])
        if content is None:
            body = (item["raw_summary"] or "").strip().replace("\n", " ")[:ITEM_BODY_MAX_CHARS]
            content = {"headline": item["title"], "prose": body}
        parts.append(_render_item(item, content, cross_links.get(item["id"]), now))
    return "\n\n".join(parts)


def format_items_for_prompt(items) -> str:  # noqa: ANN001
    """Render items as a flat markdown payload for the digest LLM, inserting a
    `## {CategoryLabel}` H2 on each category-change boundary. Items must arrive
    pre-sorted by SQL; this function does not re-sort. Each item line carries the
    DB `id` so the LLM can key its returned content back to the row.
    """
    lines: list[str] = []
    current_category: str | None = None
    for item in items:
        cat = item["source_category"]
        if cat != current_category:
            if current_category is not None:
                lines.append("")
            label = CATEGORY_LABEL.get(cat, cat.capitalize())
            lines.append(f"## {label}")
            lines.append("")
            current_category = cat
        lines.append(
            f"- id={item['id']} [{item['importance']}] {item['title']} · "
            f"{item['source_name']} · published={item['published_at']} · "
            f"reason={item['score_reason']}"
        )
        body = (item["raw_summary"] or "").strip().replace("\n", " ")
        if body:
            lines.append(f"  > {body[:ITEM_BODY_MAX_CHARS]}")
    return "\n".join(lines)


async def generate_digest(  # noqa: PLR0912, PLR0915
    *,
    state,  # noqa: ANN001
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
    notifier: Notifier | None = None,
) -> Path:
    """Generate digest for the given slot+date. Returns path of written file."""
    if agent is None:
        agent = AgentClient()  # noqa: PLC0415
    if summaries_dir is None:
        summaries_dir = Path.home() / "Documents" / "!AI" / "article_summaries"

    target_dir = output_root / f"{date.year:04d}" / f"{date.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{date.isoformat()}.md"

    existing = state.get_digest(date.isoformat(), slot)
    if existing and not force:
        logger.info("digest already exists for %s/%s, skipping", date, slot)
        # Claimed but not yet finalized → file_path is empty; return the
        # target path the winner will write to.
        return Path(existing["file_path"] or str(target_file))
    if existing and force:
        # Clear previous digest's item-marks + DB row so regeneration is clean.
        logger.info("--force: clearing previous digest for %s/%s", date, slot)
        label = f"{date.isoformat()}-{slot}"
        state.unmark_items_by_digest_label(label)
        state.delete_digest(date=date.isoformat(), slot=slot)

    # Atomic slot claim before any expensive work. If another process won the
    # race between get_digest() above and this claim, we abort without calling
    # Opus — the winner will write the file.
    if not state.claim_digest_slot(date=date.isoformat(), slot=slot, model=DIGEST_MODEL):
        logger.info("digest slot %s/%s claimed concurrently, skipping without Opus", date, slot)
        return target_file

    cutoff = _cutoff_for_slot(slot, state)
    items = state.list_items_for_digest(since_iso=cutoff)

    if not items:
        # No items → write/append placeholder, still finalize digest
        top = _format_top_header(date, slot)
        if slot == "morning":
            content = f"{top}\n\n_Keine neuen Items seit dem letzten Digest._\n"
        else:
            content = f"\n\n---\n\n{top}\n\n_Keine neuen Items seit Morgen-Digest._\n"
        _write_digest_file(target_file, content, slot=slot, force=force)
        state.finalize_digest(
            date=date.isoformat(),
            slot=slot,
            file_path=str(target_file),
            item_count=0,
        )
        logger.info(
            "digest finalized (empty)",
            extra={"event": "digest_finalized", "slot": slot, "item_count": 0},
        )
        return target_file

    items_md = format_items_for_prompt(items)
    date_de = date.strftime("%-d. %B %Y")  # "19. April 2026" on macOS/Linux
    now = datetime.now(BERLIN_TZ)
    cross_links = {item["id"]: _resolve_cross_link(item["url"], summaries_dir) for item in items}

    banner: str | None = None
    contents_by_id: dict[int, dict] = {}
    try:
        result = await agent.ask(
            prompt_name=f"digest_{slot}",
            variables={"items_markdown": items_md, "date_de": date_de},
            model=DIGEST_MODEL,
            parse="json",
        )
        contents_by_id = {int(c["id"]): c for c in result.get("items", []) if "id" in c}
    except Exception as e:  # noqa: BLE001
        logger.warning("Opus digest call failed, using degraded render: %s", e)
        banner = "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)"

    if banner is None:
        matched = sum(1 for item in items if item["id"] in contents_by_id)
        if matched != len(items):
            logger.warning(
                "digest content mismatch: %d/%d items have LLM content, rest degraded",
                matched,
                len(items),
            )

    content = _render_digest(
        items,
        contents_by_id,
        cross_links,
        slot=slot,
        date=date,
        now=now,
        banner=banner,
    )
    _write_digest_file(target_file, content, slot=slot, force=force)

    for item in items:
        state.mark_item_digested(
            item_id=item["id"],
            digest_label=f"{date.isoformat()}-{slot}",
        )
    state.finalize_digest(
        date=date.isoformat(),
        slot=slot,
        file_path=str(target_file),
        item_count=len(items),
    )
    logger.info(
        "digest finalized",
        extra={"event": "digest_finalized", "slot": slot, "item_count": len(items)},
    )
    if notifier is not None:
        logger.info(
            "digest notify dispatched",
            extra={
                "event": "digest_notify_dispatched",
                "slot": slot,
                "item_count": len(items),
            },
        )
        await notifier.notify_digest_ready(
            slot=slot,
            item_count=len(items),
            file_path=target_file,
        )
    return target_file


# Matches the canonical evening top-header line: '# News-Digest <date_de> (Abend)'.
# Used to locate the evening section in an existing file for --force regeneration.
# Morning header has '(Morgen)' so the two slots are unambiguously distinguishable.
_EVENING_HEADER_RE = re.compile(r"^# News-Digest [^\n]*\(Abend\)", re.MULTILINE)


def _find_evening_section_start(text: str) -> int:
    """Index of the start of the LAST evening top-header line, or -1 if absent."""
    last_idx = -1
    for m in _EVENING_HEADER_RE.finditer(text):
        last_idx = m.start()
    return last_idx


def _write_digest_file(path: Path, content: str, *, slot: str, force: bool = False) -> None:
    """Write or append digest content. Morning creates; evening appends.

    With `force=True` and an existing file, an evening regeneration truncates
    the previous evening section (located via `_find_evening_section_start`)
    before appending the new one, so the morning section is preserved but the
    old evening is replaced instead of duplicated. Morning regenerations
    always overwrite the whole file.
    """
    body = content if content.endswith("\n") else content + "\n"
    if slot == "morning" or not path.exists():
        path.write_text(body)
        return

    existing = path.read_text()
    if force:
        # Drop everything from the last evening header onward so a new
        # evening section takes its place. Also strip any trailing '---'
        # separator that belonged to the old evening.
        idx = _find_evening_section_start(existing)
        if idx >= 0:
            existing = existing[:idx].rstrip()
            # Strip a dangling horizontal rule left from the old separator
            existing = existing.rstrip("-").rstrip()
            existing += "\n"
    separator = "\n\n---\n\n" if not existing.endswith("\n---\n\n") else ""
    path.write_text(existing + separator + body)
