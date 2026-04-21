"""Morning/evening digest generation via Opus."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from newsroom.agent_client import AgentClient

logger = logging.getLogger(__name__)


BERLIN_TZ = ZoneInfo("Europe/Berlin")
DIGEST_MODEL = "claude-opus-4-7"
DEFAULT_OUTPUT_ROOT = Path.home() / "Documents" / "!AI" / "news"

MORNING_START_HOUR = 5
MORNING_END_HOUR = 12  # exclusive
EVENING_START_HOUR = 16
EVENING_END_HOUR = 24  # exclusive


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


def format_items_for_prompt(items, summaries_dir: Path | None = None) -> str:  # noqa: ANN001
    """Group items by subcategory and produce the markdown payload for the prompt.

    Each item renders as:
        - [importance] title · source · published=iso · url=url · reason=reason
          > body (truncated to ITEM_BODY_MAX_CHARS)

    `published_at` is passed so the digest prompt can express relative time
    ("heute 14:30", "gestern", "vor 3h") without additional state injection.
    """
    groups: dict[str, list] = defaultdict(list)
    for item in items:
        groups[item["source_subcategory"] or "other"].append(item)

    lines: list[str] = []
    for subcat in sorted(groups):
        lines.append(f"### {subcat}")
        for item in sorted(groups[subcat], key=lambda x: (-x["importance"], x["title"])):
            summary_link = ""
            if summaries_dir is not None:
                link = _resolve_cross_link(item["url"], summaries_dir)
                if link:
                    summary_link = f" | summary_path={link}"
            lines.append(
                f"- [{item['importance']}] {item['title']} · {item['source_name']} · "
                f"published={item['published_at']} · url={item['url']} · "
                f"reason={item['score_reason']}{summary_link}"
            )
            body = (item["raw_summary"] or "").strip().replace("\n", " ")
            if body:
                lines.append(f"  > {body[:ITEM_BODY_MAX_CHARS]}")
        lines.append("")
    return "\n".join(lines)


async def generate_digest(
    *,
    state,  # noqa: ANN001
    slot: str,
    date: _date,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    summaries_dir: Path | None = None,
    agent: AgentClient | None = None,
    force: bool = False,
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
        if slot == "morning":
            content = (
                f"# News-Digest {date.strftime('%d.%m.%Y')} ({slot.capitalize()})\n\n"
                "_Keine neuen Items seit dem letzten Digest._\n"
            )
        else:
            content = "\n\n---\n\n## Abend-Digest\n\n_Keine neuen Items seit Morgen-Digest._\n"
        _write_digest_file(target_file, content, slot=slot, force=force)
        state.finalize_digest(
            date=date.isoformat(),
            slot=slot,
            file_path=str(target_file),
            item_count=0,
        )
        return target_file

    items_md = format_items_for_prompt(items, summaries_dir=summaries_dir)
    date_de = date.strftime("%-d. %B %Y")  # "19. April 2026" on macOS/Linux

    try:
        content = await agent.ask(
            prompt_name=f"digest_{slot}",
            variables={"items_markdown": items_md, "date_de": date_de},
            model=DIGEST_MODEL,
            parse="text",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Opus digest call failed, using fallback: %s", e)
        content = _build_fallback_digest(items, slot=slot, date=date)

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
    return target_file


_EVENING_SECTION_MARKER = "## Abend-Digest"


def _write_digest_file(path: Path, content: str, *, slot: str, force: bool = False) -> None:
    """Write or append digest content. Morning creates; evening appends.

    With `force=True` and an existing file, an evening regeneration truncates
    the previous `## Abend-Digest` section before appending the new one, so
    the morning section is preserved but the old evening is replaced instead
    of duplicated. Morning regenerations always overwrite the whole file.
    """
    body = content if content.endswith("\n") else content + "\n"
    if slot == "morning" or not path.exists():
        path.write_text(body)
        return

    existing = path.read_text()
    if force:
        # Drop everything from the last '## Abend-Digest' onward so a new
        # evening section takes its place. Also strip any trailing '---'
        # separator that belonged to the old evening.
        idx = existing.rfind(_EVENING_SECTION_MARKER)
        if idx >= 0:
            existing = existing[:idx].rstrip()
            # Strip a dangling horizontal rule left from the old separator
            existing = existing.rstrip("-").rstrip()
            existing += "\n"
    separator = "\n\n---\n\n" if not existing.endswith("\n---\n\n") else ""
    path.write_text(existing + separator + body)


def _build_fallback_digest(items, *, slot: str, date: _date) -> str:  # noqa: ANN001
    """Emergency digest: no LLM synthesis, just a structured list."""
    if slot == "morning":
        header = (
            f"# News-Digest {date.strftime('%d.%m.%Y')} ({slot.capitalize()})\n\n"
            "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)\n\n"
        )
    else:
        header = (
            "\n\n---\n\n## Abend-Digest\n\n"
            "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)\n\n"
        )

    groups: dict[str, list] = defaultdict(list)
    for item in items:
        groups[item["source_subcategory"] or "other"].append(item)

    lines = [header]
    for subcat in sorted(groups):
        lines.append(f"## {subcat.capitalize()}\n")
        for item in sorted(groups[subcat], key=lambda x: (-x["importance"], x["title"])):
            lines.append(
                f"- **[Importance {item['importance']}]** "
                f"[{item['title']}]({item['url']}) · {item['source_name']}"
            )
        lines.append("")
    lines.append(
        "\n_Generiert ohne LLM-Synthese. Bei Bedarf Kommando "
        "`newsroom digest --force` manuell erneut ausführen._\n"
    )
    return "\n".join(lines)
