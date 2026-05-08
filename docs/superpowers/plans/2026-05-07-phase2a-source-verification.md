# Phase-2a Source URL Verification (2026-05-07)

## Summary

Verification of the six candidate sources for Phase 2a (Weltgeschehen scope). All URLs tested via `curl` on 2026-05-08 with `Mozilla/5.0` user-agent.

## Results Table

| Source | URL | HTTP | Decision |
|---|---|---|---|
| tagesschau-eilmeldungen | https://www.tagesschau.de/eilmeldungen/index~rss2.xml | 404 | fallback variant 1 (item-level heuristic) |
| tagesschau-news | https://www.tagesschau.de/index~rss2.xml | 200 | use |
| bbc-world | http://feeds.bbci.co.uk/news/world/rss.xml | 302 (→ 200) | use (follow redirects) |
| zeit-politik | https://newsfeed.zeit.de/politik/index | 200 | use |
| politico-eu | https://www.politico.eu/feed/ | 200 | use |
| reuters-world | https://openrss.org/www.reuters.com/world/ | 200 | use (openrss mirror) |

## Fallback Decisions

### Tagesschau Breaking News (`tagesschau-eilmeldungen`)

**URL Status:** 404 on dedicated feed endpoint `https://www.tagesschau.de/eilmeldungen/index~rss2.xml`

**Fallback Strategy:** Variant 1 (item-level heuristic)
- Verified that the main feed (`tagesschau-news` at HTTP 200) does **not** contain Eilmeldung-prefixed items in the current news cycle (grep count: 0).
- Per spec §3.3, this is inconclusive regarding heuristic viability — the absence of breaking news today does not prove the pattern is dead.
- Recommendation: **Proceed with variant 1** (filter tagesschau-news by title prefix `Eilmeldung:` in the digester scoring logic). This is low-risk because:
  1. It costs nothing to try (no additional fetch required).
  2. If the pattern disappears entirely, we simply get zero breaking items, not errors.
  3. If breaking news resumes with the Eilmeldung prefix, the heuristic works.
- **Fallback to variant 2** (drop breaking segment) only if the production digester observes zero Eilmeldung items over a 14-day observation window.

### Reuters World

**URL Status:** Multiple candidates tested:
- Legacy RSS: `https://www.reutersagency.com/feed/?best-topics=world&post_type=best` → **301 redirect** (unreliable)
- sitemap.xml: `https://www.reuters.com/sitemap.xml` → **401 Unauthorized** (blocked)
- OpenRSS mirror: `https://openrss.org/www.reuters.com/world/` → **200 OK** ✓

**Decision:** Use the **OpenRSS mirror** (`https://openrss.org/www.reuters.com/world/`). 
- Mirrors are lower-fidelity (potential lag, limited reliability guarantees) but acceptable for Phase 2a.
- AP World (fallback) at `https://openrss.org/apnews.com/world-news` also returns 200, in reserve if Reuters degrades.

## Final Source Count for Phase 2a

**5 sources land in `sources.yaml` for Phase 2a (Plan-Task 11):**
1. tagesschau-news (main)
2. bbc-world
3. zeit-politik
4. politico-eu
5. reuters-world (via openrss.org mirror)

The **`tagesschau-breaking` heuristic** (variant 1) is **deferred to a follow-up commit** — it requires a fetcher-level title-prefix filter that is out of scope for Phase 2a's mechanism work. The `(world, breaking)` row remains in `NOTIFICATION_THRESHOLDS` (3, 4) so the wiring is ready when the heuristic lands; until then, the row is simply unreachable.

These 5 sources are added to `sources.yaml` in Plan-Task 11 with the categories/subcategories/intervals from spec §3.1.

## Notes for Implementation

- **BBC redirect:** The initial 302 redirect resolves to a valid feed (final HTTP 200). The fetcher should follow redirects by default.
- **Reuters mirror lag:** OpenRSS mirrors typically lag 5–30 minutes behind the original source. This is acceptable for world news aggregation.
- **Tagesschau heuristic viability:** The inconclusive grep result (0 Eilmeldung items today) is noted. If production observation shows zero breaking items after 2 weeks, the digester should emit a monitoring alert and prepare for variant 2 fallback.
