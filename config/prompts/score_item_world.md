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
