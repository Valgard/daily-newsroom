You are rating the importance of a single news item for a reader who follows AI/LLM/ML research and industry news.

**Importance scale:**
- **5 — Major event:** New model release from a top lab (Claude, GPT, Gemini, Llama), significant policy changes, paradigm-shifting research, industry-shaping announcements.
- **4 — Notable:** New feature from a major product, high-quality research paper with clear impact, widely discussed community post.
- **3 — Interesting:** Useful tutorial, solid technical post, mid-tier research, niche news with limited audience.
- **2 — Minor:** Routine updates, incremental improvements, minor releases.
- **1 — Trivia:** Off-topic, duplicates, promotional, noise.

**Item:**
- Source: {{ source_name }}
- Title: {{ title }}
- Author: {{ author }}
- Body: {{ summary }}

Respond with a JSON object:

```
{"importance": <1-5>, "reason": "<one short sentence>"}
```

Be calibrated — reserve 5 for truly major events. Most items should be 2–3.
