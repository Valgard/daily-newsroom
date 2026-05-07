# Daily Newsroom Phase 2a (Weltgeschehen) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `world` category to the daily-newsroom — a second top-level topic stream alongside `ai` — with 5–7 sources, its own score prompt, subcategory-driven push thresholds, and a two-section digest layout (`## Weltgeschehen` + `## AI/LLM/ML` under each slot's H1).

**Architecture:** Mechanism step: scorer routes prompts by `source.category`, notifier resolves push thresholds via a `(category, subcategory)`-keyed table (replacing both the hardcoded `THRESHOLD_*` constants and the `7d90fbc` arxiv if-clause), digester inserts a `## {CategoryLabel}` H2 marker on each category-change boundary in the linear pass-through. SQL `list_items_for_digest` gains a `CASE source_category WHEN 'world' THEN 0 ELSE 1` primary sort key so Welt items render before AI within a slot. No DB schema change, no launchd-plist change.

**Tech Stack:** Python 3.12, `uv`, SQLite (WAL), `claude-agent-sdk` via existing `AgentClient`, `feedparser` / `httpx` for fetch, `pync` for macOS notifications, `pytest` + `pytest-httpx` + `freezegun` for tests, `ruff` + `vulture` for linting/dead-code.

**Spec:** `docs/superpowers/specs/2026-05-07-daily-newsroom-phase2a-design.md`

**Worktree assumption:** This plan should be executed inside an isolated worktree (e.g. `.worktrees/phase2a` on branch `phase2a`), created via `superpowers:using-git-worktrees` before Task 1.

---

## File Map

**Modify:**
- `src/newsroom/state.py` — `list_items_for_scoring` + `list_items_for_digest` join `source_category`; the latter prepends category-priority to its `ORDER BY`.
- `src/newsroom/scorer.py` — route `prompt_name` by category.
- `src/newsroom/notifier.py` — replace `THRESHOLD_QUIET` / `THRESHOLD_DAYTIME` constants and the `if subcategory == "arxiv"` branch with a `NOTIFICATION_THRESHOLDS` table; extend `TITLE_PREFIX`.
- `src/newsroom/digester.py` — `CATEGORY_LABEL` constant; `format_items_for_prompt` and `_build_fallback_digest` insert a `## {Label}` H2 on each category-change boundary.
- `config/sources.yaml` — add 6 Welt sources (`category: world`).
- `config/prompts/score_item.md` → renamed to `score_item_ai.md` (no content diff).
- `config/prompts/digest_morning.md` + `digest_evening.md` — one rule added: mirror input H2 markers in output.
- `tests/test_state.py`, `tests/test_scorer.py`, `tests/test_notifier.py`, `tests/test_digester.py`, `tests/test_config.py` — additive tests.
- `CLAUDE.md` — Phase Roadmap entry; Common Gotchas entry on score-prompt routing.

**Create:**
- `config/prompts/score_item_world.md` — Welt scoring prompt (§4 of spec).

**Skip:**
- `src/newsroom/fetcher.py` — already propagates `source.category`/`subcategory` unchanged.
- `src/newsroom/agent_client.py` — already loads prompts by name.
- `src/newsroom/config.py` — `Source.category` accepts arbitrary strings already.
- `digests` table / SQLite schema — `UNIQUE(date, slot)` stays.
- launchd plists — same three.

---

## Task 1: Verify candidate Welt source URLs (plan-phase, no code commits)

Spec §3.3 requires that the four URL candidates be verified live before they land in `sources.yaml`. This task does the verification and records the outcome in a brief notes file under `docs/superpowers/plans/`. No source code changes.

**Files:**
- Create: `docs/superpowers/plans/2026-05-07-phase2a-source-verification.md`

- [ ] **Step 1: Curl-check the four high-risk candidates**

```bash
echo '=== tagesschau-eilmeldungen ==='
curl -s -o /dev/null -w 'HTTP %{http_code} · size %{size_download}\n' \
    -A 'Mozilla/5.0 (newsroom-verify)' \
    'https://www.tagesschau.de/eilmeldungen/index~rss2.xml'

echo '=== tagesschau-news ==='
curl -s -o /dev/null -w 'HTTP %{http_code} · size %{size_download}\n' \
    -A 'Mozilla/5.0 (newsroom-verify)' \
    'https://www.tagesschau.de/index~rss2.xml'

echo '=== bbc-world ==='
curl -s -o /dev/null -w 'HTTP %{http_code} · size %{size_download}\n' \
    -A 'Mozilla/5.0 (newsroom-verify)' \
    'http://feeds.bbci.co.uk/news/world/rss.xml'

echo '=== zeit-politik ==='
curl -s -o /dev/null -w 'HTTP %{http_code} · size %{size_download}\n' \
    -A 'Mozilla/5.0 (newsroom-verify)' \
    'https://newsfeed.zeit.de/politik/index'

echo '=== politico-eu ==='
curl -s -o /dev/null -w 'HTTP %{http_code} · size %{size_download}\n' \
    -A 'Mozilla/5.0 (newsroom-verify)' \
    'https://www.politico.eu/feed/'
```

Expected: HTTP 200 + non-zero size for each. HTTP 404/403 means the URL is dead.

- [ ] **Step 2: Curl-check Reuters (high RSS risk per spec)**

```bash
echo '=== reuters world (legacy)  ==='
curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
    'https://www.reutersagency.com/feed/?best-topics=world&post_type=best'
echo '=== reuters world (openrss mirror) ==='
curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
    'https://openrss.org/www.reuters.com/world/'
echo '=== reuters sitemap ==='
curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
    'https://www.reuters.com/sitemap.xml'
```

If all three fail: substitute with `ap-world` via `openrss.org/apnews.com/world-news` (also fragile). If that also fails: drop `reuters-world` and start with 5 sources (Spec §8.1 risk-row says lean budget remains honoured).

- [ ] **Step 3: Verify Tagesschau-Eilmeldungen item-prefix heuristic (Plan-Phase fallback)**

If Step 1's `tagesschau-eilmeldungen` returned 404, fetch the main feed and search for `Eilmeldung:` titles to confirm the heuristic still works:

```bash
curl -s 'https://www.tagesschau.de/index~rss2.xml' | \
    grep -c '<title>Eilmeldung:'
```

Expected: ≥1 if any breaking news happened recently, 0 if not. (A zero result is not conclusive — depends on news cycle. If non-zero, the heuristic is alive.)

- [ ] **Step 4: Record outcomes**

Write a short notes file documenting:
- Which URLs returned 200, which failed.
- Which fallback (variant 1 or variant 2 from spec §3.3) is in effect for `breaking`.
- Final list of 5–7 sources to add in Task 11.

```markdown
# Phase-2a Source URL Verification (2026-05-07)

| Source | URL | HTTP | Decision |
|---|---|---|---|
| tagesschau-eilmeldungen | https://www.tagesschau.de/eilmeldungen/index~rss2.xml | 200 / 404 | use / fallback variant 1 |
| tagesschau-news | https://www.tagesschau.de/index~rss2.xml | … | … |
| bbc-world | http://feeds.bbci.co.uk/news/world/rss.xml | … | … |
| reuters-world | <chosen URL> | … | use / drop |
| zeit-politik | https://newsfeed.zeit.de/politik/index | … | … |
| politico-eu | https://www.politico.eu/feed/ | … | … |

Fallback choice for `breaking`: variant 1 (item-level heuristic) / variant 2 (drop breaking).
Final source count: N.
```

- [ ] **Step 5: Commit the verification notes**

```bash
git add docs/superpowers/plans/2026-05-07-phase2a-source-verification.md
git commit -m "docs(plan): record Phase-2a source URL verification results"
```

---

## Task 2: state.list_items_for_scoring returns source_category

Today the SELECT joins `source_subcategory` and `source_name` only. Adding `source_category` is a prerequisite for Task 4 (scorer routing).

**Files:**
- Modify: `src/newsroom/state.py` (look near line 283 — `list_items_for_scoring` method)
- Modify: `tests/test_state.py`

- [ ] **Step 1: Locate the SELECT in list_items_for_scoring**

```bash
grep -n 'list_items_for_scoring\|sources.subcategory' src/newsroom/state.py | head -10
```

You should see one method around `def list_items_for_scoring(self, limit: int = 100)` with a SELECT joining `sources`. Read the SELECT block (~10 lines).

- [ ] **Step 2: Write a failing test that asserts the column is present**

Add at the end of `tests/test_state.py`:

```python
def test_list_items_for_scoring_returns_source_category(tmp_path: Path) -> None:
    """Phase 2a routing prerequisite: scorer needs source.category in the result row."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="anthropic",
            category="ai",
            subcategory="lab",
            url="https://www.anthropic.com/news/rss.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("anthropic")["id"]
    state.insert_item(
        source_id=src_id,
        item_hash="h1",
        url="https://a.com/1",
        title="Test",
        author=None,
        published_at="2026-04-19T02:00:00Z",
        raw_summary="body",
        category="ai",
    )
    rows = state.list_items_for_scoring(limit=10)
    assert len(rows) == 1
    assert rows[0]["source_category"] == "ai"
```

- [ ] **Step 3: Run test to verify it fails**

```bash
uv run pytest tests/test_state.py::test_list_items_for_scoring_returns_source_category -v
```

Expected: FAIL with `KeyError: 'source_category'` or similar.

- [ ] **Step 4: Add `sources.category AS source_category` to the SELECT**

In `src/newsroom/state.py`, modify the SELECT inside `list_items_for_scoring`. Find the line that joins `sources.subcategory AS source_subcategory` and add a sibling line for category. The change is one comma + one line.

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/test_state.py::test_list_items_for_scoring_returns_source_category -v
```

Expected: PASS.

- [ ] **Step 6: Run the whole test suite for regression**

```bash
uv run pytest -q
```

Expected: 200 passed (Phase-1 199 + this 1).

- [ ] **Step 7: Commit**

```bash
git add src/newsroom/state.py tests/test_state.py
git commit -m "feat(state): expose source_category in list_items_for_scoring

Phase-2a prerequisite: scorer routes prompt by category, so the
column must surface to the caller. Same shape change as the existing
source_subcategory and source_name aliases."
```

---

## Task 3: state.list_items_for_digest returns source_category + category-priority sort

Two changes in one method (they share a SQL block):
1. Add `sources.category AS source_category` to the SELECT (digester needs it).
2. Prepend `(CASE sources.category WHEN 'world' THEN 0 ELSE 1 END)` to the existing `ORDER BY` so Welt items come before AI within a slot.

**Files:**
- Modify: `src/newsroom/state.py` (look near line 367)
- Modify: `tests/test_state.py`

- [ ] **Step 1: Write the failing test for source_category column**

Add to `tests/test_state.py`:

```python
def test_list_items_for_digest_returns_source_category(tmp_path: Path) -> None:
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="anthropic",
            category="ai",
            subcategory="lab",
            url="https://www.anthropic.com/news/rss.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("anthropic")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h1", url="https://a.com/1",
        title="Test", author=None, published_at="2026-04-19T02:00:00Z",
        raw_summary="body", category="ai",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=4, reason="r", model="haiku")

    rows = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    assert len(rows) == 1
    assert rows[0]["source_category"] == "ai"
```

- [ ] **Step 2: Write the failing test for Welt-before-AI ordering**

```python
def test_list_items_for_digest_orders_world_before_ai(tmp_path: Path) -> None:
    """Spec §5.2: SQL prepends category priority so Welt items render first."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    # Two sources: one ai, one world
    for name, cat, sub in [
        ("anthropic", "ai", "lab"),
        ("tagesschau-news", "world", "news"),
    ]:
        state.upsert_source(
            Source(
                name=name, category=cat, subcategory=sub,
                url=f"https://example.com/{name}.rss",
                feed_type="rss", interval_seconds=3600, enabled=True,
            )
        )
    # Both items get importance=4 → without category priority, title would decide.
    # Title 'AAA' (ai) would come before 'ZZZ' (world) under tertiary alphabetic key.
    # With category priority, world wins regardless.
    ai_id = state.get_source_by_name("anthropic")["id"]
    world_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=ai_id, item_hash="h-ai", url="https://a.com/ai",
        title="AAA-ai-item", author=None,
        published_at="2026-04-19T02:00:00Z",
        raw_summary="body", category="ai",
    )
    state.insert_item(
        source_id=world_id, item_hash="h-world", url="https://t.de/world",
        title="ZZZ-world-item", author=None,
        published_at="2026-04-19T02:00:00Z",
        raw_summary="body", category="world",
    )
    for item in state.list_items_by_status("new", limit=10):
        state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="haiku")

    rows = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    assert len(rows) == 2
    assert rows[0]["source_category"] == "world", "Welt must come first"
    assert rows[1]["source_category"] == "ai"
```

- [ ] **Step 3: Run both tests to verify they fail**

```bash
uv run pytest tests/test_state.py::test_list_items_for_digest_returns_source_category \
              tests/test_state.py::test_list_items_for_digest_orders_world_before_ai -v
```

Expected: both FAIL.

- [ ] **Step 4: Update the SQL in list_items_for_digest**

In `src/newsroom/state.py`, find the `def list_items_for_digest(self, since_iso: str)` method. The SELECT currently looks roughly like:

```sql
SELECT items.id, ...,
       sources.name AS source_name,
       sources.subcategory AS source_subcategory
  FROM items
  JOIN sources ON items.source_id = sources.id
 WHERE ...
 ORDER BY items.importance DESC,
          items.published_at DESC,
          items.title COLLATE NOCASE ASC
```

Change to:

```sql
SELECT items.id, ...,
       sources.name AS source_name,
       sources.category AS source_category,
       sources.subcategory AS source_subcategory
  FROM items
  JOIN sources ON items.source_id = sources.id
 WHERE ...
 ORDER BY (CASE sources.category WHEN 'world' THEN 0 ELSE 1 END),
          items.importance DESC,
          items.published_at DESC,
          items.title COLLATE NOCASE ASC
```

- [ ] **Step 5: Run both new tests to verify they pass**

```bash
uv run pytest tests/test_state.py::test_list_items_for_digest_returns_source_category \
              tests/test_state.py::test_list_items_for_digest_orders_world_before_ai -v
```

Expected: both PASS.

- [ ] **Step 6: Run the full suite for regression**

```bash
uv run pytest -q
```

Expected: 202 passed.

The existing `test_state.py` `tertiary alphabetical sort` test (introduced by `07078af` / now `a88cac1`) must stay green — its data is all `category='ai'`, so the new `CASE` ties on `1` and the secondary keys still decide. If it fails, the new sort key is interfering — re-check the parenthesisation.

- [ ] **Step 7: Commit**

```bash
git add src/newsroom/state.py tests/test_state.py
git commit -m "feat(state): expose source_category + Welt-priority sort in list_items_for_digest

The digest layer needs source_category to insert ## H2 markers on
category-change boundaries (Phase 2a §5.1). The CASE prepended to
ORDER BY makes Welt items render before AI within a slot, so the
linear pass-through in format_items_for_prompt receives the items
in the desired display order without Python re-sorting."
```

---

## Task 4: Rename score_item.md and route scorer prompt by category

Two operations bundled:
1. `git mv config/prompts/score_item.md config/prompts/score_item_ai.md` (no content diff).
2. `scorer.py` builds the prompt name from `item["source_category"]`.

A failing-test-first dance for the routing change; the rename is verified by running tests after.

**Files:**
- Rename: `config/prompts/score_item.md` → `config/prompts/score_item_ai.md`
- Modify: `src/newsroom/scorer.py` (line ~31 — `prompt_name="score_item"`)
- Modify: `tests/test_scorer.py`

- [ ] **Step 1: Inspect the current scorer call site**

```bash
grep -n 'prompt_name=' src/newsroom/scorer.py
```

Expected: one line, `prompt_name="score_item"`.

- [ ] **Step 2: Write the failing test for category-routing**

Add to `tests/test_scorer.py`:

```python
import pytest
from unittest.mock import AsyncMock
from pathlib import Path

from newsroom.scorer import score_pending_items
from newsroom.state import State
from newsroom.config import Source


@pytest.mark.asyncio
async def test_scorer_routes_prompt_by_category(tmp_path: Path) -> None:
    """Phase 2a: ai items use score_item_ai, world items use score_item_world."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    for name, cat, sub in [
        ("anthropic", "ai", "lab"),
        ("tagesschau-news", "world", "news"),
    ]:
        state.upsert_source(
            Source(
                name=name, category=cat, subcategory=sub,
                url=f"https://example.com/{name}.rss",
                feed_type="rss", interval_seconds=3600, enabled=True,
            )
        )
    ai_id = state.get_source_by_name("anthropic")["id"]
    world_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=ai_id, item_hash="h-ai", url="https://a.com/ai",
        title="AI item", author=None, published_at="2026-04-19T02:00:00Z",
        raw_summary="body", category="ai",
    )
    state.insert_item(
        source_id=world_id, item_hash="h-world", url="https://t.de/world",
        title="World item", author=None, published_at="2026-04-19T02:00:00Z",
        raw_summary="body", category="world",
    )

    mock_agent = AsyncMock()
    mock_agent.ask.return_value = {"importance": 3, "reason": "test"}
    await score_pending_items(state, agent=mock_agent)

    prompts_used = {call.kwargs["prompt_name"] for call in mock_agent.ask.call_args_list}
    assert prompts_used == {"score_item_ai", "score_item_world"}
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/test_scorer.py::test_scorer_routes_prompt_by_category -v
```

Expected: FAIL — `prompts_used == {"score_item"}`.

- [ ] **Step 4: Rename the prompt file**

```bash
git mv config/prompts/score_item.md config/prompts/score_item_ai.md
```

- [ ] **Step 5: Update the scorer**

In `src/newsroom/scorer.py`, change

```python
result = await agent.ask(
    prompt_name="score_item",
    variables={...},
    ...
)
```

to

```python
result = await agent.ask(
    prompt_name=f"score_item_{item['source_category']}",
    variables={...},
    ...
)
```

- [ ] **Step 6: Run the failing test (now passing)**

```bash
uv run pytest tests/test_scorer.py::test_scorer_routes_prompt_by_category -v
```

Expected: PASS.

- [ ] **Step 7: Run the full suite for regression**

```bash
uv run pytest -q
```

Expected: 203 passed. If a Phase-1 scorer test fails on `prompt_name`, it likely had a hardcoded `"score_item"` assertion — update it to `"score_item_ai"`.

- [ ] **Step 8: Commit**

```bash
git add config/prompts/score_item_ai.md src/newsroom/scorer.py tests/test_scorer.py
git rm config/prompts/score_item.md  # already moved by `git mv`, but ensure rm is staged if needed
git commit -m "feat(scorer): route prompt by category (score_item_<category>.md)

Phase-2a prerequisite for the world category. score_item.md is
renamed to score_item_ai.md (no content diff); the scorer builds
prompt_name as f\"score_item_{category}\". A future world category
adds a sibling prompt file and works without further code changes."
```

---

## Task 5: Create config/prompts/score_item_world.md

Full content of the Welt-domain scoring prompt, modelled on `score_item_ai.md` but calibrated to spec §4.2 (geopolitical 1–5 scale) and §4.3 (calibration check, source-inflation guard).

**Files:**
- Create: `config/prompts/score_item_world.md`

- [ ] **Step 1: Write the prompt file**

Create `config/prompts/score_item_world.md`:

~~~markdown
You are rating the importance of a single news item for a reader who follows world news (German + international politics, economics, geopolitics).

**Importance scale (be strict — target distribution: 1% at 5, 4% at 4, 20% at 3, 60% at 2, 15% at 1):**

- **5 — World-shaping (very rare, <1% of items):** Events that reshape the global order or have direct large-scale consequences for German/EU citizens.
  - Examples: Kriegserklärung zwischen Großmächten, mid-term-Rücktritt Bundeskanzler / US-Präsident, plötzlicher Tod eines G7-Staatsoberhaupts, Atomwaffen-Einsatz, Erdbeben mit ≥10 000 Toten, EU-Mitglied verkündet Austritt, Wahl-Ergebnis nationale Parlamentswahl Deutschland/USA/UK/Frankreich.
  - NOT: Kabinettsumbildung, einzelnes (auch hochkarätiges) Gesetz, Routine-Gipfel-Kommuniqué, Wahl in Nicht-G7-Staat.

- **4 — National / EU significant (rare, ~4% of items):** Bundestag / EU / Bundesland-Akt mit direkter Bürger-Auswirkung; Geopolitik-Eskalation mit klarer D-Relevanz; Leitzins-Wechsel; signifikanter Wirtschafts-Schock.
  - Examples: "Bundestag verabschiedet Heizungsgesetz", "EZB senkt Leitzins um 25 bp", "Saarland-Wahl: SPD verliert Mehrheit", "Trump verhängt 50 %-Zölle auf EU-Importe", EU-Russland-Sanktionspaket.
  - NOT: Vor-Debatten-Ankündigungen, Meinungsstücke *über* das Gesetz, Recap-Stories.

- **3 — Interesting (~20% of items):** Substantielle Information, die das Weltbild informiert, ohne sofortige Handlung zu verlangen. Default für analysis-Tier-Items.
  - Examples: "Studie: Lohnungleichheit DAX wächst", "Wie China seine EV-Industrie subventioniert", "Italien-Regierung wackelt nach Koalitionsstreit", Wirtschafts-Trend-Stories mit Daten.
  - Most analysis-tier items default here.

- **2 — Routine (~60%, default):** Tägliche Politik-Kadenz; inkrementelle Updates; Kabinetts-Meldungen ohne Tragweite; Talkshow-Aussagen; mid-tier Auslands-Meldungen ohne D-Bezug.
  - When uncertain between 2 and 3, pick 2 — Level 3 requires *substantive* new information, not just being well-written.

- **1 — Trivia (~15%):** Off-topic für "Weltgeschehen". Sport (sofern nicht Olympia-Eröffnungs-Klasse), Promi/Lifestyle, Wetter-Routine, Boulevard.

**Tie-breakers (anti-inflation):**
- Between 2 and 3: pick 2.
- Between 3 and 4: pick 3.

**Item:**
- Source: {{ source_name }}
- Title: {{ title }}
- Author: {{ author }}
- Body: {{ summary }}

**Calibration check (do this before answering):**
- Würde ein gut informierter Tagesschau-Zuschauer dieses Item zwei Wochen später noch als "darüber hat man geredet" erinnern? Falls nein → Level 2. Falls eindeutig ja, mit klarer Konsequenz für D/EU → Level 4. "Würde drüber reden, aber Konsequenz nur diffus" → Level 3.
- Auch wenn die Quelle Tagesschau ist: ein Routine-Statement aus Berliner Politik-Theater bleibt Level 2. Source-Reputation triggert keine automatische Promotion.

Push notifications (importance ≥ 4 normal, ≥ 3 für `breaking`-Subcategory) sind eine Unterbrechung — aber eine Notification, die nie feuert, ist auch ein Versagen. Trust the rubric and the source signal.

Respond with a JSON object only, no prose:

```
{"importance": <1-5>, "reason": "<one short German sentence>"}
```
~~~

- [ ] **Step 2: Verify the file is loadable as a prompt**

```bash
test -f config/prompts/score_item_world.md && wc -l config/prompts/score_item_world.md
```

Expected: ~50–80 lines.

- [ ] **Step 3: Run all scorer tests (Welt routing now has a prompt to load)**

```bash
uv run pytest tests/test_scorer.py -q
```

Expected: green. Task 4's routing test still passes (it mocks the agent, doesn't actually load the prompt — but the file existing is good for smoke).

- [ ] **Step 4: Commit**

```bash
git add config/prompts/score_item_world.md
git commit -m "feat(prompts): add score_item_world.md (Welt scoring scale)

§4.2 of the Phase-2a spec: 1–5 importance scale calibrated to
geopolitical / national / EU events. Tie-breakers and a calibration
check guard against inflation. Reason field is required to be
German because the digest output and digest-prompt input are German."
```

---

## Task 6: Migrate Notifier to NOTIFICATION_THRESHOLDS table

Replaces both:
- The `THRESHOLD_QUIET=5` / `THRESHOLD_DAYTIME=4` constants.
- The `if subcategory == "arxiv": return THRESHOLD_QUIET` early-return from `7d90fbc`.

The migration must produce **identical Phase-1 behaviour** for `(ai, arxiv)` and `(ai, lab)` items. Two regression tests are written before the migration to lock the contract.

**Files:**
- Modify: `src/newsroom/notifier.py`
- Modify: `tests/test_notifier.py`

- [ ] **Step 1: Identify the call sites**

```bash
grep -n 'THRESHOLD_QUIET\|THRESHOLD_DAYTIME\|compute_threshold_for_hour\|"arxiv"' \
    src/newsroom/notifier.py tests/test_notifier.py
```

Read the current `compute_threshold_for_hour` (~lines 34–52 of `notifier.py`).

- [ ] **Step 2: Write Phase-1 regression tests for arxiv (locks contract before migration)**

Add to `tests/test_notifier.py`:

```python
def test_threshold_arxiv_returns_5_daytime_phase1_regression() -> None:
    """7d90fbc: arxiv pushes only at imp=5 even at daytime — must survive table migration."""
    threshold = compute_threshold_for_hour(hour=10, category="ai", subcategory="arxiv")
    assert threshold == 5


def test_threshold_arxiv_returns_5_quiet_phase1_regression() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="ai", subcategory="arxiv")
    assert threshold == 5


def test_threshold_ai_lab_returns_4_daytime_phase1_regression() -> None:
    """Phase-1 baseline: non-arxiv ai items push at imp=4 daytime."""
    threshold = compute_threshold_for_hour(hour=10, category="ai", subcategory="lab")
    assert threshold == 4


def test_threshold_ai_lab_returns_5_quiet_phase1_regression() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="ai", subcategory="lab")
    assert threshold == 5
```

(Adjust the import line at the top of `test_notifier.py` if `compute_threshold_for_hour` isn't yet imported.)

- [ ] **Step 3: Run the regression tests against the current code**

```bash
uv run pytest tests/test_notifier.py -k 'phase1_regression' -v
```

Expected: all 4 PASS (these encode Phase-1 behaviour, which the current code provides).

- [ ] **Step 4: Write the new failing tests for Welt subcategories**

Add to `tests/test_notifier.py`:

```python
def test_threshold_world_breaking_lowers_to_3_daytime() -> None:
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="breaking")
    assert threshold == 3


def test_threshold_world_breaking_uses_4_quiet() -> None:
    threshold = compute_threshold_for_hour(hour=23, category="world", subcategory="breaking")
    assert threshold == 4


def test_threshold_world_news_uses_4_daytime() -> None:
    """Welt news/analysis fall through to (world, *) wildcard — same as Phase-1 ai."""
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="news")
    assert threshold == 4


def test_threshold_world_analysis_uses_4_daytime() -> None:
    threshold = compute_threshold_for_hour(hour=10, category="world", subcategory="analysis")
    assert threshold == 4


def test_threshold_unknown_category_falls_through_to_default() -> None:
    """Phase-3 readiness: a future tech category pushes Phase-1-default until tuned."""
    threshold = compute_threshold_for_hour(hour=10, category="tech", subcategory="news")
    assert threshold == 4
```

- [ ] **Step 5: Run the new tests to verify they fail**

```bash
uv run pytest tests/test_notifier.py -k 'world_breaking or world_news or world_analysis or unknown_category' -v
```

Expected: most FAIL (current code returns Phase-1 defaults for unknown categories, so some happen to pass — particularly `world_news` returns 4 by coincidence; lock the failure on `world_breaking_lowers_to_3`).

- [ ] **Step 6: Migrate notifier.py to the table**

In `src/newsroom/notifier.py`, replace

```python
THRESHOLD_QUIET = 5
THRESHOLD_DAYTIME = 4

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
    # arxiv: only paradigm-shifting papers (imp=5) push; lower scores reach the reader
    # via the digest only. Volume of substantive arxiv work is too high to interrupt
    # at imp=4, but a genuine "GPT-4-beating open model" paper deserves a banner.
    if subcategory == "arxiv":
        return THRESHOLD_QUIET

    quiet_hours = hour >= QUIET_HOUR_START or hour < QUIET_HOUR_END
    return THRESHOLD_QUIET if quiet_hours else THRESHOLD_DAYTIME
```

with

```python
# Spec §4.3: suppress duplicate pushes in the same category within this window.
BUNDLING_WINDOW_MINUTES = 15

# (category, subcategory) → (day_threshold, quiet_threshold)
# Resolution order: exact match → (category, "*") wildcard → DEFAULT_THRESHOLD.
# arxiv (imp=5 only) was the 7d90fbc if-clause; it is now a regular row.
NOTIFICATION_THRESHOLDS: dict[tuple[str, str], tuple[int, int]] = {
    ("world", "breaking"): (3, 4),
    ("world", "*"):        (4, 5),  # news, analysis
    ("ai", "arxiv"):       (5, 5),  # paradigm-shifting only
    ("ai", "*"):           (4, 5),
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
```

Important: the `or ""`-coercion on `category` and `subcategory` is needed because `dict.get((None, None))` is a valid lookup and would silently match a key like `(None, None)` if anyone ever inserts one. Empty-string sentinel keeps the contract explicit.

Also remove the now-unused `THRESHOLD_QUIET = 5` / `THRESHOLD_DAYTIME = 4` constants (the table replaces them).

- [ ] **Step 7: Run all notifier tests (regression + new)**

```bash
uv run pytest tests/test_notifier.py -v
```

Expected: every test PASSES — Phase-1 regressions and Phase-2a Welt tests both green.

- [ ] **Step 8: Run the full suite for cross-module regression**

```bash
uv run pytest -q
```

Expected: 207+ passed.

- [ ] **Step 9: Lint and dead-code check**

```bash
uv run ruff format src/newsroom/notifier.py tests/test_notifier.py
uv run ruff check src/newsroom/notifier.py tests/test_notifier.py
uv run vulture src/
```

Expected: all clean. If `vulture` flags `THRESHOLD_QUIET` / `THRESHOLD_DAYTIME`, ensure they're fully removed (not just unused).

- [ ] **Step 10: Commit**

```bash
git add src/newsroom/notifier.py tests/test_notifier.py
git commit -m "feat(notifier): replace hardcoded thresholds + arxiv if-clause with table

NOTIFICATION_THRESHOLDS resolves (category, subcategory) → (day, quiet)
with specific → wildcard → DEFAULT fallback. The 7d90fbc arxiv special
case is now a regular row ('ai', 'arxiv'): (5, 5); behaviour identical.
Welt subcategories add ('world', 'breaking'): (3, 4) and ('world', '*'):
(4, 5). Phase-3 categories get the conservative DEFAULT until tuned."
```

---

## Task 7: Add "world" prefix to TITLE_PREFIX

Tiny edit, but needs a test so a regression doesn't slip through.

**Files:**
- Modify: `src/newsroom/notifier.py`
- Modify: `tests/test_notifier.py`

- [ ] **Step 1: Find the existing TITLE_PREFIX dict**

```bash
grep -n 'TITLE_PREFIX' src/newsroom/notifier.py
```

Expected: definition near top of file (~line 17), and one read in `maybe_notify`.

- [ ] **Step 2: Write the failing test for the world prefix**

Add to `tests/test_notifier.py`:

```python
async def test_maybe_notify_world_item_uses_welt_prefix(tmp_path: Path) -> None:
    """A pushed world item produces a `[Welt] ...` title."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="tagesschau-eilmeldungen",
            category="world",
            subcategory="breaking",
            url="https://www.tagesschau.de/eilmeldungen/index~rss2.xml",
            feed_type="rss",
            interval_seconds=300,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("tagesschau-eilmeldungen")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h-eil",
        url="https://www.tagesschau.de/eil/x",
        title="Eilmeldung: Test", author=None,
        published_at="2026-04-19T10:00:00Z",
        raw_summary="Test breaking", category="world",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=4, reason="r", model="haiku")

    captured: list[dict] = []

    async def fake_send(*, title: str, message: str, url: str | None) -> None:
        captured.append({"title": title, "message": message, "url": url})

    notifier = Notifier(send_fn=fake_send)
    item = state.get_item_with_source(item_id)

    with freeze_time("2026-04-19 10:00:00"):
        await notifier.maybe_notify(item, state)

    assert len(captured) == 1
    assert captured[0]["title"].startswith("[Welt] ")
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/test_notifier.py::test_maybe_notify_world_item_uses_welt_prefix -v
```

Expected: FAIL — title is `"[World] ..."` (capitalised category, fallback) or similar.

- [ ] **Step 4: Add the entry to TITLE_PREFIX**

In `src/newsroom/notifier.py`:

```python
TITLE_PREFIX = {
    "ai": "AI",
    "world": "Welt",
}
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
uv run pytest tests/test_notifier.py::test_maybe_notify_world_item_uses_welt_prefix -v
```

Expected: PASS.

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest -q
```

Expected: 208+ passed.

- [ ] **Step 7: Commit**

```bash
git add src/newsroom/notifier.py tests/test_notifier.py
git commit -m "feat(notifier): add Welt title prefix for world-category pushes

Pushes for world items now read '[Welt] tagesschau-eilmeldungen' instead
of falling through to the capitalised-category default. Subcategory
('breaking') stays internal — not visible in the push title."
```

---

## Task 8: Digester — CATEGORY_LABEL constant + category-boundary H2 insertion

`format_items_for_prompt` and `_build_fallback_digest` both need the same change: linear pass-through with a `## {Label}` header inserted whenever `source_category` changes between consecutive items.

**Files:**
- Modify: `src/newsroom/digester.py`
- Modify: `tests/test_digester.py`

- [ ] **Step 1: Inspect the current format_items_for_prompt**

```bash
sed -n '70,105p' src/newsroom/digester.py
```

Read the function — it's currently a flat loop with no grouping (post-`07078af` / `a88cac1`).

- [ ] **Step 2: Write failing tests for category-boundary headers**

Add to `tests/test_digester.py`:

```python
@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_emits_world_then_ai_top_level_headers(
    populated_state: State, tmp_path: Path
) -> None:
    """Mixed digest: ## Weltgeschehen above world items, ## AI/LLM/ML above ai items."""
    populated_state.upsert_source(
        Source(
            name="tagesschau-news",
            category="world",
            subcategory="news",
            url="https://www.tagesschau.de/index~rss2.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = populated_state.get_source_by_name("tagesschau-news")["id"]
    populated_state.insert_item(
        source_id=src_id, item_hash="h-w",
        url="https://www.tagesschau.de/x",
        title="Bundestag-Item", author=None,
        published_at="2026-04-19T08:00:00Z",
        raw_summary="welt body", category="world",
    )
    item_id = populated_state.list_items_by_status("new", limit=1)[0]["id"]
    populated_state.mark_item_scored(
        item_id=item_id, importance=4, reason="welt", model="haiku"
    )
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)

    welt_idx = formatted.find("## Weltgeschehen")
    ai_idx = formatted.find("## AI/LLM/ML")
    bundestag_idx = formatted.find("Bundestag-Item")
    title_0_idx = formatted.find("Title 0")  # AI item from the populated_state fixture

    assert welt_idx >= 0, "Welt H2 missing"
    assert ai_idx >= 0, "AI H2 missing"
    assert welt_idx < bundestag_idx < ai_idx < title_0_idx, \
        "expected order: ## Weltgeschehen, welt items, ## AI/LLM/ML, ai items"


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_ai_only_wraps_in_ai_header(
    populated_state: State, tmp_path: Path
) -> None:
    """AI-only digest gets one ## AI/LLM/ML header, no Welt header."""
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "## AI/LLM/ML" in formatted
    assert "## Weltgeschehen" not in formatted


@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_world_only_omits_ai_header(tmp_path: Path) -> None:
    """Welt-only digest gets one ## Weltgeschehen header, no AI header."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="tagesschau-news",
            category="world",
            subcategory="news",
            url="https://www.tagesschau.de/index~rss2.xml",
            feed_type="rss",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h-w", url="https://t.de/x",
        title="World Only", author=None,
        published_at="2026-04-19T08:00:00Z",
        raw_summary="body", category="world",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=4, reason="r", model="haiku")
    items = state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)
    assert "## Weltgeschehen" in formatted
    assert "## AI/LLM/ML" not in formatted
```

- [ ] **Step 3: Run the new tests to verify they fail**

```bash
uv run pytest tests/test_digester.py -k 'top_level_headers or wraps_in_ai_header or world_only_omits_ai' -v
```

Expected: FAIL on all three — current code emits no `## ` headers.

- [ ] **Step 4: Implement category-boundary header insertion**

In `src/newsroom/digester.py`, near the top with the other constants:

```python
CATEGORY_LABEL = {"world": "Weltgeschehen", "ai": "AI/LLM/ML"}
```

Replace `format_items_for_prompt` with:

```python
def format_items_for_prompt(items, summaries_dir: Path | None = None) -> str:  # noqa: ANN001
    """Render items as a flat markdown payload, inserting a `## {CategoryLabel}`
    H2 marker on each category-change boundary.

    Items must arrive pre-sorted by SQL (Welt before AI by category priority,
    then importance DESC, published_at DESC, title ASC). This function does
    not re-sort or re-group; it only watches for category transitions and
    emits a header whenever it sees a new category.

    Each item renders as:
        - [importance] title · source · published=iso · url=url · reason=reason
          > body (truncated to ITEM_BODY_MAX_CHARS)
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
    return "\n".join(lines)
```

- [ ] **Step 5: Update _build_fallback_digest analogously**

The fallback path needs the same boundary logic. Replace the items-rendering block (the `for item in items: lines.append(...)` near the end of `_build_fallback_digest`) with:

```python
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
            f"- **[Importance {item['importance']}]** "
            f"[{item['title']}]({item['url']}) · {item['source_name']}"
        )
```

- [ ] **Step 6: Run the new tests to verify they pass**

```bash
uv run pytest tests/test_digester.py -k 'top_level_headers or wraps_in_ai_header or world_only_omits_ai' -v
```

Expected: all three PASS.

- [ ] **Step 7: Run the full suite — expect ONE Phase-1 test to fail**

```bash
uv run pytest -q
```

Expected: `test_format_items_for_prompt_preserves_input_order` from `tests/test_digester.py` fails because the AI-only output is now wrapped in `## AI/LLM/ML`. **This is intentional** (spec §7.1 documents it as a Phase-1-test edit). It is fixed in Task 10.

- [ ] **Step 8: Commit (do NOT fix the broken Phase-1 test in this commit — Task 10 owns that)**

```bash
git add src/newsroom/digester.py tests/test_digester.py
git commit -m "feat(digester): emit ## category headers on boundary in format_items_for_prompt

Phase-2a §5.1: linear pass-through with one ## {CategoryLabel} marker
per category-change boundary. Items still arrive pre-sorted from SQL
(category priority + importance/published_at/title); this function
does not re-sort or re-group. _build_fallback_digest gains the same
boundary logic.

Note: one Phase-1 test (preserves_input_order) now fails because the
AI-only digest is wrapped in '## AI/LLM/ML' — fix is in the next
commit (per spec §7.1 documented Phase-1 test edit)."
```

---

## Task 9: Update digest prompts to mirror input H2 markers

The digest_morning.md and digest_evening.md prompts need to:
1. Acknowledge that input may contain `## Weltgeschehen` / `## AI/LLM/ML` markers.
2. Mirror those markers in the output, in the same order.
3. Keep the existing "no `### subcat` headers in output" rule but tighten it to only forbid *subcategory* headers.

**Files:**
- Modify: `config/prompts/digest_morning.md`
- Modify: `config/prompts/digest_evening.md`

- [ ] **Step 1: Edit digest_morning.md**

In `config/prompts/digest_morning.md`, find the rule:

```
- **Keine Zwischenüberschriften.** Items fließen als ein Strom direkt unter dem Top-Header — keine `## …`-Sub-Kategorie-Header.
```

Replace with:

```
- **Top-Level-Struktur des Inputs übernehmen.** Der Input enthält ein oder mehrere `## Weltgeschehen` / `## AI/LLM/ML` H2-Marker, die Items in Top-Level-Categories trennen. Spiegele diese H2-Header 1:1 in deinem Output, in der Input-Reihenfolge, mit den jeweiligen Items darunter. Wenn nur eine Category vorhanden ist, gib nur einen H2 aus.
- **Keine Subcategory-Zwischenüberschriften.** Items fließen als Strom unter ihrem `## {Category}`-Header — keine zusätzlichen `## subcat`-Header (`## lab`, `## breaking` etc.) und keine `### subcat`-Header. Nur die zwei Top-Level-Categories sind erlaubt.
```

In the **Output-Struktur** template, change

```markdown
# News-Digest {{ date_de }} (Morgen)

### Deutsche Headline für das Item
```

to

```markdown
# News-Digest {{ date_de }} (Morgen)

## Weltgeschehen

### Deutsche Headline für das Item
```

(The example now shows the H2 explicitly. If only AI items are present, the example should clarify that `## Weltgeschehen` is omitted; add a one-liner: "Bei Welt-only-Tagen entfällt `## AI/LLM/ML`; bei AI-only-Tagen entfällt `## Weltgeschehen`.")

- [ ] **Step 2: Apply the same edits to digest_evening.md**

Same two replacements in `config/prompts/digest_evening.md`.

- [ ] **Step 3: Smoke-render the prompts (no LLM call, just string substitution)**

```bash
grep -c '## Weltgeschehen\|## AI/LLM/ML' config/prompts/digest_morning.md \
    config/prompts/digest_evening.md
```

Expected: ≥4 hits across both files (template + rule + tightened rule).

- [ ] **Step 4: Run the full suite — should still be 1 broken Phase-1 test from Task 8**

```bash
uv run pytest -q
```

Expected: same one failure as after Task 8 (`preserves_input_order`). Other tests untouched (the prompts are loaded at runtime via `agent_client.ask`, mocked in unit tests).

- [ ] **Step 5: Commit**

```bash
git add config/prompts/digest_morning.md config/prompts/digest_evening.md
git commit -m "feat(prompts): instruct digest prompts to mirror ## category headers

Phase-2a §5.3: input now contains ## Weltgeschehen / ## AI/LLM/ML H2
markers from format_items_for_prompt. Prompts must reproduce them
1:1 in the output. The acc25c5 'no subcategory headers' rule stays
in effect but is tightened to only forbid ### subcat / ## subcat
markers, not the new top-level ## category markers."
```

---

## Task 10: Fix Phase-1 format_items test wrapping in AI H2

The `test_format_items_for_prompt_preserves_input_order` test from `07078af` (now `a88cac1`) asserts the AI-only output has no `### lab` / `### curated`. After Task 8, AI-only output has `## AI/LLM/ML` wrapping it — the test must accept that.

**Files:**
- Modify: `tests/test_digester.py`

- [ ] **Step 1: Locate the test**

```bash
grep -n 'test_format_items_for_prompt_preserves_input_order' tests/test_digester.py
```

- [ ] **Step 2: Update the assertions**

Change the test body to also assert the AI H2 wrapper is present:

```python
@freeze_time("2026-04-19 22:30:00")
def test_format_items_for_prompt_preserves_input_order(populated_state: State) -> None:
    """Items must render flat in the given input order — no subcat grouping,
    no re-sorting. Sort responsibility lives in SQL (`list_items_for_digest`).

    Phase-2a addition: AI-only output is wrapped in a single `## AI/LLM/ML`
    H2 header (the category-boundary insertion fires once at the start).
    """
    populated_state.upsert_source(
        Source(
            name="simon",
            category="ai",
            subcategory="curated",
            url="https://simonwillison.net/atom/everything/",
            feed_type="atom",
            interval_seconds=3600,
            enabled=True,
        )
    )
    src_id = populated_state.get_source_by_name("simon")["id"]
    populated_state.insert_item(
        source_id=src_id,
        item_hash="h-simon",
        url="https://simonwillison.net/x",
        title="Simon Post",
        author="Simon",
        published_at=None,
        raw_summary="body",
        category="ai",
    )
    item_id = populated_state.list_items_by_status("new", limit=1)[0]["id"]
    populated_state.mark_item_scored(item_id=item_id, importance=4, reason="curated", model="haiku")
    items = populated_state.list_items_for_digest(since_iso="2026-04-01T00:00:00")
    formatted = format_items_for_prompt(items)

    # Phase 2a: one ## AI/LLM/ML header at the top, no subcategory headers.
    assert formatted.count("## AI/LLM/ML") == 1
    assert "### lab" not in formatted
    assert "### curated" not in formatted
    assert "## Weltgeschehen" not in formatted

    # Each input item appears exactly once, in the SQL-defined order.
    titles_in_order = [item["title"] for item in items]
    positions = [formatted.find(t) for t in titles_in_order]
    assert all(p >= 0 for p in positions), "every item title must be rendered"
    assert positions == sorted(positions), "items must appear in input order"
```

- [ ] **Step 3: Run the full suite — should be all green**

```bash
uv run pytest -q
```

Expected: 211+ passed (Phase-1 199 + Tasks 2/3/4/6/7/8 additions + this fix).

- [ ] **Step 4: Lint and dead-code check**

```bash
uv run ruff format src/newsroom/ tests/
uv run ruff check src/newsroom/ tests/
uv run vulture src/
```

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_digester.py
git commit -m "test(digester): align preserves_input_order with Phase-2a AI H2 wrapper

Per spec §7.1 documented Phase-1 test edit: AI-only digest output
now starts with '## AI/LLM/ML' (the category-boundary insertion
fires once at the start). The test asserts the wrapper is present
exactly once and no subcategory headers leak."
```

---

## Task 11: Add Welt sources to sources.yaml

Use the URLs verified in Task 1. If a candidate failed verification, fall back per Task 1's recorded decision.

**Files:**
- Modify: `config/sources.yaml`
- Modify: `tests/test_config.py` (or add to it)

- [ ] **Step 1: Append Welt sources to sources.yaml**

Add the following block at the end of `config/sources.yaml` (adjust URLs based on Task 1 verification outcomes):

```yaml
  # Phase 2a — Weltgeschehen
  # ─── breaking (1) ───────────────────────────────
  - name: tagesschau-eilmeldungen
    category: world
    subcategory: breaking
    url: https://www.tagesschau.de/eilmeldungen/index~rss2.xml
    feed_type: rss
    interval_seconds: 300
    enabled: true

  # ─── news (3) ───────────────────────────────────
  - name: tagesschau-news
    category: world
    subcategory: news
    url: https://www.tagesschau.de/index~rss2.xml
    feed_type: rss
    interval_seconds: 1800
    enabled: true
  - name: bbc-world
    category: world
    subcategory: news
    url: http://feeds.bbci.co.uk/news/world/rss.xml
    feed_type: rss
    interval_seconds: 3600
    enabled: true
  - name: reuters-world
    category: world
    subcategory: news
    url: <RESOLVED-IN-TASK-1>
    feed_type: <rss|sitemap-scrape>
    interval_seconds: 3600
    enabled: true

  # ─── analysis (2) ───────────────────────────────
  - name: zeit-politik
    category: world
    subcategory: analysis
    url: https://newsfeed.zeit.de/politik/index
    feed_type: rss
    interval_seconds: 7200
    enabled: true
  - name: politico-eu
    category: world
    subcategory: analysis
    url: https://www.politico.eu/feed/
    feed_type: rss
    interval_seconds: 7200
    enabled: true
```

If `tagesschau-eilmeldungen` is dead per Task 1 step 3: remove that entry and instead implement variant 1 (item-level `Eilmeldung:` heuristic in `fetcher.py` — out of scope of this task; would be a separate follow-up commit before sources.yaml is enabled in production).

If `reuters-world` cannot be resolved: remove it. The lean budget allows starting with 5 sources.

- [ ] **Step 2: Write a config validation test**

Add to `tests/test_config.py`:

```python
def test_sources_yaml_has_world_category() -> None:
    """Phase 2a: sources.yaml contains at least 5 sources with category=world."""
    from pathlib import Path
    sources = load_sources(Path("config/sources.yaml"))
    world_sources = [s for s in sources if s.category == "world"]
    assert len(world_sources) >= 5, f"Expected ≥5 world sources, got {len(world_sources)}"

    subcategories = {s.subcategory for s in world_sources}
    assert "news" in subcategories
    assert "analysis" in subcategories
    # 'breaking' is optional (depends on Task 1 fallback decision)
```

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/test_config.py::test_sources_yaml_has_world_category -v
```

Expected: PASS.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest -q
```

Expected: 212+ passed.

- [ ] **Step 5: Smoke-test the fetcher on one Welt source (read-only, no DB write)**

```bash
uv run python -c "
import asyncio
from newsroom.config import load_sources
from newsroom.fetcher import _fetch_one  # private, but useful for dry sanity
from pathlib import Path

sources = load_sources(Path('config/sources.yaml'))
welt = next(s for s in sources if s.category == 'world')
print(f'Smoke-fetching {welt.name} from {welt.url}')
items = asyncio.run(_fetch_one(welt))
print(f'Got {len(items)} items')
for item in items[:3]:
    print(f'  - {item[\"title\"][:80]}')
"
```

Expected: at least one item printed. If zero or HTTPError: revisit Task 1 verification.

(If `_fetch_one` is not the actual private function name, substitute `fetch_pending_sources` with a dry-run flag or use a minimal manual `httpx.get` — the goal is to confirm the URL responds.)

- [ ] **Step 6: Commit**

```bash
git add config/sources.yaml tests/test_config.py
git commit -m "feat(sources): add Phase-2a Weltgeschehen sources

Six sources across breaking/news/analysis subcategories — see
spec §3.2 for source rationale. URLs verified in Task 1
(see docs/superpowers/plans/2026-05-07-phase2a-source-verification.md)."
```

---

## Task 12: Integration test — end-to-end mixed Welt + AI digest

Exercise the whole pipeline (mock fetch + scoring) and assert the digest file contains both H2 sections in the correct order.

**Files:**
- Modify: `tests/test_integration.py` (or `tests/test_digester.py` if no integration file exists)

- [ ] **Step 1: Inspect existing integration tests**

```bash
ls tests/
grep -l 'integration\|end.to.end' tests/ 2>/dev/null
```

If `tests/test_integration.py` exists, add to it. If not, append to `tests/test_digester.py` with a clear `# ── Phase 2a integration ──` separator.

- [ ] **Step 2: Write the integration test**

```python
@freeze_time("2026-05-08 22:30:00")
async def test_mixed_world_and_ai_digest_writes_both_sections(
    tmp_path: Path,
) -> None:
    """End-to-end: 2 ai + 2 world items → digest file has Welt H2 above AI H2."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()

    for name, cat, sub in [
        ("anthropic", "ai", "lab"),
        ("tagesschau-news", "world", "news"),
    ]:
        state.upsert_source(
            Source(
                name=name, category=cat, subcategory=sub,
                url=f"https://example.com/{name}.rss",
                feed_type="rss", interval_seconds=3600, enabled=True,
            )
        )

    ai_id = state.get_source_by_name("anthropic")["id"]
    world_id = state.get_source_by_name("tagesschau-news")["id"]

    for i, (sid, cat, title) in enumerate([
        (world_id, "world", "Bundestag verabschiedet X"),
        (world_id, "world", "EZB senkt Zins"),
        (ai_id, "ai", "Claude 5 released"),
        (ai_id, "ai", "OpenAI o3 GA"),
    ]):
        state.insert_item(
            source_id=sid, item_hash=f"h{i}",
            url=f"https://example.com/{i}",
            title=title, author=None,
            published_at="2026-05-08T08:00:00Z",
            raw_summary=f"body {i}", category=cat,
        )

    for item in state.list_items_by_status("new", limit=10):
        state.mark_item_scored(item_id=item["id"], importance=4, reason="r", model="haiku")

    mock_agent = AsyncMock()
    # Opus mock: just echo a digest with the two H2 headers preserved
    mock_agent.ask.return_value = (
        "# News-Digest 8. May 2026 (Abend)\n\n"
        "## Weltgeschehen\n\n"
        "### Bundestag verabschiedet X\nProsa.\n\n"
        "### EZB senkt Zins\nProsa.\n\n"
        "## AI/LLM/ML\n\n"
        "### Claude 5 released\nProsa.\n\n"
        "### OpenAI o3 GA\nProsa.\n"
    )

    output_root = tmp_path / "news"
    await generate_digest(
        state=state,
        slot="evening",
        date=datetime(2026, 5, 8).date(),
        output_root=output_root,
        agent=mock_agent,
    )

    # The agent received items_markdown with both H2s in the right order
    items_md = mock_agent.ask.call_args.kwargs["variables"]["items_markdown"]
    welt_idx = items_md.find("## Weltgeschehen")
    ai_idx = items_md.find("## AI/LLM/ML")
    assert welt_idx >= 0 and ai_idx >= 0
    assert welt_idx < ai_idx, "Welt H2 must come before AI H2 in items_markdown"

    # The output file has both H2s
    out = output_root / "2026" / "05" / "2026-05-08.md"
    body = out.read_text()
    assert "# News-Digest 8. May 2026 (Abend)" in body
    assert body.count("## Weltgeschehen") == 1
    assert body.count("## AI/LLM/ML") == 1
```

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/test_digester.py::test_mixed_world_and_ai_digest_writes_both_sections -v
```

Expected: PASS (Tasks 3, 8, and 9 cover the underlying behaviour).

- [ ] **Step 4: Commit**

```bash
git add tests/test_digester.py
git commit -m "test(integration): mixed Welt+AI digest end-to-end

Asserts that with both categories present, items_markdown delivered
to Opus has '## Weltgeschehen' before '## AI/LLM/ML', and the written
digest file mirrors that ordering."
```

---

## Task 13: Integration test — Welt breaking push fires at imp=3

Closes the loop on the central Phase-2a UX change: an editorially-pre-filtered breaking item with importance=3 must produce a push during daytime.

**Files:**
- Modify: `tests/test_notifier.py`

- [ ] **Step 1: Write the integration test**

```python
@freeze_time("2026-05-08 14:00:00")
async def test_world_breaking_imp3_daytime_pushes(tmp_path: Path) -> None:
    """Phase 2a UX: a tagesschau breaking with imp=3 produces a push, no quiet hour."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="tagesschau-eilmeldungen",
            category="world",
            subcategory="breaking",
            url="https://www.tagesschau.de/eilmeldungen/index~rss2.xml",
            feed_type="rss", interval_seconds=300, enabled=True,
        )
    )
    src_id = state.get_source_by_name("tagesschau-eilmeldungen")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h-eil",
        url="https://www.tagesschau.de/eil/x",
        title="Eilmeldung: Bundeskanzler tritt zurück", author=None,
        published_at="2026-05-08T13:55:00Z",
        raw_summary="Eil-Body", category="world",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=3, reason="eil", model="haiku")

    captured: list[dict] = []
    async def fake_send(*, title: str, message: str, url: str | None) -> None:
        captured.append({"title": title, "message": message, "url": url})

    notifier = Notifier(send_fn=fake_send)
    item = state.get_item_with_source(item_id)
    await notifier.maybe_notify(item, state)

    assert len(captured) == 1, "Welt-breaking imp=3 must push at daytime"
    assert captured[0]["title"].startswith("[Welt] tagesschau-eilmeldungen")


@freeze_time("2026-05-08 14:00:00")
async def test_world_news_imp3_daytime_does_not_push(tmp_path: Path) -> None:
    """Counter-test: a non-breaking world item with imp=3 must NOT push."""
    state = State(tmp_path / "t.db")
    state.ensure_schema()
    state.upsert_source(
        Source(
            name="tagesschau-news",
            category="world",
            subcategory="news",
            url="https://www.tagesschau.de/index~rss2.xml",
            feed_type="rss", interval_seconds=1800, enabled=True,
        )
    )
    src_id = state.get_source_by_name("tagesschau-news")["id"]
    state.insert_item(
        source_id=src_id, item_hash="h-n",
        url="https://www.tagesschau.de/x",
        title="Routine-Statement", author=None,
        published_at="2026-05-08T13:55:00Z",
        raw_summary="body", category="world",
    )
    item_id = state.list_items_by_status("new", limit=1)[0]["id"]
    state.mark_item_scored(item_id=item_id, importance=3, reason="r", model="haiku")

    captured: list[dict] = []
    async def fake_send(*, title: str, message: str, url: str | None) -> None:
        captured.append({"title": title, "message": message, "url": url})

    notifier = Notifier(send_fn=fake_send)
    item = state.get_item_with_source(item_id)
    await notifier.maybe_notify(item, state)

    assert len(captured) == 0, "Welt-news imp=3 must NOT push (threshold is 4)"
```

- [ ] **Step 2: Run the tests**

```bash
uv run pytest tests/test_notifier.py -k 'world_breaking_imp3_daytime or world_news_imp3_daytime' -v
```

Expected: both PASS (covered by Task 6's threshold table + Task 7's title prefix).

- [ ] **Step 3: Run the full suite**

```bash
uv run pytest -q
```

Expected: 215+ passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_notifier.py
git commit -m "test(integration): Welt breaking imp=3 pushes, news imp=3 does not

Closes the loop on the central Phase-2a UX change: an editorially-
pre-filtered breaking source with importance=3 produces a daytime
push (threshold 3); a non-breaking world item at the same score
does not (threshold 4 via wildcard)."
```

---

## Task 14: Update CLAUDE.md (Phase Roadmap, Common Gotchas)

Per spec §7.3 documentation requirements.

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Phase Roadmap section**

Find the `## Phase Roadmap` section (around the bottom of `CLAUDE.md`). The Phase-1 spec said:

```
- **Phase 1 (this):** AI/LLM/ML only. 15 sources + Claude Code releases.
- **Phase 2:** Add Weltgeschehen + Dresden (incl. Dresden-Science: …).
- **Phase 3:** Tech, Wissenschaft (Physik/Chemie/Astro), APOD.
```

Replace with:

```
- **Phase 1 (shipped):** AI/LLM/ML, 15 sources, twice-daily digest with H1 slot headers (`# News-Digest <date> (Morgen|Abend)`), arxiv-imp=5-only push policy. Live since 2026-04-21.
- **Phase 2a (in progress):** Weltgeschehen — second top-level category alongside `ai`. 5–7 sources in breaking/news/analysis subcategories. Per-category score prompt; subcategory-driven push thresholds via `NOTIFICATION_THRESHOLDS` table; two-section digest layout (`## Weltgeschehen` + `## AI/LLM/ML` under each slot's H1). See spec `docs/superpowers/specs/2026-05-07-daily-newsroom-phase2a-design.md`.
- **Phase 2b (next):** Dresden + Dresden-Science (TU Dresden, MPI-CBG, MPI-PKS, HZDR Excellence Cluster). Builds on Phase-2a mechanism — mostly YAML + prompt edits.
- **Phase 3:** Tech, Wissenschaft (Physik/Chemie/Astro), APOD.
```

- [ ] **Step 2: Add a Common Gotchas entry on score-prompt routing**

In the `## Common Gotchas` section, append:

```
- **Score-prompt routing is per-category.** `scorer.py` builds the prompt
  name as `f"score_item_{source_category}"`. A new category (e.g. `dresden`,
  `tech`) MUST come with a matching `config/prompts/score_item_<category>.md`
  file or the agent_client will raise `KeyError` / `FileNotFoundError` on
  every item from that category. Side effect: items with `category` typos
  (e.g. `worldd`) silently fail with a log warning, not a hard error.
```

- [ ] **Step 3: Run the test suite (sanity — CLAUDE.md changes shouldn't break anything)**

```bash
uv run pytest -q
```

Expected: still 215+ passed.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude): update Phase Roadmap + score-prompt-routing gotcha

Phase 2a in progress; Phase 2b (Dresden) marked as next. Added a
Common Gotchas entry that future-category additions must come with
a corresponding score_item_<category>.md prompt file."
```

---

## Task 15: Pause launchd, run Phase-2a end-to-end smoke, re-enable

A live smoke before declaring DoD. The test suite covered every unit, but the spec §7.3 also asks for a real `newsroom` invocation that hits the LLM and writes to `state.db`.

**Files:** none — operational verification only.

- [ ] **Step 1: Pause the fetch launchd job (avoid concurrent writes)**

```bash
launchctl unload ~/Library/LaunchAgents/de.svenpoeche.newsroom.fetch.plist
launchctl list | grep newsroom.fetch && echo STILL_LOADED || echo UNLOADED
```

Expected: `UNLOADED`. Digest jobs left alone.

- [ ] **Step 2: Run a one-off fetch from the worktree to populate Welt items**

```bash
cd /Users/valgard/Projects/private/daily-newsroom/.worktrees/phase2a
uv run python -m newsroom fetch 2>&1 | tail -30
```

Expected: log lines showing fetches for the new world sources, items inserted, scoring kicks in. Watch for `score_item_world` prompt loading and `prompt_name=score_item_world` lines if logging is verbose.

- [ ] **Step 3: Inspect the resulting state**

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
    "SELECT category, subcategory, COUNT(*), AVG(importance)
       FROM items
       JOIN sources ON items.source_id = sources.id
      WHERE items.fetched_at >= datetime('now', '-1 hour')
      GROUP BY category, subcategory
      ORDER BY category, subcategory;"
```

Expected: at least `world / breaking | world / news | world / analysis` rows present, each with `importance` averages plausibly between 1.5 and 4.0.

- [ ] **Step 4: Generate a manual digest with --force**

```bash
uv run python -m newsroom digest --force 2>&1 | tail -10
```

Expected: digest written to `~/Documents/!AI/news/YYYY/MM/YYYY-MM-DD.md`. Log shows `digest_finalized` event with `item_count > 0`.

- [ ] **Step 5: Visually inspect the digest**

```bash
TODAY=$(date +%Y-%m-%d)
DIGEST="$HOME/Documents/!AI/news/$(date +%Y)/$(date +%m)/$TODAY.md"
test -s "$DIGEST" && head -50 "$DIGEST"
```

Manual check:
- File starts with `# News-Digest <date_de> (Morgen|Abend)` (depending on time of day).
- Contains `## Weltgeschehen` somewhere below the H1 (if any Welt items were fetched).
- Contains `## AI/LLM/ML` (existing AI sources still produce items).
- No `### lab` / `### breaking` subcategory headers anywhere in the body.

If any of these fail: the issue is in Tasks 8/9 — debug and re-iterate before re-enabling launchd.

- [ ] **Step 6: Re-enable the fetch job**

```bash
launchctl load ~/Library/LaunchAgents/de.svenpoeche.newsroom.fetch.plist
launchctl list | grep newsroom.fetch
```

Expected: job listed, status `-` until next 5-min trigger.

- [ ] **Step 7: Watch the next trigger to confirm autonomous run**

```bash
tail -f ~/Library/Logs/newsroom/fetch.log &
TAIL_PID=$!
sleep 360  # ~6 minutes
kill $TAIL_PID
```

Expected: at least one full fetch cycle within 6 minutes; no errors; Welt sources fetch successfully.

- [ ] **Step 8: Mark DoD-Live items in the spec**

The spec §7.3 has a 7-day-live-test checkbox. Note today's date in the spec body (or in a `docs/superpowers/plans/2026-05-07-phase2a-live-test-log.md` file) and revisit after 7 days to verify push S/N, daily push count, score distribution.

~~~bash
cat > docs/superpowers/plans/2026-05-07-phase2a-live-test-log.md <<'EOF'
# Phase 2a Live-Test Log (start 2026-05-07)

7-day window: 2026-05-07 → 2026-05-14

## Daily checkpoints

| Day | Welt pushes | AI pushes | Push S/N (subj.) | Notes |
|-----|---|---|---|---|
| 2026-05-07 | | | | |
| 2026-05-08 | | | | |
| 2026-05-09 | | | | |
| 2026-05-10 | | | | |
| 2026-05-11 | | | | |
| 2026-05-12 | | | | |
| 2026-05-13 | | | | |

## End-of-window distribution check (run on 2026-05-14)

```bash
sqlite3 ~/Library/Application\ Support/daily-newsroom/state.db \
    "SELECT importance, COUNT(*)
       FROM items
       JOIN sources ON items.source_id = sources.id
      WHERE sources.category = 'world'
        AND items.scored_at >= '2026-05-07'
      GROUP BY importance ORDER BY importance;"
```

Targets (loose bands per spec §7.3):
- ≥1 % at 5
- ≥2 % at 4
- ≥15 % at 3
- ≥50 % at 2
- ≥10 % at 1
EOF

git add docs/superpowers/plans/2026-05-07-phase2a-live-test-log.md
git commit -m "docs(plan): start Phase-2a live-test log (7-day window)"
~~~

- [ ] **Step 9: Merge the worktree branch back to main**

After the live-test log is committed and the smoke ran clean, fast-forward main:

```bash
cd /Users/valgard/Projects/private/daily-newsroom
git merge --ff-only phase2a
git push origin main
```

If non-fast-forward: rebase the phase2a branch on main first (per CLAUDE.md `git rebase` preference for merging).

- [ ] **Step 10: Remove the worktree (CHANGE CWD FIRST per CLAUDE.md)**

```bash
cd /Users/valgard/Projects/private/daily-newsroom
git worktree remove .worktrees/phase2a
git branch -d phase2a
```

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| Tagesschau-Eilmeldungen URL is dead at Task 1 verification | Variant 1 fallback (item-level `Eilmeldung:` heuristic in fetcher) — out of scope of this plan, would be a follow-up commit. Variant 2 fallback (drop breaking entirely) keeps Phase 2a shippable with 5 sources. |
| Reuters/AP RSS unavailable | Drop `reuters-world` from Task 11; lean budget honoured at 5 sources. |
| Notifier table migration regresses arxiv push behaviour | Task 6 Steps 2–3 lock the contract with explicit Phase-1 regression tests BEFORE migrating. If those tests fail post-migration, the table or resolve function is wrong. |
| SQL CASE in `list_items_for_digest` introduces query-plan regression | Negligible — the CASE is on an indexed-able expression. EXPLAIN QUERY PLAN in Task 3 if anything looks slow. |
| Phase-1 AI tests break unexpectedly | Each touched module has explicit Phase-1 regression tests (Task 6, Task 10). The full suite runs after every commit. |
| 7-day live test reveals score-distribution mismatch | Spec §7.3 has loose bands; minor deviations are expected. Tighten via `score_item_world.md` edit (PR), not a new spec. |
| Welt push volume floods Notification Center | Open Question 8.2.A in spec triggers a `MAX_PUSHES_PER_WINDOW` patch — out of scope of this plan, follow-up commit. |
| Worktree cwd-deletion bug if executor forgets `cd` before `git worktree remove` | Task 15 Step 10 explicitly cd's to the main repo first. |

---

## Self-Review

**Spec coverage:**

| Spec section | Plan task |
|---|---|
| §1.4 second top-level category `world` | Tasks 4, 11 |
| §1.4 5–7 new sources | Tasks 1, 11 |
| §1.4 extended digest layout (H2 sections) | Tasks 8, 9, 10 |
| §1.4 subcategory-driven push threshold | Task 6 |
| §1.4 Phase-2b-ready architecture | Tasks 4, 6, 8 (each adds an extension point) |
| §1.5 no DB schema change | confirmed — no `state.ensure_schema` modifications |
| §2.2 module diff (state, scorer, notifier, digester, prompts, sources.yaml) | Tasks 2, 3, 4, 5, 6, 7, 8, 9, 11 |
| §3.1 subcategory operational definitions | Task 11 (sources.yaml) + Task 5 prompt content |
| §3.2 source candidates | Tasks 1, 11 |
| §3.3 RSS availability fallbacks | Task 1 |
| §4.2 Welt scale 1–5 | Task 5 prompt body |
| §5.1 format_items_for_prompt diff | Task 8 |
| §5.2 SQL category-priority sort | Task 3 |
| §5.3 digest prompts mirror H2 | Task 9 |
| §5.4 fallback digest | Task 8 (Step 5 _build_fallback_digest) |
| §6.1 NOTIFICATION_THRESHOLDS table | Task 6 |
| §6.2 resolve function | Task 6 |
| §6.3 title prefix | Task 7 |
| §7.1 unit tests | Tasks 2, 3, 4, 6, 7, 8, 11 |
| §7.1 integration tests | Tasks 12, 13 |
| §7.1 Phase-1 test edits | Task 10 |
| §7.3 DoD documentation | Tasks 14, 15 |
| §7.3 7-day live test | Task 15 |
| §8.4 CLAUDE.md updates | Task 14 |

No gaps identified.

**Placeholder scan:** No "TBD", "TODO", "implement later", "similar to Task N" left. Task 11 has `<RESOLVED-IN-TASK-1>` for the Reuters URL — that is intentional and Task 1 explicitly produces the value.

**Type consistency:**
- `source_category` is the column alias used in Tasks 2, 3, 4, 8 — consistent.
- `CATEGORY_LABEL` dict defined in Task 8, used in Task 8 only — no naming drift.
- `NOTIFICATION_THRESHOLDS` defined in Task 6, used in Task 6 only — consistent.
- `compute_threshold_for_hour(hour, *, category, subcategory)` signature unchanged across Tasks 6 and 7.
- Test fixture `populated_state` reused across Tasks 8, 10, 12 — same shape (`category="ai"`, `subcategory="lab"`, importance 5/4/3 items).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-daily-newsroom-phase2a.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration.
**2. Inline Execution** — execute tasks here using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
