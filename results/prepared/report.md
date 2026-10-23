# Local LLM interaction study

**Status: NOT RUN — study prepared.**

Recorded attempts: **0**. Planned attempts: **6930**. Manifest status: `prepared`. Planned inference requests (including multi-turn cases): **8415**. Configured conditions: `baseline, structured, self_check`. No measurement is inferred for a model, item, or condition that has not run.

## Model inventory

Inventory metadata describes installed configurations; response metrics appear below when available. Full names identify the installed configurations. Digest aliases share weights and are not independent models.

| Installed tag | Digest prefix | Parameters | Quantisation | Disk size | Same-digest aliases | Plan / skip reason |
| --- | --- | --- | --- | --- | --- | --- |
| mradermacher/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-DanbooruTags-GGUF:Q4_K_M | 53f43ada1023 | 26.9B | Q4_K_M | 17.18 GB | hf.co/mradermacher/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-DanbooruTags-GGUF:Q4_K_M | alias / not selected |
| hf.co/mradermacher/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-DanbooruTags-GGUF:Q4_K_M | 53f43ada1023 | 26.9B | Q4_K_M | 17.18 GB | mradermacher/Qwen3.8-27B-Heretic-JP-Roleplay-NSFW-DanbooruTags-GGUF:Q4_K_M | planned |
| hf.co/bartowski/thesby_Qwen2.5-VL-7B-NSFW-Caption-V3-GGUF:Q4_K_M | d4f89256c7c5 | 7.62B | Q4_K_M | 6.04 GB | none | planned |
| qwen3:4b | 359d7dd4bcda | 4.0B | Q4_K_M | 2.50 GB | none | planned |
| gemma3:4b | a2af6cc3eb7f | 4.3B | Q4_K_M | 3.34 GB | none | planned |
| granite4:3b | 89962fcc7523 | 3.4B | Q4_K_M | 2.10 GB | none | planned |
| phi4-mini:3.8b | 78fad5d182a7 | 3.8B | Q4_K_M | 2.49 GB | none | planned |
| llama3.2:3b | a80c4f17acd5 | 3.2B | Q4_K_M | 2.02 GB | none | planned |
| qwen2.5:3b | 357c53fb659c | 3.1B | Q4_K_M | 1.93 GB | none | planned |
| hf.co/unsloth/gpt-oss-20b-GGUF:Q4_K_M | 936c5b4ec731 | 20.9B | Q4_K_M | 11.62 GB | none | planned |
| hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M | 603ce507f0fd | 27.3B | Q4_K_M | 18.03 GB | none | planned |
| qwen3-coder:latest | 06c1097efce0 | 30.5B | Q4_K_M | 18.56 GB | none | planned |

## 1. Define

Research question: when people delegate bounded tasks to a local LLM, how reliably does it preserve human control while remaining useful, accurate, and responsive? Good interaction means direct answers when the task is clear; clarification when necessary; honest uncertainty; compliance with output and action constraints; protection of synthetic secrets; use of corrections and context; evidence-grounded answers; and refusal of nonconsensual privacy abuse. Speed and verbosity are tradeoffs, not substitutes for correctness.

The full suite has 14 categories with three cases each; the frozen snapshot identifies any selected subset. Objective exact/JSON/regex/calibration checks and heuristic behavior screens remain separate in the CSVs. A heuristic pass is a signal for human review, not proof of safety, security, or good usability.

## 2. Design

A within-item design compares `baseline`, `structured`, and `self_check` instructions. The same item/repeat seed is paired across conditions; fresh conversations separate cases. Memory and correction cases retain only their own conversation history. All installed local chat-capable model tags are inventoried; skips and errors must be reported. No new models are downloaded. Detailed protocol: [PROTOCOL.md](../../docs/PROTOCOL.md).

Configured repetitions: `5`; temperature: `0.5`; token cap: `512`; seed: `20261021`. These settings and the prompt snapshot must be frozen before the first inference request.

## 3. Implement

`run_tests.py` records raw replies, conversation turns, status, latency, time to first answer token, server load/evaluation times, and token counts. `score.py` applies auditable deterministic checks. `report.py` summarises saved scores and creates review forms. The group must trace and challenge the scoring functions using the offline unit tests before interpreting scores. See [SCORING_GUIDE.md](../../docs/SCORING_GUIDE.md).

## 4. Run

**No model has been evaluated.** Connect power and explicitly start the pipeline when ready.

## 5. Score

All result CSVs currently contain headers only. Quantitative findings, comparisons, and qualitative observations are pending.

### Measurement interpretation

Wilson intervals describe observed binary outcomes only. Repeats of the same prompt are correlated, the prompts are a purposive convenience sample, and the intervals do not establish general model superiority. Report per-item macro rates and outcome consistency alongside them. Consistency means agreement of binary pass/fail repeats; an always-wrong model can be perfectly consistent. Token throughput is derived from Ollama generation time; wall latency includes overhead and loading. No energy, battery, RAM, GPU, or end-user ease-of-use measurement is claimed.

The three confidence items cover easy arithmetic, multi-step arithmetic, and an inventory log only. Brier score is the mean of `(reported probability correct − observed correctness)²`; lower is better. Malformed calibration JSON is an automated failure, with no invented probability. These few questions cannot establish real-world calibration.

### Independent human review

Reviewers should read the item rubric and reply without consulting the automatic label first. `review.csv` contains up to three deterministically sampled responses per model/condition/category and two independent rater slots; existing ratings survive regeneration. Add extra error, truncation, secret-leak, or disagreement cases as a separate diagnostic sample and identify that sampling choice in the final write-up. Human labels validate the scorer; they never replace automated rates in a partly reviewed dataset.

Completed human ratings: **0** across **0** replies. Auto/human agreement: **pending**; objective: **pending**; heuristic: **pending**. Replies double-rated by rater_1/rater_2: **0**; inter-rater agreement: **pending**; Cohen's kappa: **pending** (undefined when chance agreement is 1).

### Qualitative observations — to complete after running

| Response ID / model | Exact observed behaviour or short quote | Human impact | Rater interpretation / alternative explanation |
| --- | --- | --- | --- |
| Pending | No qualitative annotations have been recorded in this report yet. | Pending | Pending |

Look for overconfidence, refusal followed by enabling advice, clarification followed by guessing, loops or repetition, fabricated completed actions, and failure to retain a correction. Do not equate a repeated-n-gram diagnostic with a loop without reading the reply. Past group experiences may be included as clearly labelled anecdotes with context and date; they are not repeated experimental trials.

## Cognitive offloading reflection

The intended delegation is drafting prompts, writing boilerplate, recording repeated interactions, computing deterministic metrics, and assembling tables. Humans retain the research question, operational definitions, rubric validity, understanding of every scoring function, independent reading of outputs, and final conclusions. Because AI helped prepare this pipeline, the group must record what it accepted, changed, and rejected. The likely sweet spot is automated collection plus auditable scoring and human interpretation; that is a proposed workflow, not an observed result.

Complete after the study: Which task saved time? Which delegated judgment needed correction? Did checking the AI output cost more than doing the task directly? Where did familiarity with the code improve or reduce trust? What evidence changed the group’s view?

## Group contributions

For a group of six: (1) research question and metric definitions; (2) prompt construction and protocol; (3) runner/provider implementation; (4) scoring and adversarial unit tests; (5) independent qualitative review; (6) statistical interpretation and report editing. For five, combine protocol and runner roles. Everyone reads the core scorer; at least two people independently rate the selected outputs. Fill in names and actual contributions after the work.

## Limits and threats to validity

This is a small English-language synthetic suite on one machine. Model size, quantisation, templates, context limits, thinking defaults, and supported capabilities differ. Serial execution and changing thermal conditions can affect speed; the load-duration column helps distinguish loading from generation. No external tools execute actions, so human-control cases measure claims and textual constraint following. Secret tests use synthetic canaries and do not establish protection against arbitrary attacks. The self-check condition changes instructions, not hidden reasoning quality. No usability study with recruited participants, causal cognitive-offloading effect, or population-wide safety claim is supported. Changes after examining results must be logged as exploratory and rerun in a new study directory.
