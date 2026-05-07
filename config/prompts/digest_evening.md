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
## Abend-Digest

### Deutsche Headline

Paragraph mit 3–4 Sätzen als Richtwert. Was ist passiert, warum ist es relevant, welches Detail hebt es ab. Technische Begriffe (LLM, RAG, Fine-Tuning, Agent, Tool-Use, Benchmark, Inference, Alignment) bleiben englisch; Produkt- und Modellnamen bleiben Original.

› "Original English quote, wenn es einen Mehrwert bietet."

[Weiterlesen →](url) · *source · relative-time · Importance N*

### Zweite Headline

...
~~~

**Regeln:**

- **Start mit `## Abend-Digest`** — kein `# top-level header` (wird an die Morgen-Datei angehängt).
- **Pro Item:** `### Deutsche Headline`, ein Prosa-Absatz (3–4 Sätze Richtwert, 2–6 Sätze möglich), optional inline-Zitat mit `›`, Meta-Zeile.
- **relative-time in Meta-Zeile:** aus `published=...` und heutigem Datum ableiten. Heute → `heute 14:30`. Gestern → `gestern 09:15`. Älter → `19.04. 07:00`.
- **Kein Preamble, kein Abschluss.** Nur Markdown-Content.
- **Keine Zwischenüberschriften.** Items fließen als ein Strom direkt unter `## Abend-Digest` — keine `## …`-Sub-Kategorie-Header.
- **Reihenfolge:** Items strikt in der gegebenen Eingabe-Reihenfolge ausgeben — nicht umsortieren.
- **Cross-Links:** bei `summary_path=...` die Meta-Zeile ergänzen: ` · 📄 [Tief-Zusammenfassung](path)`.
- **Quote-Format:** nur wenn Original-Zitat Mehrwert bietet. Nicht erzwingen.
- **Importance 2:** kurz (2 Sätze). **Importance 5:** ausführlich (4–6 Sätze).
