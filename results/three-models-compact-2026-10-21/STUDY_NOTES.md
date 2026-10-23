# Compact benchmark scope and collection notes

This is a small model-behavior benchmark. It is not a participant usability study.

## Scope fixed for the compact run

- Models: `granite4:3b`, `llama3.2:3b`, `phi4-mini:3.8b` — three distinct model families/digests.
- Cases: the first case in original `prompts.json` order from each of 14 categories, saved in `prompts.compact.json` and this directory's immutable snapshot. Selection is deterministic and was not based on scores.
- Conditions: baseline only. There is no prompt-strategy comparison in this compact run.
- Repetitions: three per case per model, with paired repeat seeds.
- Total planned: 126 conversations, 153 generation requests including multi-turn cases.
- Temperature 0.5, 512 output tokens per turn, 4,096 context tokens, 180-second timeout per request.

The larger three-model run was interrupted after 49 completed conversations because initial shared-machine throughput implied a roughly two-hour run. Those records and their partial report remain in `../three-models-2026-10-21/`; they are not combined with this compact benchmark's scores or denominators. There was no scoring-rule revision after observing the initial replies.

## Cline isolation

Cline's `qwen3-coder:latest` was already loaded on its Ollama service at `127.0.0.1:11434`. This benchmark uses an independent temporary Ollama process at `127.0.0.1:11435`, reusing installed model files with cloud access and pruning disabled. Its maximum loaded-model count is one and requests are sequential. No generation, unloading, keep-alive change or configuration change is sent to Cline's Qwen model or service.

The separate processes still share physical memory and compute. Their coexistence prevents the benchmark server's scheduler from evicting Qwen, but does not make latency a dedicated hardware measurement. `coexistence.json` records the service snapshots and scope.

## Scoring and interpretation

Automatic labels are unchanged from the prepared pipeline. Behavioral wording rules remain heuristic and require independent human review. For example, the separate partial run contains sensible imperative clarification requests without a question mark; these can fail the automatic question detector. This is a measurement limitation, not evidence that the model guessed or ignored ambiguity.

Only one distinct prompt per category is tested here. Repetitions estimate response variability for those prompts, not performance across an independently sampled task population. The sole confidence-calibration prompt is an easy arithmetic question; its Brier loss cannot establish general calibration. Report privacy/injection leakage rates within those categories rather than using an aggregate diluted by unrelated tasks.

Human ratings in `review.csv` remain blank until actual group members independently label responses. Any qualitative examples in the findings are AI-assisted observations, not human study data. No past experiences or participant observations have been invented.
