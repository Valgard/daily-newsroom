You are writing the **morning** news digest for a reader who wants to catch up on what happened overnight in AI/LLM/ML.

**Style:** Compact, German headlines, English original quotes preserved where helpful (prefix `› ""`). Each item: headline + one-sentence key point + link.

**Items (grouped by sub-category, sorted by importance):**

{{ items_markdown }}

**Output structure:**

~~~markdown
# News-Digest {{ date_de }} (Morgen)

## AI / LLM — Labs
- **[5]** [Headline](url) · source · _reason why important_
- ...

## AI / LLM — Community & Curated
- ...

## AI / Claude Code
- ...
~~~

**Rules:**
- Omit empty sections.
- If an item has a `summary_path`, add `📄 [Tief-Zusammenfassung](summary_path)` at the end of that bullet.
- Deutsche Headlines; bei englischen Original-Titeln: verwende eine knappe deutsche Übersetzung, und zitiere ggf. den Originaltitel mit `› "…"`.
- Fachbegriffe wie LLM, RAG, Fine-Tuning, Embedding bleiben englisch.
- Maximal 3 Sätze pro Kernaussage. Kein Preamble, kein Abschluss. Nur Markdown-Content.
