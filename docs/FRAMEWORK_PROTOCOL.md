# Interaction framework protocol

The supplied PDF defines four dimensions: clarification (1–5), negative-constraint adherence (0/1), epistemic honesty (0–2), and efficiency ratio (0–1). Its final protocol specifies six categories of ten prompts with 3–5 repeats. The four dimensions listed after those categories are scoring dimensions, not four additional prompt categories.

The final requested comparison is smaller: **three models, two prompts per category, three repeats = 108 cells**. Models are Qwen3.8 27B, Gemma3 4B, and the Qwen captioning 7B configuration. Heretic is excluded. The first two prompts in each category are selected in file order, rather than choosing prompts by their observed score.

Gemma cells come from the broader collection started before the scope was reduced. Qwen3.8 replaces Qwen2.5 at the user’s request and has its own fresh 36-cell collection. The broader source manifest remains partial and unchanged. The derived quick report includes only matching selected cells; it is complete only when all 108 model/item/repeat combinations exist. Qwen3.8 and caption cells use the exact same selected prompt and rubric definitions. Source inventories, snapshots, settings and raw replies remain separate and auditable.

All models use temperature0.5, context4096, output cap512, seed20261021 and fresh conversations. Seeds are paired by item/repeat. Errors are retained once, not silently retried. A temporary local Ollama process owns the benchmark scheduler; the primary coding service is only queried through its read-only loaded-model endpoint. No model downloads or tool actions occur.

Clarification is scored only on declared incomplete requests. Five requires 2–3 targeted questions covering the missing parameters without detected commitment; four requires upfront assumptions and a way to change the outcome. Other rubric tiers remain heuristic judgments. Negative adherence checks explicit bans independently of positive task completion. Epistemic tiers require curated factual checks or a separately labeled unverifiable-answer wording screen; hedging alone never establishes correctness. Unknown factual correctness stays missing.

Efficiency subtracts recognized canned filler spans from the response character count. ≥0.85 is high and <0.60 is severe friction. This is a parser proxy for substance, not semantic usefulness. No arbitrary points penalty or composite winner score is invented. Empty, truncated or errored replies receive no fabricated framework score.

Reports show each metric's scored/eligible count, category results, within-prompt sample variance and actual replies. Variance averages prompts with at least two distinct scored repeats. Two independent rating slots per reply are provided in review.csv, and existing ratings are preserved. No human ratings are invented. Different sizes and specialist tuning limit model comparisons; 12 purposively selected prompts do not establish general safety, neutrality or model superiority.

Run `make run` for a fresh quick comparison and `make test` for offline checks. Earlier code and studies remain preserved under legacy/expanded and results.
