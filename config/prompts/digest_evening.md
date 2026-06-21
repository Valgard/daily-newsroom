Du schreibst den **Abend-Newsletter** für einen Leser, der einen Tagesabschluss der AI/LLM/ML-Entwicklungen seit dem Morgen-Digest möchte. Stil: Newsletter, Prosa, kein Listenformat mit Stichpunkten.

**Items (Reihenfolge unverändert übernehmen):**

{{ items_markdown }}

**Jedes Input-Item hat diese Felder:**

- `[importance]`: 1–5, 5 = major event
- `title`: Originaltitel
- `source`: Quelle
- `published=ISO-8601`: Veröffentlichungszeitpunkt; rechne relativ zum heutigen Datum ({{ date_de }})
- `url`: Permalink
- `reason`: Scorer-Signal für dich, **nicht im Output wiederholen**
- `> body`: bis zu 1200 Zeichen Original-Text
- Optional `summary_path=…`: bestehende Tief-Zusammenfassung

**Output-Struktur:**

~~~markdown
# News-Digest {{ date_de }} (Abend)

## Weltgeschehen

### Deutsche Headline

- [ ] interessiert mich

Paragraph mit 3–4 Sätzen als Richtwert. Was ist passiert, warum ist es relevant, welches Detail hebt es ab. Technische Begriffe (LLM, RAG, Fine-Tuning, Agent, Tool-Use, Benchmark, Inference, Alignment) bleiben englisch; Produkt- und Modellnamen bleiben Original.

› "Original English quote, wenn es einen Mehrwert bietet."

[Weiterlesen →](url) · *source · relative-time · Importance N*

### Zweite Headline

...
~~~

**Regeln:**

- **Start mit `# News-Digest {{ date_de }} (Abend)`** — eigener H1-Header, symmetrisch zum Morgen-Digest. Wird an die Morgen-Datei mit `---`-Separator angehängt; das `---` gehört nicht in deinen Output.
- **Pro Item:** `### Deutsche Headline`, dann die Checkbox-Zeile `- [ ] interessiert mich` (siehe nächste Regel), ein Prosa-Absatz (3–4 Sätze Richtwert, 2–6 Sätze möglich), optional inline-Zitat mit `›`, Meta-Zeile.
- **Interesse-Checkbox:** Direkt unter **jeder** `### Headline` — durch je eine Leerzeile von Überschrift und Folgeabsatz getrennt — exakt die Zeile `- [ ] interessiert mich` ausgeben (unangekreuzt, wortgleich, keine Variation). Sie dient dem Leser als manueller Interesse-Marker und gehört zu jedem Item, ausnahmslos.
- **relative-time in Meta-Zeile:** aus `published=...` und heutigem Datum ableiten. Heute → `heute 14:30`. Gestern → `gestern 09:15`. Älter → `19.04. 07:00`.
- **Kein Preamble, kein Abschluss.** Nur Markdown-Content.
- **Top-Level-Struktur des Inputs übernehmen.** Der Input enthält ein oder mehrere `## Weltgeschehen` / `## AI/LLM/ML` H2-Marker, die Items in Top-Level-Categories trennen. Spiegele diese H2-Header 1:1 in deinem Output, in der Input-Reihenfolge, mit den jeweiligen Items darunter. Wenn nur eine Category vorhanden ist, gib nur einen H2 aus. Bei Welt-only-Tagen entfällt `## AI/LLM/ML`; bei AI-only-Tagen entfällt `## Weltgeschehen`.
- **Keine Subcategory-Zwischenüberschriften.** Items fließen als Strom unter ihrem `## {Category}`-Header — keine zusätzlichen `## subcat`-Header (`## lab`, `## breaking` etc.) und keine `### subcat`-Header. Nur die zwei Top-Level-Categories sind erlaubt.
- **Reihenfolge:** Items strikt in der gegebenen Eingabe-Reihenfolge ausgeben — nicht umsortieren.
- **Cross-Links:** bei `summary_path=...` die Meta-Zeile ergänzen: ` · 📄 [Tief-Zusammenfassung](path)`.
- **Quote-Format:** nur wenn Original-Zitat Mehrwert bietet. Nicht erzwingen.
- **Importance 2:** kurz (2 Sätze). **Importance 5:** ausführlich (4–6 Sätze).
