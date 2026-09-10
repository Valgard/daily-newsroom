"""Morning/evening digest generation via Opus."""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from datetime import date as _date
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from newsroom.agent_client import AgentClient, ParseError
from newsroom.logging_setup import LOG_DIR

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
# Top-level category → digest H1 category label. Unknown categories fall back to
# `.capitalize()` at the call site (Phase-2b/3 readiness: `dresden` → "Dresden").
CATEGORY_LABEL = {"world": "Weltgeschehen", "ai": "AI/LLM/ML"}

# Degraded-render banners. The two causes stay distinguishable on purpose: a malformed
# response is not an outage, and reporting it as one sends every later diagnosis down
# the wrong path — which is exactly what the single old banner did.
BANNER_UNREACHABLE = (
    "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus war nicht erreichbar)"
)
BANNER_UNPARSEABLE = (
    "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus-Antwort war nicht verwertbar)"
)
# A third cause with its own wording: the response arrived and parsed, it just carried
# no content for any item in this file. Seen in production on 2026-07-09, when a parsed
# response held content for 0 of 125 items and produced no banner at all.
BANNER_NO_CONTENT = (
    "⚠️ Automatisch generiert (ohne LLM-Zusammenfassung — Opus-Antwort enthielt keine Item-Inhalte)"
)
# Per-item counterpart, for when only some items are missing content. A banner would
# claim the whole file is degraded; this says which item is.
DEGRADED_ITEM_MARKER = "⚠️ ohne LLM-Zusammenfassung"

# Unparseable responses are dumped here in full; the log message only carries a prefix.
PARSE_FAILURE_DIR = LOG_DIR / "parse-failures"


def _format_top_header(date: _date, slot: str, category: str) -> str:
    """Canonical digest H1: '# News-Digest <date_de> — <Category> (Morgen|Abend)'.

    Single source of truth for the H1 across every render path. The category
    label resolves via CATEGORY_LABEL (unknown → `.capitalize()`).
    """
    date_de = date.strftime("%-d. %B %Y")
    label = CATEGORY_LABEL.get(category, category.capitalize())
    return f"# News-Digest {date_de} — {label} ({SLOT_LABEL_DE[slot]})"


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


def _render_item(  # noqa: ANN001
    item,
    content: dict,
    cross_link: Path | None,
    now: datetime,
    *,
    mark_degraded: bool = False,
) -> str:
    """Render one digest entry deterministically from a DB row + LLM content."""
    parts = [f"## {content['headline']}", "- [ ] interessiert mich"]
    prose = (content.get("prose") or "").strip()
    if prose:
        parts.append(prose)
    quote = (content.get("quote") or "").strip()
    if quote:
        parts.append(f"› {quote}")
    reltime = _relative_time(item["published_at"], now)
    meta_bits = [item["source_name"], reltime, f"Importance {item['importance']}"]
    if mark_degraded:
        meta_bits.append(DEGRADED_ITEM_MARKER)
    meta = f"[Weiterlesen →]({item['url']}) · *{' · '.join(meta_bits)}*"
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
# Requires a letter, slash, `!` or `?` after the `<`, so a bare comparison operator
# ("gilt wenn a < b") stays text instead of being eaten as far as the next `>`.
_HTML_TAG_RE = re.compile(r"<[a-zA-Z/!?][^>]*>")
# A markdown sigil in first position, exposed once the tags around it are gone.
_LEADING_SIGIL_RE = re.compile(r"^(?:([#>|+*-])|(\d+)([.)]))")


def _usable_contents(raw_items) -> dict[int, dict]:  # noqa: ANN001
    """Index LLM item content by DB id, keeping only entries that can be rendered.

    Membership in this dict is what marks an item healthy, so it has to mean
    *renderable*, not merely *present*. An entry with a blank or missing headline
    would render an empty H2 and throw the DB fallback away — the silent
    degradation this module exists to surface — or raise KeyError mid-render, after
    the digest slot is already claimed and therefore lost until someone runs --force.

    A quote counts as body text; there is no reason to discard a real headline just
    because the model put its text in the other field.
    """
    usable: dict[int, dict] = {}
    for content in raw_items:
        if not isinstance(content, dict):
            continue
        try:
            item_id = int(content["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not str(content.get("headline") or "").strip():
            continue
        if not (str(content.get("prose") or "") + str(content.get("quote") or "")).strip():
            continue
        usable[item_id] = content
    return usable


def _plain_text(raw: str) -> str:
    """Feed HTML to readable plain text. Not a sanitiser — nothing is rendered here.

    Order matters: tags go first, so that text the author escaped on purpose
    (``&lt;p&gt;``) survives unescaping instead of being mistaken for markup and
    dropped. Tags become spaces rather than vanishing, or ``<p>A</p><p>B</p>``
    would read as ``AB``.
    """
    text = " ".join(html.unescape(_HTML_TAG_RE.sub(" ", raw)).split())
    # Escape a leading sigil: the code owns every bit of markdown structure, and a
    # feed paragraph starting "# 1 Grund" would otherwise open a heading mid-file.
    return _LEADING_SIGIL_RE.sub(
        lambda m: f"\\{m.group(1)}" if m.group(1) else f"{m.group(2)}\\{m.group(3)}",
        text,
        count=1,
    )


def _content_for(item, contents_by_id: dict[int, dict]) -> dict:  # noqa: ANN001
    """LLM content for an item, or a degraded fallback from the DB row.

    A missing id (item absent from the LLM JSON) renders from the row:
    headline = title, prose = raw_summary truncated to ITEM_BODY_MAX_CHARS.

    The truncation runs *after* stripping markup. Most feeds deliver near-plain
    text, but a few — Reddit and The Batch above all — wrap every summary in nested
    ``<a href=...>`` markup that accounts for about half its length; cutting first
    would spend the budget on tags instead of on article text.
    """
    content = contents_by_id.get(item["id"])
    if content is None:
        body = _plain_text(item["raw_summary"] or "")[:ITEM_BODY_MAX_CHARS]
        content = {"headline": item["title"], "prose": body}
    return content


def _render_digest(
    items,  # noqa: ANN001
    contents_by_id: dict[int, dict],
    cross_links: dict[int, Path | None],
    *,
    slot: str,
    date: _date,
    now: datetime,
    category: str,
    banner: str | None = None,
) -> str:
    """Render one category's digest body (no trailing newline, no separator).

    All items must share `category`; the caller partitions before calling.

    A banner and the per-item markers are mutually exclusive: under a banner every
    item is degraded already, and repeating that per item would bury the one line
    that says it.
    """
    parts = [_format_top_header(date, slot, category)]
    if banner:
        parts.append(banner)
    for item in items:
        content = _content_for(item, contents_by_id)
        degraded = item["id"] not in contents_by_id
        parts.append(
            _render_item(
                item,
                content,
                cross_links.get(item["id"]),
                now,
                mark_degraded=degraded and not banner,
            )
        )
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


def _category_file(target_dir: Path, date: _date, category: str) -> Path:
    """Per-category, per-day digest file: '<target_dir>/<date>_<category>.md'."""
    return target_dir / f"{date.isoformat()}_{category}.md"


def _paths_from_record(raw: str | None, fallback: list[Path]) -> list[Path]:
    """Parse a digest row's `file_path` (JSON array) back into Paths.

    Tolerates a legacy single-path string (pre-split rows). Empty/blank value
    (claimed but not yet finalized) → caller's fallback ([] when no file yet).
    """
    if not raw:
        return fallback
    try:
        return [Path(p) for p in json.loads(raw)]
    except (json.JSONDecodeError, TypeError):
        return [Path(raw)]


def _dump_parse_failure(raw: str, *, slot: str, date: _date) -> Path | None:
    """Persist the full unparseable response for diagnosis. Best-effort by design.

    Never raises: returns the written path, or None when there was nothing to write
    or the write failed. Callers run inside an exception handler after the digest
    slot is claimed, so anything escaping here would leave that slot claimed but
    unfinalised, and every later run would skip it.

    One seam in that promise: the warning below formats its argument, and
    RichHandler formats outside its own try/except. Log the exception and nothing
    richer — an object with a raising __str__ would escape after all.
    """
    if not raw:
        return None
    try:
        PARSE_FAILURE_DIR.mkdir(parents=True, exist_ok=True)
        # UTC, matching the log timestamps that point at this file. The
        # "never UTC" invariant covers user-facing time; a dump is log infrastructure.
        stamp = datetime.now(UTC).strftime("%H%M%S")
        path = PARSE_FAILURE_DIR / f"{date.isoformat()}-{slot}-{stamp}.txt"
        # errors="replace": a malformed response is exactly where a lone surrogate
        # turns up, and an unwritable character must not cost the digest.
        path.write_text(raw, encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001 — diagnostics must never outrank the digest
        # Deliberately broad: this runs after claim_digest_slot, so anything escaping
        # here leaves the slot claimed-but-unfinalised and every later run skips it.
        logger.warning("could not write parse-failure dump: %s", e)
        return None
    return path


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
) -> list[Path]:
    """Generate digest for the given slot+date. Returns list of written category files."""
    if agent is None:
        agent = AgentClient()  # noqa: PLC0415
    if summaries_dir is None:
        summaries_dir = Path.home() / "Documents" / "!AI" / "article_summaries"

    target_dir = output_root / f"{date.year:04d}" / f"{date.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)

    existing = state.get_digest(date.isoformat(), slot)
    if existing and not force:
        logger.info("digest already exists for %s/%s, skipping", date, slot)
        return _paths_from_record(existing["file_path"], [])
    if existing and force:
        logger.info("--force: clearing previous digest for %s/%s", date, slot)
        label = f"{date.isoformat()}-{slot}"
        state.unmark_items_by_digest_label(label)
        state.delete_digest(date=date.isoformat(), slot=slot)

    if not state.claim_digest_slot(date=date.isoformat(), slot=slot, model=DIGEST_MODEL):
        logger.info("digest slot %s/%s claimed concurrently, skipping without Opus", date, slot)
        return []

    cutoff = _cutoff_for_slot(slot, state)
    items = state.list_items_for_digest(since_iso=cutoff)

    if not items:
        state.finalize_digest(
            date=date.isoformat(),
            slot=slot,
            file_path=json.dumps([]),
            item_count=0,
        )
        logger.info(
            "digest finalized (empty)",
            extra={"event": "digest_finalized", "slot": slot, "item_count": 0},
        )
        return []

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
        contents_by_id = _usable_contents(result.get("items", []))
    except ParseError as e:
        logger.warning("Opus response unparseable after retries, using degraded render: %s", e)
        dump_path = _dump_parse_failure(e.raw_response, slot=slot, date=date)
        if dump_path is not None:
            logger.warning("full unparseable response saved to %s", dump_path)
        banner = BANNER_UNPARSEABLE
    except Exception as e:  # noqa: BLE001
        logger.warning("Opus digest call failed, using degraded render: %s", e)
        banner = BANNER_UNREACHABLE

    if banner is None:
        matched = sum(1 for item in items if item["id"] in contents_by_id)
        if matched != len(items):
            logger.warning(
                "digest content mismatch: %d/%d items have LLM content, rest degraded",
                matched,
                len(items),
            )

    # Partition items by top-level category, preserving SQL order (world first).
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item["source_category"], []).append(item)

    written: list[Path] = []
    for category, cat_items in groups.items():
        # Classify per category, not per run: each category is its own file, so a run
        # that is globally partial can hold one healthy file and one entirely without
        # content. A global verdict would mislabel whichever of the two it got wrong.
        cat_banner = banner
        if cat_banner is None and not any(i["id"] in contents_by_id for i in cat_items):
            logger.warning(
                "no LLM content for any %s item, banner set",
                category,
                extra={"event": "digest_category_no_content", "category": category},
            )
            cat_banner = BANNER_NO_CONTENT
        content = _render_digest(
            cat_items,
            contents_by_id,
            cross_links,
            slot=slot,
            date=date,
            now=now,
            category=category,
            banner=cat_banner,
        )
        path = _category_file(target_dir, date, category)
        _write_digest_file(path, content, slot=slot, force=force)
        written.append(path)

    for item in items:
        state.mark_item_digested(
            item_id=item["id"],
            digest_label=f"{date.isoformat()}-{slot}",
        )
    state.finalize_digest(
        date=date.isoformat(),
        slot=slot,
        file_path=json.dumps([str(p) for p in written]),
        item_count=len(items),
    )
    logger.info(
        "digest finalized",
        extra={"event": "digest_finalized", "slot": slot, "item_count": len(items)},
    )

    if notifier is not None:
        for category, cat_items in groups.items():
            category_label = CATEGORY_LABEL.get(category, category.capitalize())
            logger.info(
                "digest notify dispatched",
                extra={
                    "event": "digest_notify_dispatched",
                    "slot": slot,
                    "category": category,
                    "item_count": len(cat_items),
                },
            )
            await notifier.notify_digest_ready(
                category_label=category_label,
                item_count=len(cat_items),
                file_path=_category_file(target_dir, date, category),
            )
    return written


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
