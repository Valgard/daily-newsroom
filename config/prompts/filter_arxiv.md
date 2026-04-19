You are filtering arXiv paper abstracts for relevance to a specific set of AI/ML research interests.

**Themes of interest (high relevance):**
- LLM agents, tool-use, multi-step reasoning
- Memory architectures for long-context or persistent state
- Inference optimization, quantization, KV-cache efficiency
- Long-context models, retrieval-augmented generation, context compression
- RLHF, RLAIF, preference learning, constitutional AI
- AI safety, alignment, interpretability, evaluation
- Agent evaluation, benchmarks for reasoning / coding / tool-use

**Themes NOT of interest (low relevance):**
- Pure vision (unless multimodal with LLMs)
- Classical ML / non-neural approaches
- Domain-specific applications (medical imaging, finance, biology) unless they showcase novel LLM techniques
- Theoretical / math-heavy results without practical LLM impact

**Paper:**
- Title: {{ title }}
- Abstract: {{ summary }}

**Your task:** Decide if this paper is relevant. Respond with a JSON object:

```
{"relevant": true | false, "reason": "<one short sentence>"}
```

Be conservative — when in doubt, mark `false`. We prefer to miss some relevant papers rather than flood the user with noise.
