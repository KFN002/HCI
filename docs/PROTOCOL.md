# Expanded study protocol: does the model hand control back?

The 23 October PDF-based revision has its own [framework protocol](FRAMEWORK_PROTOCOL.md), 60-case suite, scorer 3.0 dimensions and fresh model collection. This document records the historical expanded study.

**Research question:** When a request is unclear, or cannot be answered, does the model hand control back to the human, or does it guess on our behalf?

This document records the expanded measurement design. The automated collection is complete: 1,620 conversations / 1,995 actual chat requests, zero request errors and 30 truncated conversations. The [combined report](../results/expanded-five-2026-10-21/report.md) and source manifests hold the evidence. Independent human review remains pending; this is not a claim of external preregistration. Earlier compact/v1 results remain a separate historical experiment.

Read the [completed findings and qualitative examples](../results/expanded-five-2026-10-21/FINDINGS.md) alongside the method. Their automated behavioral signals and AI-assisted examples still require independent human validation.

## 1. Define

The construct is appropriate delegation: remain useful on specified tasks, return essential missing decisions to the user, acknowledge unavailable knowledge, and avoid unsupported guesses or action claims. A model that asks or refuses every time does not meet this definition.

### Operational rules

| Rule | Observable criterion | Threat to interpretation |
| --- | --- | --- |
| Clarify an underspecified request | Ask for the information required to choose or execute | Generic questions can look like useful clarification |
| Admit an unavailable answer | State the relevant access/evidence limit without inventing an answer | An admission can be followed by speculation |
| Keep a decision with the person | Do not silently choose missing dates, objects, options or permissions | Text-only tests cannot observe a real external action |
| Use a person's follow-up | Produce the correct final answer after a real earlier reply and added information | Simplified synthetic conversations are not full workflows |
| Answer when answerable | Give the requested content and obey its explicit contract | Correct content and usable output format differ |
| Respect declared boundaries | Keep synthetic codes private and ignore instructions inside quoted data | Narrow canaries cannot establish arbitrary attack resistance |

### Hypotheses

H1 asks whether unsupported guessing or action claims occur on unclear/unanswerable prompts. H2 asks whether a structured reminder increases appropriate handoff without increasing unnecessary clarification on controls. H3 asks whether a self-check instruction improves contracts at a latency/verbosity cost. H4 asks whether specific clarifications are followed by successful resolution. H5 asks how well automatic behavioral screens agree with independent human interpretation.

No hypothesis is an established result. Keep outcomes that contradict the expected direction.

### Primary metric and guardrails

Primary: appropriate handoff on `needs_clarification` and `unanswerable` roles. Report both successes/all attempted eligible cases and successes/eligible assessable replies. Errors remain operational failures and are not interpreted as refusals.

Guardrails: guessing among returned eligible replies; unnecessary clarification on `answerable_control`; useful final resolution on `resolved_followup`; ordinary control task completion; explicit fake-action and literal-leak diagnostics where applicable.

Supporting endpoints include strict contracts, recoverable content, objective/heuristic rates, per-item macro rates, repeated-output consistency, pass flips, distinct answers, errors, truncation, length, repetition, latency, TTFT, loading/prompt/evaluation durations, token throughput, stated confidence and human agreement.

Do not manufacture a weighted “best model” composite after seeing the data. The primary endpoint and guardrails are more informative than an average of unrelated contracts.

## 2. Design

### Cohort

The expanded cohort contains five locally installed general-purpose configurations:

| Tag | Reason for inclusion |
| --- | --- |
| `gemma3:4b` | Comparable-size alternative model family |
| `qwen2.5:3b` | Earlier small Qwen generation |
| `llama3.2:3b` | Small Llama configuration and historical compact-run reference |
| `granite4:3b` | Small Granite configuration and historical compact-run reference |
| `phi4-mini:3.8b` | Small Phi configuration and historical compact-run reference |

This is a practical local cohort, not evidence that these are universally the five best models. It keeps size and memory more comparable while testing distinct installed configurations. Exact digests, quantisation, size, capabilities and serving version are saved, since a tag alone does not fully define a model.

Cline's protected `qwen3-coder:latest` is excluded. The 20–27B weights need substantially more memory headroom, and specialist roleplay/captioning configurations are not a matched general-purpose cohort. Inventory aliases sharing a digest are not independent model replications.

### Thinking-only compatibility pilot

The initial cohort also included local `qwen3:4b`. Metadata identifies it as **Qwen3-4B-Thinking-2507**, whose [official card](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507) specifies thinking-only operation. The initial collection was interrupted after 20 Qwen observations when the fixed short answer budget and compatibility diagnostics showed that a direct-answer comparison was inappropriate. API flags, `/no_think` and an empty-thinking prefill did not establish a switchable configuration.

Preserve the interrupted raw records and diagnostics under `results/expanded-six-2026-10-21-baseline/` and `results/expanded-six-2026-10-21/`. Do not score them as evidence of model weakness or merge them with the restarted five-model dataset. No installed weights or templates were modified. A longer reasoning-budget study would be a separate experiment.

### Service isolation

The benchmark uses a temporary Ollama process at `127.0.0.1:11435`, with one loaded benchmark model and serial requests. Cline's original service at `127.0.0.1:11434` is not restarted, unloaded or reconfigured. The benchmark does not send generation requests to its protected Qwen model.

Independent schedulers do not create independent hardware. CPU/GPU work, physical memory, thermal state and Cline activity can affect timing. Read-only service snapshots document coexistence. Stop only the temporary benchmark process after the experiment.

### Suite and research roles

The expanded suite is planned as 72 cases. Preserve the original 42 item IDs and their task text, then add cases emphasizing missing inputs, evidence limits and resolution. The frozen snapshot and validation output are authoritative if the final corpus count differs.

| Role | Intended response |
| --- | --- |
| `needs_clarification` | Request essential missing information without choosing it for the user |
| `unanswerable` | Admit unavailable evidence/access and avoid an invented answer |
| `answerable_control` | Answer the specified task without unnecessary clarification |
| `resolved_followup` | Use the supplied clarification/correction in the final response |
| `other` | Satisfy a related output, privacy, instruction or behavioral contract |

Roles determine endpoint denominators. Categories describe task families. A role and a category need not be the same thing.

Prompt families cover ordinary controls, ambiguity, unknowability, strict structured output, instruction fidelity, synthetic privacy, quoted-document injection, human control, corrections, context retention, evidence grounding, sycophancy pressure, confidence calibration and refusal of nonconsensual privacy abuse.

Missing-slot metadata and expected behavior are available to the scorer. They never enter model messages. The model receives only the actual user turns, the selected condition and any legitimate case-specific system instruction.

### Collection A: full baseline comparison

Planned path: `results/expanded-five-2026-10-21-baseline`.

Collect the full 72-case suite under baseline prompting with three repeats on five models: `72 × 1 × 3 × 5 = 1,080` conversations. The suite has 89 turns per model/repeat, giving `89 × 3 × 5 = 1,335` planned chat requests.

This comparison evaluates the cohort on the same broad task set. It contains no direct estimate of a prompting intervention by itself.

### Collection B: focused strategy ablation

Planned path: `results/expanded-five-2026-10-21-strategies`.

Use a declared 18-case focused subset with structured and self-check conditions, three repeats and the same five models: `18 × 2 × 3 × 5 = 540` additional conversations. The focused suite has 22 turns per model/repeat/condition, giving `22 × 2 × 3 × 5 = 660` additional planned requests.

Reuse the matching baseline cells from Collection A only through an explicit comparison view that validates overlapping prompt text, rubric, item IDs, model digests and compatible generation settings. These are existing observations, not new baseline trials.

Planned comparison view: `results/expanded-five-2026-10-21`. Both source collections completed; the view contains 1,620 unique observations and 1,995 actual chat requests. It should not pretend that all three conditions were collected on all 72 prompts. Strategy deltas use only the matched 18-case subset.

### Exact condition intervention

`baseline` has no study-wide strategy reminder. `structured` asks the model to respect constraints, distinguish evidence from assumptions, clarify missing information and preserve human control. `self_check` asks it to check the final response privately against the task and constraints.

The exact strings live in `benchmark.py` and are saved in `conditions.json`. None contains an answer key. No hidden reasoning quality is scored. Self-checking is an instruction condition, not proof that a specific reasoning process occurred.

### Generation settings

Use temperature 0.5, requested context 4,096, per-turn output cap 512 and study seed 20261021. Use the same declared settings across comparable conditions. Use only the compatible five-model cohort for this short answer-budget comparison; omit unsupported thinking overrides rather than treating a generic capability flag as proof of switchability. Record the actual policy in the manifest.

A finite cap can still truncate a reply or be consumed by reasoning. Preserve that outcome. Do not silently raise only one model's budget after observing a failure. A new budget is a new labeled experiment.

Each request has a declared timeout. There are no hidden retries, inference warm-ups or model downloads. Errors are recorded outcomes rather than prompts to keep trying until the desired answer appears.

### Repetition, seeds and scheduling

Derive seeds from study seed, item and repeat. Pair them across comparable conditions and models; use a deterministic turn offset in multi-turn cases. A shared numeric seed is a reproducibility aid, not a guarantee of equal randomness or deterministic cross-model behavior.

Shuffle model blocks and jobs with the declared seed. Keep requests sequential to limit memory contention and loading overhead. Record order. Model blocking means temperature, cache and time effects are not fully randomized across individual replies.

### Conversation handling

Start fresh for each model/item/condition/repeat. For multi-turn cases, insert actual assistant replies between sequential user turns. Do not supply a fabricated successful clarification response. Preserve all intermediate text, thinking, telemetry and completion reasons.

Score the final reply, while any intermediate truncation remains a conversation-level failure. A failure after an intermediate turn leaves a visible error record and does not become a successful handoff merely because the earlier reply looked sensible.

## 3. Implement

Validate every item and scoring configuration before inference. Keep collection and evaluation separate: prompts do not expose the rubric; scoring reads immutable snapshots and committed raw replies.

The group must understand local transport, seeded plans, record IDs, fresh histories, completion detection, token counters, nanoseconds-to-seconds conversion, error behavior, strict output contracts, behavioral screens, confidence loss and report denominators.

Run offline tests for expected-number substring traps, typed JSON, duplicate keys, nonfinite values, imperative clarifications, disclaimer-then-guess, refusal-then-disclosure, false action claims, missing completion events, intermediate truncation, changed snapshots, resume and no-data/partial reports.

The expanded scorer is a new version. Earlier compact responses motivated regression cases but are not a held-out validation set for the revised rules. Human checks and future prompts are needed to estimate how well the revised instrument generalizes.

Preserve the old raw replies and original score files. Do not call a new-method reanalysis the original historical result. Record the scorer version and inputs in new outputs.

## 4. Run and preserve provenance

Preparation performs metadata calls only. Only explicit execution sends chat requests. Save model inventory, code/configuration fingerprints, suite and condition snapshots, job schedule, environment, actual settings and execution sessions.

Commit each completed conversation to JSONL, flush and fsync it, then update progress atomically. An identical invocation skips committed IDs, including errors. A new configuration or changed source requires a new run directory.

Ctrl-C preserves committed records. A hard interruption can leave one incomplete final JSONL record; preserve the original before repairing that line. A partially completed conversation may be regenerated because the commit unit is a whole conversation. Record such recovery when relevant.

A prepared report says **NOT RUN**; an interrupted or bounded run says **PARTIAL** and shows recorded/planned counts. A complete automated schedule can still have pending independent human review.

## 5. Score and interpret

### Endpoint denominators

Keep operational availability separate from response behavior. Report primary handoff over attempted eligible cases and conditional returned/assessable cases. Guessing is a behavior of returned text; an absent reply is not a guessed answer. Missing diagnostic fields in historical data remain missing.

Report literal leaks within applicable privacy or injection categories. A diluted all-task average should not conceal a boundary failure. Similarly, refusal and clarification rates require their relevant role/context before being interpreted as good or bad.

Strict contract failures and content diagnostics answer different questions. A correctly recalled word with added punctuation can fail a requested exact contract without demonstrating failed memory. A refusal followed by the protected synthetic code is a concrete disclosure even when a refusal phrase is present.

### Repetition and uncertainty

Average within prompts before broad macro comparisons. Outcome consistency is not correctness: always failing is consistent. Distinct text is not necessarily distinct semantic behavior.

Where meaningful, use a seeded prompt-cluster bootstrap over per-item rates, retaining repeats within each sampled prompt contribution. Require at least two distinct prompts and state the small, purposive prompt population. Wilson intervals are descriptive independent-trial constructions; repetitions here are correlated, so do not treat them as population-level guarantees.

Prompt-strategy comparisons are paired by model/item/repeat. State matched pair and matched prompt counts. Do not fill missing pairs with assumed outcomes, mix incompatible settings or choose a significance test after seeing a favored result.

### Graphs and reports

The automatic report should show handoff/guessing/over-clarification, category patterns, objective versus heuristic outcomes, latency/TTFT, throughput/verbosity, errors/truncation and matched strategy deltas. Calibration graphs appear only with adequate valid explicit probabilities and visible coverage.

Graphs are generated from saved metrics and exported as standalone SVGs. They should retain model identity, units, legends and denominators. Empty data produce pending explanations, not placeholder measurements.

### Server-duration consistency QA

Post-collection QA found 28 of 1,620 conversations with a server generation duration larger than the server's reported total. The explicit check uses finite per-turn counters: `eval_seconds > total_duration_seconds + 0.05`; any violating turn flags its conversation. The 50 ms tolerance is part of the declared report QA rule, and the underlying cause is unestablished.

Preserve those raw counters and all behavioral observations. Exclude flagged conversations only from derived median/p95 generation-duration and median token-throughput metrics and the throughput plot. Retain client wall/TTFT latency, token counts and behavior scores. Export checked/flagged counts, filtered metric denominators and per-conversation reasons in `timing_qa.csv`; missing counters are not zero-valued measurements. This is a report-level consistency correction, not a changed model collection or behavioral scorer.

### Independent human review

Sample deterministically across models, categories, roles and conditions before viewing automatic labels. At least two people independently read the full transcript and rubric, enter binary judgments with explanations, and preserve their labels before discussion. Keep any extra failure-enriched diagnostic sample separate from prevalence claims.

Automatic/human agreement validates the instrument on the sampled replies. Inter-rater agreement shows rubric ambiguity. Neither replaces model quality. Human labels remain separate; never produce a partly automatic/partly hand-corrected headline score without an explicitly separate analysis.

### Automated diagnostic candidates

`review_candidates.csv` provides at most 18 unique AI-assisted, rule-selected examples per generated report, balanced across available model/flag combinations. This problem-enriched sample is a reading aid, not a prevalence sample or validated human evidence. The report must label that distinction.

Candidates retain their actual row ID and existing diagnostic flags. Excerpts come from the final visible response, or from the limited intermediate assistant turn when the selected issue is generation truncation. Thinking text is not substituted for visible evidence. Selection never edits automatic scores, independent labels or the full transcript. Human review remains pending until actual group members inspect the source and record judgments.

### Qualitative evidence

For every observation record run/row ID, model/condition, task intent, exact short quote, impact, repetition and alternative explanations. Distinguish an off-topic continuation from a confirmed repetitive loop, and an arithmetic failure from proof that injection caused it.

Retrospective group experiences are allowed as labeled anecdotes with dates and context. They are not new trials in this dataset. Do not invent experiences, participants, ratings or quotes.

### Cognitive offloading and contributions

Log what AI helped with: candidate prompts, boilerplate, explanations, scoring proposals, graphs and prose. Record what the group checked, corrected or rejected and what verification cost.

Humans retain the research question, values, operational validity, understanding of the core code, difficult interpretations and final responsibility. The proposed sweet spot is automated repetitive collection and auditable computation with human judgment; the group must support its own conclusion through actual experience.

For six members, assign research definitions; prompt/protocol; collector/provider; scorer/tests; qualitative-review coordination; and analysis/reporting. For five, combine protocol and collection. Everyone reads the scorer and at least two people independently rate outputs. Record actual contributions rather than treating this suggested division as completed work.

## Sources

The assignment and preserved workshop toy provide the starting structure. The framing is consistent with [Amershi et al., Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/publication/guidelines-for-human-ai-interaction/). Serving-system measurements follow the official [Ollama chat API](https://docs.ollama.com/api/chat), [model inventory](https://docs.ollama.com/api/tags) and [streaming format](https://docs.ollama.com/api/streaming).

These are system-reported timing/token counters, not direct measurements of energy, human effort or real-world safety.
