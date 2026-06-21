# Deterministic digest rendering: LLM returns typed content, Python renders structure

## Context and Problem Statement

The digest generator (`generate_digest`) called the LLM with `parse="text"` and
wrote the returned markdown verbatim to disk. The model therefore owned not only
content (German headline, prose, optional quote) but all structural boilerplate:
`###` headings, the `- [ ] interessiert mich` interest checkbox, `##` section
grouping, the `---` evening separator, the `[Weiterlesen →] · *source · time ·
Importance N*` meta line, and the relative-time computation from `published_at`.

This was fragile and untestable. Adding the interest checkbox had to be done as
prompt engineering rather than a code change with a test; a dropped or malformed
structural element surfaced only by reading generated files; and the LLM is a
known-unreliable source for date arithmetic (relative-time).

How should the boundary between LLM-generated content and program-rendered
structure be drawn so that structure is always correct and format changes are
maintainable?

## Decision Drivers

- **Structural correctness:** every structural element must always be exactly
  right, never silently altered or dropped by the model.
- **Maintainability:** future format changes should be a code/template edit with
  a unit test, not prompt tuning.
- **Format fidelity:** output must reproduce the exact prior on-disk format so
  historical digest files stay consistent (no migration).
- **Reuse existing infrastructure:** `agent_client` already supports
  `parse="json"`; the scorer/filter modules already follow an LLM→JSON→Python
  pattern.

Explicitly *not* a driver: minimizing LLM responsibility to the extreme.
Grey-area content decisions (e.g. whether a quote adds value) stay with the LLM.

## Considered Options

1. **Typed JSON fields + shared renderer** — the LLM returns
   `{"items":[{id,headline,prose,quote?}]}`; Python iterates the authoritative DB
   items and renders all structure through one `_render_item`/`_render_digest`
   path that also powers the LLM-outage fallback.
2. **Opaque per-item content blob** — the LLM returns `[{id, body_markdown}]`
   where `body_markdown` already contains headline+prose+quote; Python only wraps
   it with checkbox/meta/sections.
3. **Status quo (text) + deterministic post-validator** — the LLM keeps emitting
   the whole markdown document; Python parses, validates, and repairs it
   (inject missing checkbox, fix meta line).

## Decision Outcome

Chosen option: **Option 1 (typed JSON fields + shared renderer)**, because it is
the only option that fully satisfies both structural correctness and
maintainability: no LLM-produced structural string survives, the happy path and
the outage fallback collapse onto one unit-tested renderer, and it aligns the
digest with the codebase's established `parse="json"` pattern.

Python iterates the DB items (authoritative for order, category grouping, and all
metadata); LLM content is looked up by `id`. Items absent from the LLM response
degrade gracefully (headline = `title`, prose = truncated `raw_summary`) so no
item is ever dropped. relative-time, cross-link, and the meta line are computed
in Python (`Europe/Berlin`). The evening `---` separator stays owned by
`_write_digest_file`. The bespoke `_build_fallback_digest` is removed.

### Consequences

- Good: structural elements are guaranteed and fully unit-testable; the prior
  on-disk format is reproduced byte-for-byte (no file migration); relative-time
  is computed deterministically and can no longer be wrong; happy path and
  fallback share one renderer.
- Good: future format changes (like the interest checkbox that motivated this)
  are a code/template edit with a test.
- Bad/neutral: the LLM must echo each item `id` correctly — mitigated by
  id-keyed lookup, graceful degradation for missing/unknown ids, and a
  `logger.warning` on partial-mismatch.
- Bad/neutral: the digest prompts and several `generate_digest` test mocks had to
  migrate from a markdown-string contract to a JSON contract.

### Confirmation

The full test suite (228 tests) passes. Dedicated unit tests pin `_relative_time`
(today/yesterday/older, `Europe/Berlin`), `_render_item` (exact strings, quote,
cross-link, empty-prose), `_render_digest` (grouping, degradation, no `---`,
banner), the `id`-keyed prompt payload, and the happy-path render through
`generate_digest`. Format fidelity and the untouched-surface invariants
(`_write_digest_file`, `_find_evening_section_start`, `claim_digest_slot`,
`finalize_digest`, notify path, `agent_client`) were verified in a whole-branch
review.

## Pros and Cons of the Options

### Option 1 — Typed JSON fields + shared renderer

- Good: maximal correctness; one renderer for both paths; reuses `parse="json"`;
  format changes become code+test.
- Good: Python separately controls each field, so the `###` headline and meta
  line can never be malformed by the model.
- Neutral: requires a JSON contract and id round-tripping.

### Option 2 — Opaque per-item content blob

- Good: less rigid schema; prose may be freely multi-part markdown.
- Bad: the `###` headline still originates from LLM text, so Python cannot
  validate or separately control it — weakens the correctness goal.

### Option 3 — Status quo + post-validator

- Good: smallest upfront change.
- Bad: regex-parsing LLM prose for repair is exactly the fragility being removed;
  it relocates the problem instead of solving it. Fails both drivers.

## More Information

Distilled from the raw design spec produced during brainstorming. The spec was
removed from the tree in the same commit that added this ADR (different artifact
types: raw spec vs. distilled decision record). Retrieve the original spec from
history (rebase-safe, no literal hash):

~~~
git show "$(git rev-list -1 HEAD -- docs/specs/2026-06-21-deterministic-digest-rendering-design.md)^:docs/specs/2026-06-21-deterministic-digest-rendering-design.md"
~~~

Implementation landed in commits `936c1bf`..`05e6330` on `main`.
