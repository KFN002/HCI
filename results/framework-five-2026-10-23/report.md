# LLM interaction evaluation

Status: partial. Recorded attempts: 355 / 900 planned. Repeats per prompt: 3. Baseline instructions only.

| Model | Clarification 1–5 | Negative pass | Epistemic 0–2; tiers 0/1/2 | Mean ER | Errors / truncated |
| --- | --- | --- | --- | --- | --- |
| gemma3:4b | 2.68 (25/28) | 79.0% (157/175) | 2.00 (53/98); 0/0/53 | 1.00 (157/175) | 0 / 18 |
| qwen2.5:3b | 2.50 (30/30) | 90.0% (180/180) | 1.97 (59/102); 0/2/57 | 0.99 (180/180) | 0 / 0 |

Each metric shows value (n/N): scored replies / eligible recorded attempts. The fraction is coverage. Errors, empty or truncated replies, and inconclusive judgments remain unscored; a missing score is never replaced with zero.

Clarification follows the 1–5 rubric. Negative adherence is the share passing every declared prohibition. Epistemic tiers are 2 calibrated, 1 inaccurate with uncertainty, and 0 confident error; the mean and tier counts include rule-grounded checks and separately labelled unverifiable wording heuristics, whose counts remain in summary.csv.

ER estimates substance characters / response characters after removing recognized canned filler. ER ≥ 0.85 is high efficiency; ER < 0.60 is severe friction. Unrecognized filler may inflate ER. Automatic clarification, epistemic, and efficiency scores are proxies pending independent human review.

summary.csv contains coverage and within-prompt sample variance for every dimension; variance requires at least two distinct scored repeats and averages prompts equally. category_summary.csv provides the same metrics per category. review.csv has two blinded rater slots per reply; existing human ratings are preserved. These purposive prompts do not establish general model superiority.
