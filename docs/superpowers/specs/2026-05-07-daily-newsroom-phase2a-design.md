# Daily Newsroom — Phase 2a (Weltgeschehen) Design Spec

**Status:** Draft (awaiting user approval)
**Date:** 2026-05-07
**Author:** Sven Pöche (via brainstorming with Claude)
**Type:** Design Spec for Phase 2a — Weltgeschehen extension to the daily-newsroom agent
**Project Root:** `/Users/valgard/Projects/private/daily-newsroom/`
**Prior Spec:** `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md` (Phase 1, shipped)

---

## 1. Context & Goals

### 1.1 Problem

Phase 1 of daily-newsroom is live (merged ~2026-04-21) and covers the AI/LLM/ML topic stream end-to-end: 15 sources, twice-daily digests, push notifications, cross-linking to `~/Documents/!AI/article_summaries/`. Spec §10.1 of the Phase-1 design earmarked Weltgeschehen and Dresden as the next two topic streams. This spec covers **Phase 2a — Weltgeschehen only**. Phase 2b (Dresden) gets its own spec later.

### 1.2 Phase 2a is a mechanism step, not a feature buildout

The central deliverable is **not** "world news appear in the digest from now on" — it is "the newsroom can run multiple thematic domains side by side". Phase 2b (Dresden) and Phase 3 (Tech, science, APOD) build on the mechanism without touching core code.

### 1.3 Phase-1 baseline this spec assumes

Phase-1 commits since 2026-04-21 that shape the Phase-2a starting state:

- **`7d90fbc feat(notifier): allow arxiv push at imp=5`** — `compute_threshold_for_hour` now has an arxiv special case: `if subcategory == "arxiv": return THRESHOLD_QUIET` (i.e. 5). Effective Phase-1 thresholds are `(ai, arxiv) → (5, 5)` and `(ai, *) → (4, 5)`.
- **`f191674 refactor(digest): drop subcategory headers, flatten output`** — removed the `## subcat`-mapping rules from the digest prompts and the `## {subcat.capitalize()}` headers from the LLM-outage fallback. Digest output is a single prose stream per slot, not a multi-section structure.
- **`a88cac1 refactor(digest): consolidate sort to SQL`** — sort is SQL-only (`ORDER BY importance DESC, published_at DESC, title COLLATE NOCASE ASC`). `format_items_for_prompt` is strict pass-through; no Python re-sort, no Python re-group.
- **`14c082b fix(digest): align evening header to H1, symmetric to morning`** — **prerequisite.** Both slots now produce a `# News-Digest <date_de> (Morgen|Abend)` H1; the evening section is no longer an H2 nested under the morning. Phase 2a builds the Welt/AI category sections as H2 under this slot-H1.

### 1.4 Goals

- **Second top-level category `world`** alongside the existing `ai`. Both flow through the same fetch → score → digest → notify pipeline, but with their own scoring prompt and notifier characteristic.
- **5–7 new sources** in three subcategories: `breaking` (curated breaking-news feeds), `news` (mainstream top-news), `analysis` (in-depth / commentary).
- **Extended digest layout:** within each slot's existing H1 (`# News-Digest <date_de> (Morgen|Abend)`), insert H2 sections `## Weltgeschehen` (top) and `## AI/LLM/ML` (below). Items inside each H2 stay flat prose, consistent with the post-`f191674` newsletter style.
- **Subcategory-driven push threshold:** `breaking` items push at importance ≥3 (day) / ≥4 (quiet); everything else as in Phase 1 (≥4 / ≥5). The Phase-1 `(ai, arxiv) → (5, 5)` special case is preserved as a regular table entry.
- **Phase-2b-ready architecture:** a future Dresden category should be additive — one new prompt file plus a YAML edit, one entry in the category-display-order constant and (optionally) one entry in the notifier threshold table.

### 1.5 Non-Goals (Phase 2a only)

- **No Dresden sources** — separate Phase 2b.
- **No new output paths** — same `~/Documents/!AI/news/YYYY/MM/YYYY-MM-DD.md`.
- **No `digests` schema change** — `UNIQUE(date, slot)` stays; one digest row per slot covers both categories.
- **No launchd plist changes** — same three plists as Phase 1.
- **No cross-category dedup** — if both Reuters and tagesschau publish the same story, both surface (same as today when multiple AI sources cover the same model release). Cross-category dedup is its own future phase.
- **No closure of existing AI sources** — Phase 1 sources stay untouched.
- **No burst cap** for Welt notifications — existing 15-min per-category bundling is the only volume guardrail in Phase 2a (see §6.5).
- **No revival of `### subcat` headers in the digest output** — `f191674` removed them deliberately. Phase 2a only re-introduces structure at the **category** level (H2), not the subcategory level.

---

## 2. Architecture Extension

The Phase-1 invariants (§3.3 of the prior spec) hold: modules communicate only via the state DB, launchd-invoked commands stay argumentless, every transition is a SQLite transaction, idempotency is sacred. Phase 2a is purely additive plus targeted in-place edits.

### 2.1 Data Flow

```
sources.yaml  (15 AI + 5–7 Welt)
   ├─ category: ai     · subcategory: arxiv|lab|curated|community|claude-code
   └─ category: world  · subcategory: breaking|news|analysis
            │
            ▼
fetcher.py  (UNCHANGED — already propagates source.category/subcategory)
            │
            ▼
scorer.py   (1-line change: route prompt by category)
   prompt_name = f"score_item_{item['source_category']}"
            │           │
   ┌────────┘           └────────┐
   ▼                             ▼
score_item_ai.md           score_item_world.md
(Phase-1 prompt,           (NEW — geopolitical
 renamed)                   calibration scale)
            │
            ▼
notifier.py  (NOTIFICATION_THRESHOLDS table replaces hardcoded ints +
              special-case if-clause; arxiv special case becomes a row)
   ('world', 'breaking') → (3, 4)
   ('world', '*')        → (4, 5)
   ('ai',    'arxiv')    → (5, 5)   # was: hardcoded if-clause from 7d90fbc
   ('ai',    '*')        → (4, 5)
            │
            ▼
state.list_items_for_digest  (SQL gets category-priority as primary sort key)
   ORDER BY (CASE source_category WHEN 'world' THEN 0 ELSE 1 END),
            importance DESC, published_at DESC, title COLLATE NOCASE ASC
            │
            ▼
digester.format_items_for_prompt
   (linear pass-through; emits a `## {CategoryLabel}` H2 marker on each
    category-change boundary; no re-sort, no re-group)
            │
            ▼
digest_morning.md / digest_evening.md
   (one extra rule: 'mirror the input `## ` headers in your output')
            │
            ▼
~/Documents/!AI/news/YYYY/MM/YYYY-MM-DD.md
   # News-Digest 7. May 2026 (Morgen)        ← H1 from 14c082b
   ## Weltgeschehen                          ← NEW H2 from Phase 2a
   ### Welt-Item-Headline                    ← H3 per item (unchanged)
   ...
   ## AI/LLM/ML                              ← NEW H2 from Phase 2a
   ### AI-Item-Headline                      ← H3 per item (unchanged)
   ...
   ---                                       ← Slot separator (existing)
   # News-Digest 7. May 2026 (Abend)         ← H1 from 14c082b
   ## Weltgeschehen
   ## AI/LLM/ML
```

### 2.2 Module-by-Module Diff

| Module | Change | Magnitude |
|---|---|---|
| `config.py` | None. `Source.category`/`subcategory` already exist. | — |
| `sources.yaml` | +5–7 Welt sources (`category: world`). | YAML edit |
| `state.py` | (1) `list_items_for_digest` and `list_items_for_scoring` join `source_category` (today only `source_subcategory` and `source_name`). (2) `list_items_for_digest` gains a category-priority sort key prepended to the existing `ORDER BY`. | ~3 SQL lines |
| `scorer.py` | `prompt_name` routing by category. | 1 line |
| `agent_client.py` | None. Loads prompt files by name. | — |
| `notifier.py` | Replace hardcoded threshold ints + arxiv if-clause with `NOTIFICATION_THRESHOLDS` table; resolve via `(category, subcategory)` exact → `(category, "*")` wildcard → `DEFAULT`. Title-prefix dict gains `"world": "Welt"`. | ~25 LOC + tests |
| `digester.py` | `format_items_for_prompt`: linear pass-through with category-boundary `## ` header insertion (no re-grouping). `_build_fallback_digest` same. New constant `CATEGORY_LABEL`. | ~12 LOC + tests |
| `config/prompts/score_item.md` | Rename → `score_item_ai.md`, no content diff. | rename |
| `config/prompts/score_item_world.md` | NEW. Welt calibration scale (see §4). | ~80 lines |
| `config/prompts/digest_morning.md` + `digest_evening.md` | One added rule: input may contain `## Weltgeschehen` / `## AI/LLM/ML` H2 markers — mirror them 1:1 in the output, place items beneath the corresponding marker. | ~3 lines per file |

### 2.3 Notifier-threshold table location

Three options were considered in brainstorming:

- **(a) Hardcoded constant in `notifier.py`** — chosen for Phase 2a. Replaces the existing hardcoded `THRESHOLD_QUIET=5` / `THRESHOLD_DAYTIME=4` plus the `if subcategory == "arxiv"` special case from `7d90fbc`, unifying both in one structure.
- (b) Top-level block in `sources.yaml` (`notification_thresholds:` next to `sources:`) — deferred to Phase 3 if more categories arrive.
- (c) Per-source field on `Source` — rejected: redundant (all `breaking` sources would copy the same block) and Pydantic schema gets noisier.

### 2.4 Wildcard-Match semantics in the Notifier

```python
NOTIFICATION_THRESHOLDS = {
    ("world", "breaking"): (3, 4),    # day, quiet
    ("world", "*"):        (4, 5),    # news, analysis
    ("ai", "arxiv"):       (5, 5),    # paradigm-shifting only (was 7d90fbc if-clause)
    ("ai", "*"):           (4, 5),
}
DEFAULT_THRESHOLD = (4, 5)


def _resolve_threshold(category: str, subcategory: str | None, hour_local: int) -> int:
    table = (
        NOTIFICATION_THRESHOLDS.get((category, subcategory))
        or NOTIFICATION_THRESHOLDS.get((category, "*"))
        or DEFAULT_THRESHOLD
    )
    day, quiet = table
    is_quiet = hour_local >= 22 or hour_local < 7
    return quiet if is_quiet else day
```

Resolution order: specific match → `(category, "*")` wildcard → hardcoded `DEFAULT_THRESHOLD` `(4, 5)`. Additively extensible — Phase 3 can refine via new subcategory rows without touching existing entries. The `(ai, arxiv)` row replaces the `7d90fbc` if-clause with no behaviour change.

### 2.5 What stays unchanged

- `digests` table — still `UNIQUE(date, slot)`. One row covers both categories per slot. The race-safe `claim_digest_slot` mechanism (commit `b1b84a4`) does not need re-engineering.
- launchd plists — same three, same trigger times.
- Cross-linking against `~/Documents/!AI/article_summaries/` — same per-URL match logic, irrespective of category.
- Notifier bundling (15-min per-category bundle on bursts) — kicks in automatically for Welt.
- Quiet-hours definition — 22:00–06:59 Europe/Berlin, unchanged.
- `_cutoff_for_slot` — Welt and AI items share one slot window; no Welt-specific cutoff.
- The `14c082b` slot-H1 / `_EVENING_HEADER_RE` mechanism — Phase 2a does not touch the slot-header layer; the H2 category sections live entirely *inside* a finalized slot block.

---

## 3. Welt Subcategories & Source Candidates

### 3.1 Operational Definitions

Three subcategories for `category: world`. A source belongs to exactly one subcategory; items inherit it. Definitions are **falsifiable**, anchored to source behaviour, not item content.

| Subcategory | Operational Definition | Push Threshold (day / quiet) | Frequency | Expected Volume |
|---|---|---|---|---|
| `breaking` | Editorially pre-filtered breaking-news stream. Short items; every publication represents a "must know now" decision by the editor. The source has its own breaking-news threshold that we respect. | 3 / 4 | 5 min poll | <5 / day normal, 10–20 in crises |
| `news` | Mainstream top-news feed: Politics / Economy / World from a publication's main lineup. No internal "breaking" gate. | 4 / 5 (as AI) | 30–60 min | 30–80 / day |
| `analysis` | Background / depth / commentary. Longer pieces, editorially delayed, often opinion + framing. | 4 / 5 (as AI) | 2–4 h | 10–30 / day |

**Falsifiability test:** "Would this source ship something within 2–3 minutes of 'Bundeskanzler resigns'?" → yes: `breaking`. "Would this item plausibly be the lead of tomorrow's print edition?" → yes: `news`. "Is this primarily framing rather than reporting?" → `analysis`.

### 3.2 Source Candidates (Plan-Phase verifies exact URLs + RSS availability)

**Lean budget: 6 sources, 1 + 3 + 2.**

#### `breaking` (1)

| Source | URL Candidate | Note |
|---|---|---|
| `tagesschau-eilmeldungen` | `https://www.tagesschau.de/eilmeldungen/index~rss2.xml` | Plan phase verifies RSS liveness with `curl`. Only reliable German breaking-news feed with editorial gating. dpa direct is not public. |

#### `news` (3)

| Source | URL Candidate | Note |
|---|---|---|
| `tagesschau-news` | `https://www.tagesschau.de/index~rss2.xml` | Main lineup feed. |
| `bbc-world` | `http://feeds.bbci.co.uk/news/world/rss.xml` | International, English. |
| `reuters-world` | TBD | Reuters retired public RSS in 2020/21. Plan phase: try sitemap-scrape (Phase-1 pattern from Anthropic), `openrss.org` mirror, or substitute AP World. |

#### `analysis` (2)

| Source | URL Candidate | Note |
|---|---|---|
| `zeit-politik` | `https://newsfeed.zeit.de/politik/index` | ZEIT politics feed. |
| `politico-eu` | `https://www.politico.eu/feed/` | EU politics depth, English. |

### 3.3 RSS Availability Risk + Fallback

Three sources are plan-phase risk — exact URL and fetch mechanism must be verified before they land hardcoded in `sources.yaml`:

- **Reuters** — RSS retirement is documented. Fallbacks: (1) sitemap-scrape (the Anthropic pattern in Phase 1), (2) RSS mirror via openrss.org, (3) substitute with AP World (same risk class).
- **AP World** — similar risk; likely only via a mirror.
- **Tagesschau Eilmeldungen** — the `eilmeldungen/index~rss2.xml` path existed historically; plan phase must `curl`-check current status.

**If `tagesschau-eilmeldungen` is dead**, two fallbacks are pre-decided:

- **Fallback variant 1 (preferred):** the main feed `tagesschau-news` reports breaking-eligible items via a `Eilmeldung:` title prefix; the fetcher assigns subcategory **per item** (heuristic) instead of per source. Small fetcher tweak; the `breaking` concept survives. Phase 2b can reuse the heuristic for similar mixed feeds.
- **Fallback variant 2:** drop `breaking` from Phase 2a entirely. Welt becomes all `news` with push ≥4. Notifier threshold table stays in (cost-free) and activates in Phase 2b/3. Loss: no breaking pathway.

→ **Spec default: variant 1.** Tagesschau uses `Eilmeldung:` as a consistent title prefix; the heuristic is trivial and reusable.

### 3.4 What is NOT in Phase 2a

- **Economic breaking sources** (Bloomberg, Reuters Markets) — Phase 3 or backlog.
- **Print-lead duplicates** (SPIEGEL/FAZ/SZ) — editorially congruent with Tagesschau main feed; would amplify dedup pressure (the deciding factor for Profile C in brainstorming).
- **Social-media aggregates** — explicitly out-of-scope per Phase-1 §10.3.

---

## 4. Welt Score Prompt Calibration

### 4.1 Architectural Separation: Score ↔ Subcategory

The Welt score prompt **does not see** the source's subcategory. It receives only `source_name`, `title`, `author`, `summary` (identical to Phase 1). Subcategory exclusively drives the notifier threshold.

Reason: clean separation of concerns. *Score* answers "how content-important is this item?", *Notifier* answers "should this push now?". Letting subcategory leak into the score prompt would inflate every Tagesschau breaking item to 4 by editorial pre-filter alone — and the subcategory threshold system would lose its room. Source-name inflation already exists in Phase 1 (anthropic-news triggers measurably higher scores) and is accepted; we don't add a second inflation lever.

### 4.2 Welt Scale 1–5 (initial calibration)

Target distribution as Phase 1: **1 % at 5 · 4 % at 4 · 20 % at 3 · 60 % at 2 · 15 % at 1.**

| Level | Label | Operational Criterion | Positive Examples | Anti-Examples |
|---|---|---|---|---|
| **5** | World-shaping (<1 %) | Event reshapes global order or has direct large-scale consequences for D/EU citizens | War declaration between major powers; mid-term resignation of Bundeskanzler / US President; G7 head of state suddenly deceased; nuclear-weapon use; ≥10 000-fatality earthquake; EU member announces exit; outcome of national parliamentary election (DE / US / UK / FR) | Cabinet reshuffle; single (high-profile) bill; routine summit communiqué; non-G7 election |
| **4** | National / EU significant (~4 %) | Bundestag / EU / state-level act with direct citizen impact; geopolitics escalation with clear D-relevance; central-bank rate change; significant economic shock | "Bundestag passes Heating Act"; "ECB cuts rate 25 bp"; "Saarland election: SPD loses majority"; "Trump imposes 50 % tariffs on EU imports"; EU-Russia sanctions package | Pre-debate announcements; opinion pieces *about* the policy; recap stories |
| **3** | Interesting (~20 %) | Substantive information that informs worldview without demanding immediate action. Default for analysis-tier items | "Study: top-DAX wage inequality grows"; "How China subsidises its EV industry"; "Italian government shaky after coalition row"; data-bearing economic-trend stories | — |
| **2** | Routine (~60 %, default) | Daily political cadence; incremental updates; cabinet meetings without consequence; talk-show statements; mid-tier foreign news without D relevance | — | — |
| **1** | Trivia (~15 %) | Off-topic for "Weltgeschehen" | Sport (unless Olympic-opening-class); celebrity / lifestyle; routine weather; tabloid | — |

**Tie-break rules** (anti-inflation): when uncertain between 2 and 3 → **pick 2**; between 3 and 4 → **pick 3**.

### 4.3 Calibration Check (prompt footer)

> Would a well-informed Tagesschau viewer remember this item as "worth talking about" two weeks later? If no → Level 2. If clearly yes, with concrete consequence for D/EU → Level 4. "Would talk about, but consequence diffuse" → Level 3.

Plus, against source-inflation:

> Even if the source is Tagesschau: routine Berlin-political-theater statements stay Level 2. Source reputation does not auto-promote.

### 4.4 Reason Field Language

The `reason` field in JSON output is reused as input to the Opus digest prompt. For Welt items: **`reason` always in German.** The digest output is German, and a German reason snippet is directly reusable by Opus without re-translation.

---

## 5. Digester Extension

### 5.1 `format_items_for_prompt` Diff (relative to current post-`a88cac1` code)

Current state — strict pass-through, items pre-sorted by SQL:

```python
def format_items_for_prompt(items, summaries_dir=None):
    lines = []
    for item in items:
        lines.append(f"- [{item['importance']}] {item['title']} · {item['source_name']} · ...")
        if body := (item["raw_summary"] or "").strip().replace("\n", " "):
            lines.append(f"  > {body[:ITEM_BODY_MAX_CHARS]}")
    return "\n".join(lines)
```

Phase 2a — same linear pass-through, plus a category-boundary header insertion:

```python
CATEGORY_LABEL = {"world": "Weltgeschehen", "ai": "AI/LLM/ML"}


def format_items_for_prompt(items, summaries_dir=None):
    """Render items as flat prose, with a `## {CategoryLabel}` H2 marker
    inserted on each category-change boundary. Items must arrive pre-sorted
    by SQL (category priority first, then importance/published_at/title).
    No Python re-sort or re-group.
    """
    lines = []
    current_category = None
    for item in items:
        cat = item["source_category"]
        if cat != current_category:
            if current_category is not None:
                lines.append("")  # blank line before next H2
            lines.append(f"## {CATEGORY_LABEL.get(cat, cat.capitalize())}")
            lines.append("")
            current_category = cat
        lines.append(f"- [{item['importance']}] {item['title']} · ...")
        if body := (item["raw_summary"] or "").strip().replace("\n", " "):
            lines.append(f"  > {body[:ITEM_BODY_MAX_CHARS]}")
    return "\n".join(lines)
```

Properties:
- **No re-sort.** SQL already returns items grouped by category-priority (Welt before AI) and ordered within each group by `(importance DESC, published_at DESC, title COLLATE NOCASE ASC)`. The function emits in the order received.
- **Category headers inserted exactly on transitions.** A digest with only AI items emits one `## AI/LLM/ML` header above the prose stream; a digest with only Welt items emits one `## Weltgeschehen` header. A mixed digest emits both, Welt first.
- **No empty-section stubs.** If a category has zero items, no header is emitted.
- **Subcategory still invisible** in the output — `f191674`'s decision against `### subcat` headers is preserved.

### 5.2 `state.list_items_for_digest` SQL change

Today (`a88cac1`):

```sql
ORDER BY items.importance DESC,
         items.published_at DESC,
         items.title COLLATE NOCASE ASC
```

Phase 2a prepends category priority:

```sql
ORDER BY (CASE sources.category WHEN 'world' THEN 0 ELSE 1 END),
         items.importance DESC,
         items.published_at DESC,
         items.title COLLATE NOCASE ASC
```

→ Welt items come before AI items in the result; within each category, the existing tertiary ordering applies. AI-only digests behave identically to Phase 1 (the `CASE` ties on `1` and the secondary keys decide). For Phase 2b, the `CASE` table is extended with `WHEN 'dresden' THEN 1 ELSE 2`, no other code change.

### 5.3 Digest Prompts (`digest_morning.md` / `digest_evening.md`)

**Unchanged:** variable substitution (`{{ items_markdown }}`, `{{ date_de }}`), `ITEM_BODY_MAX_CHARS=1200`, output format (Markdown), German output, `# News-Digest <date_de> (Morgen|Abend)` H1 (introduced by `14c082b`).

**One added rule per file:**

> Input contains one or more `## Weltgeschehen` / `## AI/LLM/ML` H2 headers separating items by top-level category. Mirror these H2 headers 1:1 in your output, in the same order, with the items beneath. If only one category is present, emit only that one H2.

This is the *only* prompt edit. The `f191674` rule "Keine Zwischenüberschriften — keine `## …`-Sub-Kategorie-Header" stays applicable to **subcategory** headers (`### lab`, `### breaking` …). The Phase-2a `## Weltgeschehen` / `## AI/LLM/ML` are at a different hierarchy level and are explicitly mandated; the existing rule should be tightened to clarify this distinction:

> Keine Subcategory-Zwischenüberschriften (`### lab`, `### breaking` o.ä.) — Items fließen als Strom unter ihrem `## {Category}`-Header. Die `## Weltgeschehen` / `## AI/LLM/ML`-Top-Level-Header kommen aus dem Input und müssen erhalten bleiben.

### 5.4 Fallback Digest (`_build_fallback_digest`)

Linear pass-through with the same category-boundary header insertion as `format_items_for_prompt`. Slot-H1 already comes from `_format_top_header(date, slot)` (introduced by `14c082b`); Phase 2a adds the H2-on-category-change inside the body. ~6 LOC additional.

### 5.5 Cross-Linking (unchanged)

`_resolve_cross_link` operates per-item URL against `~/Documents/!AI/article_summaries/`. Subcategory/category is irrelevant for the lookup. Welt items with an existing summary get the same badge as today's AI items.

The Phase-1 performance caveat (CLAUDE.md: full-text scan latency if `article_summaries/` grows to thousands of files) is not made worse by Phase 2a (linear in item count, same lookup). Stays on the Phase-1 deferral list.

### 5.6 Slot mechanics (unchanged)

`_cutoff_for_slot`, `_write_digest_file`, `_find_evening_section_start`, the `claim_digest_slot` race protection, the `--force` evening regeneration — all remain on the slot-H1 layer that `14c082b` made symmetric. Phase 2a adds nothing at this layer.

---

## 6. Notifier Extension

### 6.1 NOTIFICATION_THRESHOLDS Table

Replaces both the hardcoded `THRESHOLD_QUIET=5` / `THRESHOLD_DAYTIME=4` constants and the `if subcategory == "arxiv": return THRESHOLD_QUIET` branch from `7d90fbc`:

```python
# (category, subcategory) → (day_threshold, quiet_threshold)
# Resolution order: exact → (category, "*") wildcard → DEFAULT.
NOTIFICATION_THRESHOLDS = {
    ("world", "breaking"): (3, 4),
    ("world", "*"):        (4, 5),  # news, analysis
    ("ai", "arxiv"):       (5, 5),  # paradigm-shifting only (was 7d90fbc if-clause)
    ("ai", "*"):           (4, 5),
}
DEFAULT_THRESHOLD = (4, 5)
```

Quiet-hours definition (22:00–06:59 Europe/Berlin) unchanged.

### 6.2 Resolve function — extends the existing `compute_threshold_for_hour`

The current signature already reserves `category` and `subcategory` parameters (the body says "reserved for Phase 2 per-category overrides; unused today"). Phase 2a wires them up:

```python
def compute_threshold_for_hour(
    hour: int,
    *,
    category: str | None = None,
    subcategory: str | None = None,
) -> int:
    table = (
        NOTIFICATION_THRESHOLDS.get((category, subcategory))
        or NOTIFICATION_THRESHOLDS.get((category, "*"))
        or DEFAULT_THRESHOLD
    )
    day, quiet = table
    is_quiet = hour >= QUIET_HOUR_START or hour < QUIET_HOUR_END
    return quiet if is_quiet else day
```

Edge cases:
- Item with no subcategory (`None`) — falls cleanly through to `(category, "*")`.
- Item with unknown category (Phase 3 introduces `tech` before notifier table is updated) — falls to `DEFAULT_THRESHOLD`. **Intentional property:** new categories push as Phase-1 (≥4 / ≥5) until explicitly tuned.
- The Phase-1 arxiv item with importance=5: `(ai, arxiv)` matches first, returns `(5, 5)`. Day or quiet, threshold is 5. Behaviour identical to `7d90fbc`.
- The Phase-1 arxiv item with importance=4: same lookup, threshold is 5, item does not push. Identical to `7d90fbc`.

### 6.3 Title Prefix per Category

Current `notifier.py` already has a dict:

```python
TITLE_PREFIX = {
    "ai": "AI",
}
```

Phase 2a adds one entry:

```python
TITLE_PREFIX = {
    "ai": "AI",
    "world": "Welt",
}
```

Push titles: `"[Welt] tagesschau-eilmeldungen"` for single, `"[Welt] 4 neue Items"` for bundled. Subcategory does not appear in the title — `breaking` is an internal routing class, not a UX-visibility class.

### 6.4 Bundle / Dedup Behaviour (unchanged from Phase 1)

15-min dedup per category stays active (Phase-1 spec §4.3 step 4, code in `notifier.maybe_notify`). Bundling is **within a category, not across categories**. A Welt breaking and a simultaneous AI release fire as two separate pushes; no `"[Mix] 2 Items"` mode.

### 6.5 Burst Cap — deliberately NOT in Phase 2a

No Welt-specific hard cap (e.g. "max 3 Welt pushes / hour") in Phase 2a. The existing 15-min per-category bundling acts as a natural burst guard.

Reasons:
- Calibration first. If 7-day live test shows >20 Welt pushes / day average or >5 in 30 min, a cap arrives as a tuning patch — same iteration cycle as score-prompt tuning.
- Anti-pre-optimisation. Real push frequency is unknown until live data.
- Recovery path is trivial: a `MAX_PUSHES_PER_WINDOW = {('world', '*'): (3, 1800)}` constant block plus 5 lines of code. No spec change needed.

→ Tracked as Open Question 8.2.A.

### 6.6 Test Setup Diff

Phase-1 `Notifier(send_fn=...)` DI pattern unchanged. New tests:

- breaking + importance=3 + hour=10 → fires.
- news + importance=3 + hour=10 → no fire.
- breaking + importance=3 + hour=23 → no fire (quiet, threshold 4).
- unknown category + importance=4 + hour=10 → fires (DEFAULT).
- ai + arxiv + importance=4 + hour=10 → no fire (Phase-1 regression check, identical to `7d90fbc`).
- ai + arxiv + importance=5 + hour=10 → fires (Phase-1 regression check).
- ai + lab + importance=4 + hour=10 → fires (Phase-1 regression check, ai-wildcard).

---

## 7. Tests & Definition of Done

### 7.1 Test Pyramid Extension

**Unit tests (additive, ~12–15 new):**

| Module | New test | Purpose |
|---|---|---|
| `scorer.py` | `category=ai` → `prompt_name="score_item_ai"` | Routing |
| `scorer.py` | `category=world` → `prompt_name="score_item_world"` | Routing |
| `notifier.py` | breaking + imp=3 + hour=10 → fires | Wildcard + breaking modifier |
| `notifier.py` | breaking + imp=3 + hour=23 → no fire | Quiet override |
| `notifier.py` | news + imp=3 + hour=10 → no fire | Wildcard, no breaking bonus |
| `notifier.py` | unknown category + imp=4 → fires | DEFAULT fallback |
| `notifier.py` | ai+arxiv + imp=4 → no fire (regression for `7d90fbc` after table-migration) | Behaviour-preserving migration |
| `notifier.py` | ai+arxiv + imp=5 → fires (regression) | Behaviour-preserving migration |
| `notifier.py` | ai+lab + imp=4 → fires (regression) | Phase-1 wildcard behaviour green |
| `digester.format_items_for_prompt` | mixed Welt+AI → `## Weltgeschehen` first, then `## AI/LLM/ML`, items linear in input order | Category-boundary header |
| `digester.format_items_for_prompt` | only AI → single `## AI/LLM/ML` header above stream | Empty-side omission |
| `digester.format_items_for_prompt` | only Welt → single `## Weltgeschehen` header | Empty-side omission |
| `digester.format_items_for_prompt` | rendered items appear in input order, no internal re-sort | Confirms SQL-as-sort-source-of-truth |
| `digester._build_fallback_digest` | same category-boundary behaviour | Fallback consistency |
| `state.list_items_for_digest` | result includes `source_category` column AND items are returned with Welt-before-AI primary ordering | Schema-join + SQL sort |
| `state.list_items_for_scoring` | result includes `source_category` column | Routing prerequisite |
| `config.load_sources` | YAML with `category: world` validates | Pydantic accepts new category values |

**Integration tests (additive, 2–3 new):**

- End-to-end mixed: 2 AI + 2 Welt items → both scored with respective prompts → digest writes file with one slot-H1 + two H2 sections.
- End-to-end Welt-only: only Welt items → digest has `# News-Digest …` + `## Weltgeschehen` only, no `## AI/LLM/ML`.
- Notification end-to-end: Welt breaking imp=3 → push captured by mock `send_fn`; Welt news imp=3 → no push.

**Smoke tests (real LLM, on-demand, `@pytest.mark.real_llm`):**

- Calibration sanity: ~5–10 real Welt items scored against `score_item_world.md`, manually compared to scale §4.2.
- Digest format: mock items both categories → real Opus call → manual visual check that the slot-H1 holds two H2 sections in correct order, no `### subcat` header leakage.

**Phase-1 regression check (critical):**

All existing AI-only tests must stay green. The relevant ones to watch:

- `test_format_items_for_prompt_preserves_input_order` (AI-only, `f191674`/`a88cac1` introduced) — Phase 2a wraps the AI block in a `## AI/LLM/ML` H2 even when no Welt items are present. **This test must be updated** to expect the H2 header. Documented as a Phase-1-test-edit, not a regression.
- The `_EVENING_HEADER_RE` machinery from `14c082b` — Phase 2a does not touch it; the H2 sections live entirely inside slot blocks.
- The arxiv push-threshold tests (Phase-1 `7d90fbc`) — Phase 2a's table migration must produce identical behaviour for the `(ai, arxiv) → 5` and `(ai, lab) → 4` cases.

### 7.2 Coverage Target

Phase-1 ≥70 % in `src/newsroom/` stays. Phase 2a should *increase* effective coverage because new tests target newly added units (threshold table resolve, category-boundary insertion).

### 7.3 Definition of Done

**Functional:**
- [ ] `score_item.md` renamed to `score_item_ai.md`; `score_item_world.md` exists.
- [ ] All 5–7 Welt sources fetch successfully (`newsroom status` green).
- [ ] `score_model` (or logs) shows: Welt items scored with `score_item_world.md`, AI items with `score_item_ai.md`.
- [ ] Welt `breaking` item importance=3 + day-time → push fires.
- [ ] Welt `news` item importance=3 + day-time → push does not fire.
- [ ] Phase-1 `(ai, arxiv) → imp=5` push behaviour preserved post-table-migration.
- [ ] Digest contains `# News-Digest …` slot-H1 + `## Weltgeschehen` (top) + `## AI/LLM/ML` (below) when both categories have items.
- [ ] Digest omits `## Weltgeschehen` when no Welt items in the slot window; omits `## AI/LLM/ML` when no AI items.
- [ ] Cross-linking against `article_summaries/` works for Welt items unchanged.
- [ ] Tagesschau-Eilmeldungen source online OR item-level breaking heuristic (variant 1, §3.3) implemented.

**Non-functional:**
- [ ] `pytest` green; coverage ≥70 % in `src/newsroom/`.
- [ ] `ruff check` + `ruff format --check` clean.
- [ ] `vulture src/` clean.
- [ ] All Phase-1 tests except the documented `format_items_for_prompt` H2-wrapping test edit stay green without modification.

**Operational:**
- [ ] 7-day live test, no manual intervention.
- [ ] Welt push signal-to-noise subjectively ≥60 % "worthwhile" (same yardstick as Phase-1).
- [ ] ≤20 Welt pushes / day on 7-day average (informal volume guardrail; exceeding triggers Open Question 8.2.A, not a DoD fail).
- [ ] 0 missed digests over 7 days.
- [ ] Welt score distribution after 7 days lies in **loose bands** of the target: ≥1 % at 5, ≥2 % at 4, ≥15 % at 3, ≥50 % at 2, ≥10 % at 1. Strong band deviation triggers a score-prompt tuning PR before "DoD met".

**Documentation:**
- [ ] This spec committed.
- [ ] Plan committed under `docs/superpowers/plans/2026-05-07-daily-newsroom-phase2a.md`.
- [ ] `CLAUDE.md` Phase Roadmap updated: Phase 2a (Welt) status, Phase 2b (Dresden) marked next.
- [ ] `CLAUDE.md` Common Gotchas: score-prompt routing — adding a new category requires a corresponding `score_item_<category>.md` file, otherwise `KeyError`.

---

## 8. Risks, Open Questions, Phase-2b Hooks

### 8.1 Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Tagesschau-Eilmeldungen feed dead or moved | Medium | Medium | Variant-1 fallback (item-level breaking heuristic on main feed). Plan phase verifies URL before YAML entry. |
| Reuters/AP have no RSS and sitemap-scrape blocked | High | Low | Total-failure fallback: start Phase 2a with 5 sources (Tagesschau-Eil + Tagesschau-news + BBC + ZEIT + Politico). Lean budget honoured. |
| Welt score prompt fails to differentiate from AI prompt | Medium | Low | Live test surfaces this. Iterative sharpening as Phase 1 (commits `7ae30f5` / `ab630ac`). DoD bands deliberately loose to allow tuning. |
| Welt push volume explodes during real crisis (war, attack) | Low (in 7-day window) / High (lifetime) | High | Phase-1 15-min bundling kicks in automatically. Open Question 8.2.A triggers explicit cap if live data shows >20 / day. |
| Phase-1 AI tests break unintentionally | Medium | High | Each touched module has an explicit Phase-1 regression test in the pyramid. CI runs after each commit. |
| Threshold-table migration regresses `7d90fbc` arxiv behaviour | Low | Medium | Two specific regression tests (`(ai, arxiv) imp=4 → no fire`, `imp=5 → fires`) before the migration commit lands. |
| SQL category-priority sort introduces query-plan regression | Low | Low | The `CASE` is deterministic and indexable via expression; SQLite query planner handles it inline. Spec-Phase-1 SQL hot-path is `idx_items_digest_pending` — unaffected. EXPLAIN QUERY PLAN to confirm in plan phase. |
| Score-model cost rises too much from Welt sources | Low | Low | Welt items shorter on average than arXiv. Haiku score cost per item small. Cost tracking via `events.jsonl` (Phase-1 logging infrastructure). |
| Tagesschau Eilmeldungen flag items the user doesn't see as world-relevant (sport, local) | Medium | Low | Score scale §4.2 has explicit Sport→Level-1 rule. A sport breaking would score 1 → no push. |

### 8.2 Open Questions (deferred decisions)

**A) Burst cap for Welt pushes.** If live test shows ≥20 Welt pushes / day or >5 in 30 min: introduce hardcoded `MAX_PUSHES_PER_WINDOW = {('world', '*'): (3, 1800)}`. Patch character, no spec update. Decision post-live-test.

**B) Level-5 sharpness on death of non-G7 head of state.** Scale §4.2 deliberately under-specified. Resolve after a real incident (or a synthetic 30-day lookback) via `score_item_world.md` edit.

**C) Cross-category dedup.** When the same story (e.g. "Anthropic receives 1B investment") appears in both `ai` and `world` sources. Phase 2a surfaces both; notifier fires both. Deferred to a future phase if data shows it's a real problem.

**D) Migrate notifier threshold table to `sources.yaml`.** Phase 2a hardcodes; if Phase 3 brings five more categories, evaluate migration to variant (b) of §2.3.

**E) Reason language strict-German vs heuristic.** Spec default: "Welt reasons always German". If Opus digester struggles to mix German reasons in English AI items, split heuristically per item.

### 8.3 Phase-2b Hooks (Phase 2a prepares without using)

Phase 2a builds the mechanism such that Phase 2b (Dresden) becomes **mostly YAML + prompt**:

| Phase-2b extension | Phase-2a groundwork | Phase-2b effort |
|---|---|---|
| Dresden sources with `category: dresden` | Scorer routing via `f"score_item_{category}"` | YAML edit |
| `score_item_dresden.md` (local politics, transport, economy, culture) | Phase-2a score prompt as template | 1 new prompt file |
| `score_item_dresden_science.md` (TU/HZDR/MPI press) | NOTIFICATION_THRESHOLDS table accepts more `(category, subcategory)` rows additively | 1 new prompt file + 1 table row |
| Digest with `## Dresden` H2 section | `CATEGORY_LABEL` dict gains one entry; SQL `CASE` extends with `WHEN 'dresden' THEN 1 ELSE 2` (Welt > Dresden > AI by default) | 1 dict entry + 1 SQL line |
| Dresden science has its own push threshold | Wildcard resolve already supports `(category, subcategory)`-specific entries | 1 table entry |

Phase-2b effort estimate (assuming Phase-2a groundwork in place): ~1 day implementation + 3–5 days source research and live test. Markedly shorter than Phase 2a (~1 week implementation + 1 week live test) — exactly the mechanism-vs-buildout trade-off chosen during brainstorming.

### 8.4 CLAUDE.md Updates Required (part of Phase 2a implementation)

- **Phase Roadmap** section: Phase 2 → Phase 2a (Welt) `in progress`, Phase 2b (Dresden) marked as next.
- **Common Gotchas:** entry that score-prompt routing runs via `f"score_item_{category}"` — adding a new category requires a matching prompt file or scoring will `KeyError`.
- **Architecture Summary:** no structural change. The "dumb trigger, smart script" pattern stands.

---

## 9. References

- Phase-1 design spec: `docs/superpowers/specs/2026-04-19-daily-newsroom-design.md`
- Phase-1 implementation plan: `docs/superpowers/plans/2026-04-19-daily-newsroom-phase1.md`
- Digest-notification design (Phase 1.5): `docs/superpowers/specs/2026-05-05-digest-notification-design.md`
- Phase-1 score-prompt sharpening commits: `7ae30f5` (Level-2 sharpen), `ab630ac` (recalibrate)
- Phase-1 arxiv push special-case: `7d90fbc` (now subsumed by Phase-2a NOTIFICATION_THRESHOLDS table)
- Phase-1 digest flat-newsletter refactor: `f191674` (drop subcategory headers), `a88cac1` (sort to SQL)
- Phase-1 H1-symmetry fix (Phase-2a prerequisite): `14c082b`
- Race-safe digest slot claim: `b1b84a4`
- Existing knowledge-management target: `~/Documents/!AI/article_summaries/`, `~/Documents/!AI/Glossar/`
