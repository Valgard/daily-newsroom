You are rating the importance of a single news item for a reader who follows AI/LLM/ML research and industry news.

**Importance scale (be strict — target distribution: 1% at 5, 4% at 4, 20% at 3, 60% at 2, 15% at 1):**

- **5 — Major event (very rare, <1% of items):** Flagship-model release from a frontier lab (Claude 5, GPT-6, Gemini 3, Llama 5). Paradigm-shifting research result (e.g. first GPT-4-beating open model, mechanistic interpretability breakthrough that reframes how we think about LLMs). Industry-shaping policy change (e.g. EU AI Act enforcement begins, major acquisition).
  - Examples: "Claude 4.5 released", "OpenAI o3 public preview", "DeepSeek-R1 matches o1"
  - NOT: version bumps, new API features, small-model releases, tutorials

- **4 — Notable (rare, ~4% of items):** Any of the following:
  - **New model / product / feature from a frontier lab** that a practitioner could adopt or evaluate this week. The frontier-lab set is broader than the US Big-5 — it includes:
      - Western: Anthropic, OpenAI, Google/DeepMind, Meta, Mistral, xAI, Cohere, AI2/Allen, NVIDIA Research, Stability, Black Forest Labs, Together AI
      - Chinese: Alibaba/Qwen, DeepSeek, ByteDance/Seed, Tencent, Baidu/ERNIE, Ant Group/inclusionAI, Moonshot/Kimi, 01.AI/Yi, Zhipu/GLM
    Open-weights releases on Hugging Face from these orgs count.
  - **Substantive research finding or discovery** that changes how a practitioner reasons about LLMs/agents/training/eval — even without immediate code adoption. Mechanistic interpretability results, capability discoveries, well-substantiated negative results, surprising empirical patterns. The bar: would a thoughtful AI researcher cite this in a discussion next week?
  - **High-impact open-source infrastructure**: novel attention/inference kernels with measurable speedups (FlashAttention-class, FlashQLA-class), substantially better training or serving architectures, new benchmarks already being adopted by labs.
  - Examples: "Claude Sonnet 4.6 in GA", "Gemini 2.5 adds computer use", "Llama 3.3 70B released", "Mistral-Medium-3.5-128B released", "Qwen FlashQLA: 2-3× edge inference speedup", "Anthropic finds emotion features in Claude (interpretability)", "Apollo Research: scheming-behavior eval results", "DeepSeek-V4 technical report"
  - NOT: blog posts re-explaining existing features, library version bumps, incremental benchmarks on minor models, tutorials, surveys, community forks of existing tools, commentary/opinion pieces about other people's work

- **3 — Interesting (~20% of items):** Well-written technical content worth bookmarking. New open-source tool/library. Thoughtful analysis. Mid-tier paper. HuggingFace blog posts about new models/datasets/techniques default here unless clearly Level 4.
  - **Floor for open-weights releases:** any new open-weights model at credible scale (≥10B params, or with non-trivial new architecture/training claims) defaults to Level 3 minimum — even if the releasing org isn't on the frontier-lab list above. "Credible scale but unknown lab" is still worth bookmarking; don't demote to 2 just because the org isn't famous. Promote to 4 only if the org IS on the frontier-lab list.
  - Examples: "KV-cache tutorial", "Introducing SynthID-Text", "Small LLM benchmark survey", "Welcome PaliGemma 2", "<unknown-university-lab>/12B-instruct released" (credible-scale open-weights from non-frontier org → Level 3, not 2)
  - Most HuggingFace, most curated-newsletter, most tutorial content lands here

- **2 — Minor (~60%, default):** Routine updates, incremental improvements, niche topics, version bumps without user-visible changes, sponsored content, reposts. When uncertain between 2 and 3, pick 2.

- **1 — Trivia (~15%):** Off-topic, promotional-only, dupes, listicles, low-effort aggregators, non-AI content that slipped in.

**Item:**
- Source: {{ source_name }}
- Title: {{ title }}
- Author: {{ author }}
- Body: {{ summary }}

**Calibration check (do this before answering):** the target is 1% at 5, 4% at 4, 20% at 3, 60% at 2, 15% at 1. Ask yourself:
- Does the source itself signal importance? (anthropic-news / openai-blog / deepmind-blog / huggingface-blog from a lab account → higher prior; reddit/hackernews/arxiv → only the content decides.)
- Is this a release/discovery/finding (potentially 4+) or commentary/tutorial/recap (max 3)?
- If you'd rate every Anthropic engineering blog at 3 and every Mistral/Qwen open-weights release at 3, your bar for 4 is too high — the rubric is calibrated such that genuine frontier-lab releases and substantive discoveries DO cross the line. Conversely, if community forum posts are landing at 4, your bar is too low.

Push notifications (importance ≥ 4) are an interruption — but a notification that never fires is also a failure. Trust the rubric and the source signal.

Respond with a JSON object only, no prose:

```
{"importance": <1-5>, "reason": "<one short sentence>"}
```
