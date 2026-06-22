# Design: Kategorie-getrennte Digest-Dateien

- **Datum:** 2026-06-22
- **Status:** Proposed
- **Scope:** Reiner Output-Split der Digest-Markdown-Dateien pro Top-Level-Kategorie
  (`ai`, `world`). Keine Änderung an Erzeugung, Slots, Timing oder Scoring.

## Problem

Der Digest schreibt heute beide Top-Level-Kategorien (`ai`, `world`) **gemischt in
eine Datei** pro Tag (`~/Documents/!AI/news/{YYYY}/{MM}/{YYYY-MM-DD}.md`), getrennt
nur durch H2-Sections (`## Weltgeschehen`, `## AI/LLM/ML`). Der Nutzer will die
Themen getrennt konsumieren — je Kategorie eine eigene Datei.

## Ziel & Nicht-Ziel

**Ziel:** Pro Slot zwei Dateien statt einer — eine je Top-Level-Kategorie. Je
nicht-leerer Kategorie ein Notification-Ping, der die zugehörige Datei öffnet.

**Nicht-Ziel (YAGNI):**
- Kein Feature-Flag/Toggle — der Split ist das neue Default-Verhalten.
- Keine getrennten Zeitpläne/Slots pro Kategorie (Erzeugung bleibt ein Slot-Claim,
  ein LLM-Call, zwei Pings).
- Keine DB-Schema-Migration.

## Architektur-Kerngedanke

`generate_digest()` bleibt strukturell unverändert: **ein** `claim_digest_slot()`,
**ein** LLM-Call, **eine** `digests`-Zeile pro `(date, slot)`. Die
`UNIQUE(date, slot)`-Invariante und die Idempotenz bleiben unberührt.

Der Split passiert ausschließlich im **Schreib-Schritt**: die zurückgegebenen Items
werden nach `source_category` partitioniert; pro nicht-leerer Partition wird eine
suffixierte Datei gerendert und geschrieben, danach pro geschriebener Datei ein Ping
gefeuert.

## Datei-Layout

- Pfad: `{output_root}/{YYYY}/{MM}/{YYYY-MM-DD}_{category}.md`
- `category` ist der **interne Key** (`ai`, `world`) — stabil, klein, keine Umlaute/
  Leerzeichen.
- Beispiel Morgen-Slot 2026-06-22:
  - `~/Documents/!AI/news/2026/06/2026-06-22_ai.md`
  - `~/Documents/!AI/news/2026/06/2026-06-22_world.md`

Begründung Suffix (statt Unterordner): kleinster Eingriff in die Pfadlogik, beide
Themen sortieren chronologisch im selben Monatsordner, „alles von heute" bleibt mit
einem Blick sichtbar.

## Rendering & Heading-Hierarchie

Da die Datei *die* Kategorie ist, entfällt die bisherige
`## Weltgeschehen` / `## AI/LLM/ML`-Kategorie-Section. Die dadurch entstehende
Ebenen-Lücke wird geschlossen, indem die Item-Headlines eine Ebene hochrücken
(H3 → H2). Die Kategorie wandert ins H1.

Pro-Datei-Hierarchie:

~~~markdown
# News-Digest 22. Juni 2026 — AI/LLM/ML (Morgen)   <!-- H1: Datum + Kategorie + Slot -->
## {Headline Item 1}                                <!-- H2: News (von H3 hochgezogen) -->
- [ ] interessiert mich
<prose>

> <quote?>

[Weiterlesen →](url) · *source · time · Importance N*
## {Headline Item 2}
…
---                                                 <!-- Morgen/Abend-Trenner, nur Abend-Append -->
# News-Digest 22. Juni 2026 — AI/LLM/ML (Abend)
## {Headline} …
~~~

Konkrete Render-Änderungen:
- **H1-Format** (`_format_top_header`): Kategorie-Label wird eingezogen:
  `# News-Digest {date_de} — {CATEGORY_LABEL[category]} ({Slot-Label})`.
- **Kategorie-Section entfällt** (`_render_digest`): kein `## {CATEGORY_LABEL}`-Loop
  mehr; `_render_digest` rendert genau eine Kategorie.
- **Item-Headline H3 → H2** (`_render_item`): `### {headline}` → `## {headline}`.
- Die `- [ ] interessiert mich`-Checkbox, der Prose-Body, das optionale Quote und die
  Meta-Zeile (`[Weiterlesen →] · *source · time · Importance N*`) bleiben unverändert.

**Invariante respektiert:** Der `---` Morgen/Abend-Separator bleibt ausschließlich von
`_write_digest_file` erzeugt — niemals vom Renderer oder Prompt (CLAUDE.md).

## Schreib-Logik (`_write_digest_file`)

Unverändertes Verhalten, nur **pro Kategorie-Datei** statt einmal:
- **Morgen** (`slot == "morning"`): jeweilige Kategorie-Datei überschreiben oder neu
  anlegen.
- **Abend** (`slot == "evening"`): existierende Morgen-Datei lesen, mit `\n\n---\n\n`
  appenden. Fehlt die Morgen-Datei (Kategorie war morgens leer), wird die Datei frisch
  angelegt — die bestehende „Datei fehlt → neu anlegen"-Logik greift unverändert.
- **`force`-Regeneration:** `_EVENING_HEADER_RE` läuft pro Datei. Die Regex muss ans
  neue H1-Format (mit Kategorie + Slot) angepasst werden, damit der Abend-H1 weiterhin
  gefunden und ab dort truncatet wird.

## Notification

Nach erfolgreichem Schreiben: **pro nicht-leerer Kategorie ein**
`notify_digest_ready(...)`-Call.

- Message: `"{CATEGORY_LABEL}-Digest bereit ({n} Items)"`, z. B.
  `"AI/LLM/ML-Digest bereit (8 Items)"`, `"Weltgeschehen-Digest bereit (7 Items)"`.
- `file_path` der Notification ist die zugehörige Kategorie-Datei (Klick öffnet genau
  diese).
- Der Notifier bekommt dafür ein zusätzliches `category_label`-Argument (oder die
  Message wird vom Aufrufer vorbereitet — Implementierungsdetail).
- **Leere Kategorie:** keine Datei, kein Ping.

Diese direkte Notifier-Kopplung verbleibt — wie heute — als bekannte
Phase-1-Architektur-Deferral (`digester.py::generate_digest`, injizierter
`notifier`-Param). Der Split ändert nur die Anzahl der Calls (1 → bis zu 2), nicht die
Kopplungsklasse.

## Bookkeeping & Idempotenz

- **Eine `digests`-Zeile pro `(date, slot)`** bleibt. `UNIQUE(date, slot)` unverändert
  → keine Migration.
- `digests.file_path` (TEXT) speichert ein **JSON-Array** der geschriebenen Pfade.
- `digests.item_count` = Summe über beide Kategorien.
- **Verify vor Commit:** dass `file_path` nirgends als Einzelpfad zurückgelesen wird
  (Append/Re-Open nutzt den *berechneten* Pfad, nicht den DB-Wert). Falls doch ein
  Konsument existiert, wird er auf das JSON-Array angepasst. Annahme wird nicht blind
  übernommen.

## Edge Cases

- **Eine Kategorie in einem Slot leer:** keine Datei erzeugt/angefasst, kein Ping.
- **Beide Kategorien leer:** keine Datei, kein Ping — wie heutiges Leer-Verhalten.
- **Abend ohne Morgen-Datei der Kategorie:** Abend legt Datei frisch an (s. o.).
- **Kompletter LLM-Ausfall (degraded):** beide Kategorien degraded gerendert (DB-Row
  als Fallback: `headline = title`, `prose = raw_summary[:1200]`), beide Dateien +
  beide Pings, jeweils unter dem „⚠️ Automatisch generiert"-Banner. Die degraded-
  Render-Invariante bleibt erhalten.
- **Unbekannte/zukünftige Top-Level-Kategorie:** der Partitions-Loop iteriert über die
  tatsächlich vorhandenen `source_category`-Werte der Items; eine neue Kategorie
  erzeugt automatisch ihre eigene `{date}_{category}.md` (Suffix = Key, Label-Fallback
  `.capitalize()` wie heute).

## Tests (`tests/test_digester.py`)

Bestehende Single-File-Tests werden auf Multi-File umgestellt; neu/angepasst:
- Pro Slot werden zwei Dateien mit korrektem Suffix geschrieben.
- Items landen in der richtigen Kategorie-Datei (Partition korrekt).
- H1 enthält Kategorie-Label; Item-Headlines sind H2; keine `## Kategorie`-Section.
- Leere Kategorie ⇒ keine Datei, kein Ping.
- Abend-Slot appended pro Datei mit genau einem `---`; Abend-ohne-Morgen legt frisch
  an.
- `force`-Regeneration trunciert pro Datei am neuen H1-Format korrekt.
- Degraded-Render erzeugt beide Dateien + beide Pings inkl. Banner.
- Notifier wird pro nicht-leerer Kategorie genau einmal aufgerufen (Spy/`send_fn`).
- `digests`-Zeile: eine pro `(date, slot)`, `file_path` als JSON-Array, `item_count`
  als Summe.

## Betroffene Dateien

- `src/newsroom/digester.py` — Partition-Loop, `_format_top_header`, `_render_digest`,
  `_render_item`, `_write_digest_file`, `_EVENING_HEADER_RE`, Notifier-Calls,
  `digests`-Bookkeeping.
- `src/newsroom/notifier.py` — `notify_digest_ready` um Kategorie-Label erweitern.
- `tests/test_digester.py` — Multi-File-Erwartungen.
- Ggf. `tests/test_notifier.py` — neue Signatur.

## Doku-Folgearbeiten (nach Implementierung)

- `CLAUDE.md` „Common Gotchas" + „Digest rendering"-Abschnitte: Single-File → Multi-File
  pro Kategorie, neue Heading-Hierarchie (H1 mit Kategorie, Items H2), zwei Pings.
- ADR-Gating-Frage: Verdient dieser Output-Split einen eigenen ADR, oder ist er eine
  Verfeinerung von `docs/adrs/0001-deterministic-digest-rendering.md`? Default: nur ADR,
  wenn folgenreiche Architektur-Entscheidung — sonst Spec nach Umsetzung verwerfen.
