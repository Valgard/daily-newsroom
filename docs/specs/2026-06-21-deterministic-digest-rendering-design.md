# Deterministic Digest Rendering — Design

**Date:** 2026-06-21
**Status:** Approved (brainstorming) — pending implementation plan
**Scope:** `src/newsroom/digester.py`, `config/prompts/digest_{morning,evening}.md`, `tests/test_digester.py`

## Problem

Today the digest LLM call (`generate_digest`, `parse="text"`) returns a **complete
markdown document** that is written verbatim to disk via `_write_digest_file`. The
model is therefore responsible not only for content (German headline, prose,
optional quote) but also for pure structural boilerplate:

- `### ` heading wrapper, `## Weltgeschehen` / `## AI/LLM/ML` section headers,
  `---` evening separator
- the `- [ ] interessiert mich` interest checkbox
- the meta line `[Weiterlesen →](url) · *source · relative-time · Importance N*`
- **relative-time computed from `published_at`** — a classic LLM error source
  (date-difference arithmetic)
- cross-link injection when `summary_path` is present

This is fragile and untestable. The recent checkbox addition had to be done as
prompt engineering instead of a code change with a test. A dropped or malformed
structural element only surfaces by reading generated files.

## Goals

- **(a) Structural correctness:** every structural element (checkbox, meta line,
  sections, separator, relative-time) is *always* exactly right, never produced
  by the LLM.
- **(b) Maintainability:** future format changes are a code + template edit with a
  unit test, not prompt tuning.

Explicitly **not** a goal: minimizing LLM responsibility to the extreme. Grey-area
content decisions (e.g. whether a quote adds value) stay with the LLM.

## Boundary

| Element | Owner |
|---|---|
| Top header, `## ` sections, `### ` wrapper, checkbox, meta line, relative-time, cross-link, `---` separator | **Python** (deterministic, tested) |
| German headline, prose paragraph, optional quote | **LLM** (language generation only) |

## Chosen Approach — Typed JSON fields + shared renderer

The LLM returns typed per-item content; Python renders all structure through a
single code path that also powers the fallback.

### Data flow

```
DB items (order + category + metadata)  ──┐
                                          ├─→ _render_digest ─→ markdown ─→ _write_digest_file
LLM: {"items":[{id,headline,prose,quote?}]} ─→ contents_by_id ─┘
```

Python iterates the **DB items** (authoritative for order, category grouping, and
all metadata). LLM content is looked up by `id`. Order and grouping never come
from the model.

### Components (all in `digester.py`)

- **`_relative_time(published_at: str, now: datetime) -> str`** — pure function,
  `Europe/Berlin` explicitly (project invariant). Returns `heute 14:30`,
  `gestern 09:15`, or `19.04. 07:00`.
- **`_render_item(item, content) -> str`** — renders one entry deterministically:
  `### {headline}`, blank line, `- [ ] interessiert mich`, blank line, prose,
  optional `› {quote}`, then the meta line (with ` · 📄 [Tief-Zusammenfassung](path)`
  appended when the item has a resolved `summary_path`). `item` is the DB row
  (authoritative url / source_name / importance / published_at / category);
  `content` carries the LLM building blocks.
- **`_render_digest(items, contents_by_id, *, slot, date) -> str`** — groups items
  by category into `## Weltgeschehen` / `## AI/LLM/ML` in DB order, emits the top
  header and (evening) the `---` separator, and calls `_render_item` per item.

### LLM contract

The prompt shrinks substantially: all structure / relative-time / checkbox /
section / meta-line rules are removed. What remains is **content guidance** only:
technical terms stay English, importance 2 → short / 5 → detailed, quote only when
it adds value, no clickbait headlines. Output:

```json
{"items": [{"id": 123, "headline": "…", "prose": "…", "quote": "… (optional)"}]}
```

Returned as an object (`{"items": […]}`) so it fits the existing
`parse="json" → dict` contract in `agent_client`. **No `agent_client` change.**

## Error handling

- **Missing item / unknown id:** Python iterates DB items; if an item has no LLM
  content, it is rendered **degraded** (headline = `title`, prose = truncated
  `raw_summary`). *No item ever disappears from the digest.* Unknown ids in the
  LLM output are ignored and logged.
- **Full LLM outage (`AgentError`):** same fallback path as today, but the fallback
  now uses the **same renderer** with `title` / `raw_summary` as content. Happy
  path and fallback collapse onto one structural path. The current bespoke
  `_build_fallback_digest` bullet-list format is replaced by the shared renderer
  output (carrying the existing "⚠️ Automatisch generiert" banner).

## Format compatibility

The renderer reproduces the **exact** current on-disk format (italic meta line,
spacing, checkbox placement) so no migration of historical files is required and
`git` history stays consistent. A golden-output test pins the format against a
known-good sample.

## Untouched

`_write_digest_file`, `_find_evening_section_start`, `--force` regeneration,
evening append, idempotency, `claim_digest_slot`, the notify path, and
`agent_client` all stay as-is. Only the *content source* and the *rendering*
change.

## Testing (TDD)

- `_relative_time`: today / yesterday / older, `Europe/Berlin` correctness, via
  `freezegun`.
- `_render_item`: with / without quote, with / without cross-link, checkbox always
  present, exact meta-line format.
- `_render_digest`: section grouping, DB order preserved, evening separator.
- Degradation: missing LLM content → item still rendered from title / summary.
- `generate_digest`: mock agent returning JSON (happy path); `AgentError` → shared
  renderer fallback. Existing text-mock digest tests are migrated to JSON returns.

## More Information

Raw brainstorming context: this design supersedes the LLM-produces-everything
rendering that the 2026-06-21 checkbox commit (`80a3b53`) worked around at the
prompt level.
