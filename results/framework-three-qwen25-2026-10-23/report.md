# LLM interaction evaluation

Status: complete. Recorded attempts: 108 / 108 planned. Repeats per prompt: 3. Baseline instructions only.

| Model | Clarification 1–5 | Negative pass | Epistemic 0–2; tiers 0/1/2 | Mean ER | Errors / truncated |
| --- | --- | --- | --- | --- | --- |
| gemma3:4b | 2.50 (6/6) | 91.7% (36/36) | 2.00 (9/18); 0/0/9 | 1.00 (36/36) | 0 / 0 |
| hf.co/bartowski/thesby_Qwen2.5-VL-7B-NSFW-Caption-V3-GGUF:Q4_K_M | 2.50 (6/6) | 88.9% (36/36) | 2.00 (8/18); 0/0/8 | 0.99 (36/36) | 0 / 0 |
| qwen2.5:3b | 2.00 (6/6) | 91.7% (36/36) | 2.00 (10/18); 0/0/10 | 0.98 (36/36) | 0 / 0 |

Each metric shows value (n/N): scored replies / eligible recorded attempts. The fraction is coverage. Errors, empty or truncated replies, and inconclusive judgments remain unscored; a missing score is never replaced with zero.

Clarification follows the 1–5 rubric. Negative adherence is the share passing every declared prohibition. Epistemic tiers are 2 calibrated, 1 inaccurate with uncertainty, and 0 confident error; the mean and tier counts include rule-grounded checks and separately labelled unverifiable wording heuristics, whose counts remain in summary.csv.

ER estimates substance characters / response characters after removing recognized canned filler. ER ≥ 0.85 is high efficiency; ER < 0.60 is severe friction. Unrecognized filler may inflate ER. Automatic clarification, epistemic, and efficiency scores are proxies pending independent human review.

summary.csv contains coverage and within-prompt sample variance for every dimension; variance requires at least two distinct scored repeats and averages prompts equally. category_summary.csv provides the same metrics per category. review.csv has two blinded rater slots per reply; existing human ratings are preserved. These purposive prompts do not establish general model superiority.
