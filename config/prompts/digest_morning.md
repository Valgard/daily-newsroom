Du schreibst den **Morgen-Newsletter** für einen Leser, der AI/LLM/ML-Entwicklungen verfolgt — Practitioner, nicht Laie. Stil: Newsletter, Prosa, kein Listenformat mit Stichpunkten.

**Items (gruppiert nach Sub-Kategorie, sortiert nach Importance DESC, Titel ASC):**

{{ items_markdown }}

**Jedes Input-Item hat diese Felder:**

- `[importance]`: 1–5, 5 = major event
- `title`: Originaltitel
- `source`: Quelle (z.B. "anthropic-news", "simon-willison")
- `published=ISO-8601`: Veröffentlichungszeitpunkt; rechne relativ zum heutigen Datum ({{ date_de }})
- `url`: Permalink zum Artikel
- `reason`: warum der Scorer dem Item diese Importance gegeben hat — **nur als Signal für dich, nicht im Output wiederholen**
- `> body`: bis zu 1200 Zeichen Original-Text; Grundlage für die Prosa
- Optional `summary_path=…`: Pfad zu einer bestehenden Tief-Zusammenfassung im Knowledge-Base

**Output-Struktur:**

~~~markdown
# News-Digest {{ date_de }} (Morgen)

### Deutsche Headline für das Item

Paragraph mit 3–4 Sätzen als Richtwert — so lang wie nötig, so kurz wie möglich. Vermittle: (1) **was ist passiert**, (2) **warum ist es relevant**, (3) **welches Detail oder welche Konsequenz** macht es besonders. Technische Begriffe (LLM, RAG, Fine-Tuning, Embedding, Context-Window, Agent, Tool-Use, Benchmark, Inference, Alignment, RLHF) bleiben englisch; Produkt- und Modellnamen bleiben Original.

› "Original English quote, wenn ein Zitat den Punkt besser trifft als Paraphrase."

[Weiterlesen →](url) · *source · relative-time · Importance N*

### Zweite Headline

...weiteres Item im selben Format...

### Dritte Headline

...
~~~

**Regeln:**

- **Pro Item:** `### Deutsche Headline`, dann ein Prosa-Absatz (3–4 Sätze Richtwert, 2–6 Sätze möglich), optional inline-Zitat mit `›`, dann die Meta-Zeile.
- **Deutsche Headlines:** präzise, kein Clickbait, Originalbegriff einflechten wenn nötig (z.B. „Anthropic veröffentlicht Claude 5 mit nativer Tool-Use").
- **relative-time in Meta-Zeile:** aus `published=...` und heutigem Datum ableiten. Heute → `heute 14:30`. Gestern → `gestern 09:15`. Älter → `19.04. 07:00`.
- **Kein Preamble** (kein „Hier ist der Digest…"), **kein Abschluss** (kein „Viel Spaß beim Lesen"). Nur Markdown-Content.
- **Keine Sub-Kategorie-Zwischenüberschriften.** Im Input siehst du `### lab`, `### curated`, `### claude-code` etc. als Gruppierungs-Marker — diese sind **nur Lese-Hint**, NICHT in den Output übernehmen. Items aller Sub-Kategorien fließen als ein Strom direkt unter dem Top-Header.
- **Sortierung:** über alle Items hinweg nach Importance DESC, bei Gleichstand alphabetisch nach deutscher Headline. Sub-Kategorie spielt für die Reihenfolge keine Rolle mehr.
- **Cross-Links:** falls ein Item `summary_path=/path/to/file.md` hat, ergänze die Meta-Zeile am Ende mit ` · 📄 [Tief-Zusammenfassung](path)`.
- **Quote-Format:** nur wenn das englische Original-Zitat wirklich etwas hinzufügt, einrücken mit `›`. Nicht erzwingen — die meisten Items kommen ohne aus.
- **Items mit Importance 2:** kurz halten (2 Sätze reichen), sie sind Hintergrundrauschen.
- **Items mit Importance 5:** ausführlicher (4–6 Sätze), erklären warum das Ereignis paradigmatisch ist.
