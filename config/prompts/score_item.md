You are rating the importance of a single news item for a reader who follows AI/LLM/ML research and industry news.

**Importance scale (be strict — target distribution: 1% at 5, 4% at 4, 20% at 3, 60% at 2, 15% at 1):**

- **5 — Major event (very rare, <1% of items):** Flagship-model release from a frontier lab (Claude 5, GPT-6, Gemini 3, Llama 5). Paradigm-shifting research result (e.g. first GPT-4-beating open model, mechanistic interpretability breakthrough). Industry-shaping policy change (e.g. EU AI Act enforcement begins, major acquisition).
  - Examples: "Claude 4.5 released", "OpenAI o3 public preview", "DeepSeek-R1 matches o1"
  - NOT: version bumps, new API features, small-model releases, tutorials

- **4 — Notable (rare, ~4% of items):** A specific new product/feature from Anthropic/OpenAI/Google/Meta/DeepMind that a practitioner would adopt this week. OR a research paper with immediate industry uptake.
  - Examples: "Claude Sonnet 4.6 in GA", "Gemini 2.5 adds computer use", "Llama 3.3 70B released"
  - NOT: blog posts explaining existing features, library updates, incremental benchmarks, community-built tools, tutorials, surveys

- **3 — Interesting (~20% of items):** Well-written technical content worth bookmarking. New open-source tool/library. Thoughtful analysis. Mid-tier paper. HuggingFace blog posts about new models/datasets/techniques default here unless clearly Level 4.
  - Examples: "KV-cache tutorial", "Introducing SynthID-Text", "Small LLM benchmark survey", "Welcome PaliGemma 2"
  - Most HuggingFace, most curated-newsletter, most tutorial content lands here

- **2 — Minor (~60%, default):** Routine updates, incremental improvements, niche topics, version bumps without user-visible changes, sponsored content, reposts. When uncertain between 2 and 3, pick 2.

- **1 — Trivia (~15%):** Off-topic, promotional-only, dupes, listicles, low-effort aggregators, non-AI content that slipped in.

**Item:**
- Source: {{ source_name }}
- Title: {{ title }}
- Author: {{ author }}
- Body: {{ summary }}

**Calibration reminder:** the user already gets a twice-daily digest of ALL scored items. Push notifications (importance ≥ 4) are an interruption. Over-scoring burns their trust. **When in doubt, go DOWN one level.**

Respond with a JSON object only, no prose:

```
{"importance": <1-5>, "reason": "<one short sentence>"}
```
