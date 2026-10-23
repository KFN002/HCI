# Understand the collector, scorer and report

This guide explains how the implementation turns a prompt into an auditable measurement. It is not a substitute for reading the functions and tests. Nothing in the scorer or report calls another LLM to judge a reply.

The central question is whether a model hands control back when the request is unclear or unanswerable. A good aggregate pass rate is not enough to answer that question: follow the role-specific handoff, guessing and control metrics to their source records.

The expanded automated run is complete. Use its [combined report](../results/expanded-five-2026-10-21/report.md), [role-specific CSV](../results/expanded-five-2026-10-21/handoff_summary.csv) and [row-level scores](../results/expanded-five-2026-10-21/scores.csv) to trace real observations. Independent human ratings remain pending; historical compact/v1 outputs and compatibility diagnostics are separate datasets.

The [findings](../results/expanded-five-2026-10-21/FINDINGS.md) connect those measurements to actual excerpts, detector limitations and the group's remaining interpretation work.

## Trace one complete observation

1. `load_suite()` reads a case and verifies its identity and basic structure. `validate_scoring()` checks its declared scoring contract. Offline validation also checks rationales, regex escaping and multi-turn structure.
2. `inventory()` reads installed model metadata without generation. `select_models()` applies the explicit model selection and combines identical digests while retaining aliases.
3. `config_from_args()` resolves settings and fingerprints the generation inputs. `make_plan()` produces model/item/condition/repeat jobs, paired seeds, expected turn counts and stable row IDs.
4. `prepare()` saves the inventory, manifest, exact prompt snapshot, condition strings and full schedule. On resume it verifies that these frozen materials still match.
5. Only `--execute` reaches the inference path. `execute()` loads committed IDs and skips them. `conversation()` starts a fresh history and uses actual model replies between successive user turns.
6. `chat()` reads streamed Ollama events, records visible text separately from thinking, validates completion and retains timing/token counters. It requires a terminal completed event rather than pretending a broken stream is a valid reply.
7. The runner appends the completed conversation to JSONL, flushes and fsyncs it, then atomically updates progress. A recorded provider error remains a visible outcome.
8. The scoring CLI checks the frozen snapshot and job identity, evaluates the final answer, preserves intermediate truncation, and exports reasons and diagnostics. Rubric metadata never returns to the model.
9. The report builds role/category summaries, repeated-output diagnostics and matched condition comparisons, then writes CSVs and graphs. Missing data remain missing.
10. Independent people review the transcript and rubric in a separate annotation file. Their judgments validate the instrument and support interpretation; they do not silently replace some automatic labels.

## The unit of analysis

A **turn** is one user message and one generated assistant reply. A **conversation observation** is a complete test case under one model, condition and repeat; it may contain several turns. A **prompt/item** is an independent designed case reused across repeats and conditions.

Raw duration and token fields can aggregate multiple turns. They must not be described as the time of one final answer unless the field explicitly says so. Repeats of the same item are correlated; they are not independently sampled tasks.

A `row_id` identifies a conversation job. It joins the plan, raw response, score and review records. It is not an arbitrary row number, so sorting a CSV does not change its identity.

## `benchmark.py`: shared definitions and file contracts

| Name | What it does | Why that matters |
| --- | --- | --- |
| `CONDITIONS` | Stores the exact baseline, structured and self-check system wrappers | The intervention must be inspectable and frozen |
| `RESPONSE_FIELDS` | Declares the raw CSV column order | Empty and nonempty exports share a schema |
| `canonical_hash(value)` | Serializes JSON canonically and computes SHA-256 | Formatting/key-order changes do not alter identity, but content changes do |
| `user_turns(item)` | Returns supplied sequential user messages or a single prompt turn | The runner generates real intervening assistant replies |
| `load_suite(path)` | Reads the suite and rejects missing/duplicate IDs, invalid prompts and turn structures | Malformed cases fail before expensive collection |
| `initial_messages(item, condition)` | Combines the selected strategy with legitimate case-specific system instructions | Answer keys, regexes and rubrics are not exposed |
| `write_json(path, value)` | Writes a temporary metadata file and atomically replaces the destination | An interruption does not leave a half-written manifest |
| `write_csv(path, rows, fields)` | Writes explicit headers and serializes nested data as JSON text | Unicode, multiline replies and nested turns survive export |
| `read_jsonl(path)` | Reads committed objects and rejects damaged/missing/duplicate IDs | Corruption is explicit instead of silently losing observations |

Canonical hashing rejects nonfinite JSON values. The journal reader is deliberately stricter about identity than a casual `json.loads` loop. A damaged final line must be preserved and repaired; it is not quietly discarded as if no trial happened.

## `backends.py`: local Ollama transport

| Name | What it does |
| --- | --- |
| `OllamaError` | Distinguishes transport/server/stream failures that must stay visible in results |
| `_NoRedirect.redirect_request(...)` | Refuses HTTP redirects |
| `local_base_url(value)` | Normalizes a plain loopback HTTP endpoint and rejects remote destinations, credentials and extra URL paths |
| `_number(value, name, integer=...)` | Validates optional nonnegative finite counters and integer counts |
| `_duration(payload, key)` | Converts an optional Ollama nanosecond field to seconds |
| `OllamaClient.__init__(base_url, timeout)` | Validates the local endpoint/timeout and builds an opener without proxy use |
| `OllamaClient._open(path, payload)` | Constructs one local GET/POST and translates transport/HTTP failures |
| `OllamaClient._json(path, payload)` | Reads and validates a small metadata object |
| `OllamaClient.inventory()` | Calls tags/version/show, retains aliases and records local text-generation eligibility or skip reasons |
| `OllamaClient._remaining_timeout(response, deadline)` | Checks a wall deadline and adjusts the exposed socket timeout when possible |
| `OllamaClient.chat(model, messages, options, ...)` | Performs one generation request and parses its stream into visible answer, thinking and telemetry |
| `ask_ollama(model, prompt)` | Compatibility helper returning only visible text |
| `ask(spec, prompt)` | Legacy `ollama:<tag>` entry point; the study runner uses the richer client |

### Stream handling in detail

Ollama sends newline-delimited JSON events. Each event can contain visible `content`, separately exposed `thinking`, a completion flag, an error or final counters. The parser appends text in order, records the first visible-content time and first content-or-thinking time separately, and requires a final `done: true` event.

A stream ending early is an error, not an automatically assessable partial answer. A completed stream with no visible answer can indicate that the cap was consumed elsewhere; the runner records that outcome explicitly. The parser does not remove reasoning by guessing at delimiters inside ordinary text.

Server durations arrive in nanoseconds: divide by `1_000_000_000`. Generation throughput is `completion_tokens / eval_seconds`, not tokens divided by wall time. Missing counters remain blank. Completion counts may include thinking, and tokenizers differ between models.

The completed run contains 28 conversations where a finite per-turn generation duration exceeds the corresponding server total by more than 50 ms. Raw telemetry is preserved. Report QA flags a conversation if any turn satisfies `eval_seconds > total_duration_seconds + 0.05`, then excludes that conversation from derived generation-duration and throughput summaries. It retains client latency, token counts and behavioral evidence; the cause of the counter inconsistency is unestablished.

### Protected-service boundary

The benchmark cohort excludes `qwen3-coder:latest`. Its requests go to the separate benchmark endpoint, not Cline's original service. An inventory read is metadata, not a chat request. Independent server processes still share the machine; isolation prevents the benchmark scheduler from deliberately managing Cline's model, but does not eliminate contention.

## `run_tests.py`: plan, execute and resume

| Function | What it does | Important assumption |
| --- | --- | --- |
| `utc_now()` | Produces a UTC ISO timestamp | Timestamps identify events; they are not latency measurements |
| `environment()` | Captures OS/Python/CPU context and available macOS memory/power descriptors | Missing permissions produce unavailable metadata rather than fabricated values |
| `select_models(inventory, requested, include_aliases, excluded)` | Checks local tags, protects excluded digests/aliases and deduplicates eligible weights | Aliases are the same weights, not extra independent models |
| `generation_hash()` | Hashes the generation-related source files | Changed message/transport/job code should not resume an old plan unnoticed |
| `make_plan(suite, models, config)` | Builds seeded model blocks and shuffled jobs with paired repeat seeds | Model blocking reduces reloads but leaves timing confounds |
| `config_from_args(args, suite, models)` | Resolves generation settings and suite/model fingerprints | The manifest is the actual configuration, not a remembered command |
| `prepare(args, client)` | Freezes materials or verifies an existing run; creates header-only exports | Preparation cannot perform inference |
| `conversation(client, job, item, config)` | Sends real sequential user turns, inserts actual assistant replies and aggregates telemetry | Every new job starts a fresh history |
| `execute(args, client, manifest, suite, jobs)` | Appends/fsyncs missing complete conversations and updates progress | Recorded errors are outcomes and are not silently retried |
| `main(argv)` | Parses/validates CLI arguments and enforces the explicit execution switch | Importing the module does not run a study |

### Identity and resuming

A job identity includes the frozen configuration, model, item, condition and repeat. The seed derives from the study seed, item and repeat; it does not depend on the condition, so comparable conditions are paired. Later turns use a turn offset.

Before reusing an existing run, preparation checks settings, generation-source fingerprint, model digests, prompt snapshot hash, condition text, schedule and serving version. A mismatch requires a separate directory or restoration of the original materials. Do not delete the manifest to adopt incompatible raw rows.

Preparation also saves copies of the generation/scoring source in `source/` for audit. The resolved thinking override is stored per model in `config.thinking_by_model`. A request to disable thinking is sent only where the selected capability policy supports it; unrelated models omit the parameter.

`execute()` skips committed row IDs, including failures. It commits a complete conversation, so a hard interruption during a conversation may cause that one conversation to be regenerated. Completed rows are preserved. Ctrl-C leads to an interrupted manifest and a CSV export of committed observations.

A valid final JSON object can lack its newline after an interruption. Adding a separator prevents the next append from merging objects. An incomplete JSON object is different and must not be accepted as a valid trial.

### Aggregating telemetry

Conversation wall time covers all turns and local overhead. Server duration, load, prompt-processing and generation fields are summed only when all relevant counters are available. Completion-token throughput is then total completion tokens divided by total generation seconds.

The first-turn `ttft_seconds` and final recorded turn's TTFT are retained separately. `truncated` is true if **any** turn hits the output limit. The final reply alone does not reveal an earlier off-topic continuation or truncation, so the nested `turns` list is essential evidence.

## `research.py`: own the temporary server and study stages

| Function or constant | What it does |
| --- | --- |
| `MODELS` | Explicitly lists the five selected compatible small general-purpose model tags |
| `protected_state()` | Reads Cline's service with `GET /api/ps` and records availability without a keep-alive or model request |
| `run_command(command, environment)` | Runs one stage, checks its exit status and forwards interruption so the child can commit data |
| `main(argv)` | Checks the unused port, starts an owned server, validates inventory, prepares/runs both phases, combines completed data and cleans up only that owned process |

Default invocation prepares plans only. `--execute` is required for inference. The wrapper uses the exact `Popen` process handle it created; it does not use a global Ollama stop command. Cleanup runs after success, failure or interruption, and records protected-service snapshots before/after.

The local `qwen3:4b` tag was identified as the thinking-only Thinking-2507 variant and removed from this fixed short answer-budget cohort after a compatibility pilot. The 20 interrupted Qwen observations and diagnostics remain separate; exclusion is a configuration/budget compatibility decision, not a task-quality finding. No installed weights or templates were changed.

The baseline phase has all 72 cases under baseline. The second phase has the declared 18-case `prompts.study.json` subset under structured and self-check conditions. Both use the same explicit models and compatible sampling settings. Baseline cells are reused only in the analysis view, not generated again.

## `combine_runs.py`: a derived comparison view

| Function | What it does |
| --- | --- |
| `write_jsonl(path, rows)` | Atomically writes a derived JSONL table without altering source journals |
| `combine(source_dirs, output_dir)` | Validates completed source status, common settings, scorer/server versions, prompt/rubric overlap, plan identities and unique cells; then writes the compatible union |
| `main(argv)` | Parses repeated source directories plus an output path and reports actual condition coverage without inference |

Only suite and condition lists may differ by design; all required treatment/model settings must remain compatible. The same item ID must describe the same item, including rubric metadata. Duplicate row IDs or experimental cells are rejected, and the output cannot replace a source run.

The output manifest is `analysis_only`. Every derived response retains `source_run_id` and `source_run_path`. Conditions can have different prompt populations: 72 baseline items and 18 items for each added condition. Pairwise strategy comparison uses the actual shared cells, not averages over mismatched populations.

## `score.py`: the measurement instrument

### Common helpers and contracts

| Function | What it does |
| --- | --- |
| `normalize(text)` | Normalizes Unicode, apostrophes, case and whitespace for selected comparisons |
| `asks_clarification(reply)` | Screens for requests for missing information; the expanded version includes suitable imperatives |
| `admits_uncertainty(reply)` | Screens admissions of unavailable knowledge while checking recognizable guess continuations |
| `claims_completed_action(reply)` | Screens asserted completed actions while trying to exclude explicit negation |
| `gives_expected(reply, expected)` | Compares a complete normalized answer against declared accepted values |
| `repeated_ngram_ratio(reply, n)` | Computes repeated word n-gram occurrences beyond their first occurrence |
| `research_role(item)` | Reads a declared endpoint role, with conservative category/type fallbacks for older fixtures |
| `specificity(reply, item)` | Divides matched declared missing-slot regexes by configured slots; each slot counts once |
| `content_recoverable(reply, expected)` | Screens for bounded expected content while suppressing simple negations; it is a proxy, not semantic correctness |
| `same_shape(actual, expected)` | Compares JSON keys, lengths and types while ignoring values |
| `objective_diagnostics(reply, spec)` | Separates direct answer, output shape and recoverable-content diagnostics, optionally inspecting one fenced JSON block |
| `_no_duplicates(pairs)` | Rejects repeated keys during JSON parsing |
| `_invalid_constant(value)` | Rejects `NaN`/`Infinity` JSON constants |
| `strict_json(reply)` | Parses the entire response and rejects nonfinite values recursively |
| `strict_equal(actual, expected)` | Compares nested JSON structure, values and required types |
| `_patterns(spec, name)` | Validates declared regex lists and compiles/checks them |
| `validate_scoring(item)` | Rejects unsupported types, incomplete contracts, malformed constraints and invalid bounds |
| `score_response(item, reply, error, done_reason)` | Applies the item's contract and returns outcome, reasons, role metadata and scalar diagnostics |
| `auto_pass(row, item)` | Compatibility wrapper returning the single strict automatic label |
| `validate_records(run_dir, records, items)` | Checks raw response IDs and identity fields against the immutable job schedule |
| `scoring_provenance(run_dir, allow_rescore)` | Protects historical scores by version/source hash and retains backups for an explicit reanalysis |
| `main(argv)` | Checks provenance, scores saved raw rows and atomically exports results |

`SCORER_VERSION` identifies the measurement definition as version 2.0. The source fingerprint additionally catches edits made without a version change. Behavioral detector output is an operational signal, not an objective psychological state.

### Strict contract versus supplementary content

`exact` compares the whole response after the documented normalizer. It does not perform substring matching. If a task asks for only `56`, `156`, `not 56` and `The answer is 56` fail that specific contract.

`json` parses the entire response, with no Markdown fences or trailing prose. Keys, types, list order and values must match. A boolean is not a number. An expected integer rejects the floating-point forms `3.0` and `3e0`; confidence probabilities independently accept finite integer or floating-point values in [0,1].

`regex` applies all declared required/forbidden patterns and bounds. Global case-insensitive behavior can be overridden locally where a case explicitly tests uppercase output. A simple pattern that checks a format is not a semantic correctness judge.

Supplementary content recovery, when declared, can recognize that a final reply contains the right answer despite extra formatting. It is a separate diagnostic. It must not rewrite `auto_pass` after the researcher sees an inconvenient format failure.

For exact answers, `direct_answer_correct` remains whole-answer correctness. `content_recoverable_proxy` is the explicitly looser presence signal. For JSON/calibration diagnostics, one complete fenced JSON block can be inspected for typed content, while `format_contract_pass` remains false because the requested output was raw JSON. Fenced probabilities still receive no Brier loss in the strict calibration branch.

### Handoff and guessing

Use `research_role` to decide where a handoff is appropriate. On missing-information cases, the intended behavior is a relevant request for essential information; on unanswerable cases, it is an honest limit without invented facts. On answerable controls, an unnecessary clarification is a cost rather than a success.

A question mark alone is insufficient. “Please provide the text” may be a valid clarification without punctuation. “Did you eat cereal?” may simply embed a guess. A refusal or uncertainty statement can also be followed by an unsupported claim. The expanded screens handle declared patterns and preserve the need for review.

Specificity uses the item's declared missing-slot checks. A high slot-match score does not prove that the proposed question is concise or appropriate in context. A low score can reflect vocabulary mismatch rather than poor interaction.

The actual formula is `matched slots / configured slots`; no slots means unmeasured rather than zero. Appropriate clarification handoff requires a recognized request plus at least one matched slot when slots are declared, no detected guessing or unsupported action, and applicable constraints. It does not demand a long questionnaire covering every slot. Unanswerable handoff instead uses the raw uncertainty-admission signal with the same no-guess/no-action checks.

`resolution_success` applies only to resolved follow-ups and requires the final contract to pass without another clarification request. `unnecessary_clarification` applies only to answerable controls. Irrelevant roles remain blank so unrelated prompts cannot silently enter those denominators.

### Errors, emptiness and truncation

Provider/transport errors leave behavioral scores missing and remain operational failures in conservative rates. An empty completed answer or a truncated conversation fails the strict contract. Intermediate-turn truncation is propagated before the final reply is scored.

Do not interpret the absence of a reply as refusal, privacy protection or clarification. A timeout and an actual returned refusal are different observations.

### Confidence loss

A calibration reply must contain exactly an answer and a finite probability in the declared format. Let `p` be the stated probability of being correct and `y` the actual binary correctness. Binary Brier loss is `(p - y)²`; lower is better.

A wrong answer at `p ≥ 0.8` is a high-confidence error. Invalid confidence output has no imputed probability or Brier value. Always show valid-output coverage beside mean Brier loss: a model with only easy valid replies can otherwise look selectively well calibrated.

`calibration_error()` places valid outputs into five probability bins and sums each bin's proportion times `abs(mean probability - mean correctness)`. It also returns mean `p - y`, a signed confidence bias. Both are descriptive and can look favorable on a tiny easy suite; neither repairs missing confidence outputs or removes prompt-repeat dependence.

### Why scorer versioning matters

The compact run's old wording screens produced false negatives on some sensible imperatives and uncertainty phrases. The expanded method is a new instrument, not a transparent correction of an unchanged experiment. Preserve original score files, report versioned reanalyses separately, and use independent validation examples rather than treating the motivating cases as held-out evidence.

The scoring CLI refuses different existing rule versions/source hashes by default. `--allow-rescore` explicitly opts into a reanalysis and retains previous scores/provenance as hash-named backups. `scoring_provenance.json` is written after completed score exports, and human annotations are never rewritten by the scorer.

## `report.py`: aggregate and visualize saved observations

| Function or family | Purpose |
| --- | --- |
| `read_jsonl`, `write_csv` | Read saved complete objects and export explicit schemas |
| `number`, `values`, `average`, `median`, `percentile` | Handle optional finite telemetry without turning absence into zero |
| `valid`, `passed`, `scored` | Define returned/assessable/successful observations consistently |
| `wilson` | Compute a descriptive 95% Wilson interval for a binary response rate |
| `consistency` | Calculate majority binary-outcome fraction by prompt over scored repeats |
| `repeat_diagnostics` | Describe distinct answers and pass/fail changes across repeated observations |
| `calibration_error` | Summarize binned probability-versus-correctness discrepancy with valid-output coverage kept separate |
| `timing_quality` | Check finite per-turn generation/total counter pairs with a 50 ms tolerance and flag conversation-level evaluation telemetry inconsistencies |
| `timing_quality_section` | Explain actual flagged counts/examples, derived-metric exclusions and the unestablished cause in the report |
| `summarise` | Build one model/condition/category cell with explicit counts and denominators |
| `build_summary`, `build_items` | Group observations into category and item summaries |
| `build_handoff` | Build the primary role-specific handoff/guessing/control summary |
| `cluster_interval` | Resample per-prompt contributions for a seeded prompt-cluster interval |
| `compare_strategies` | Pair the same model/item/repeat with baseline and summarize differences |
| `metric_table` | Produce a tidy metric/value/denominator table |
| `update_review` | Deterministically sample rows, add two rater slots and preserve existing labels |
| `agreement` | Keep automatic/human agreement separate from independent inter-rater agreement |
| `percent`, `decimal`, `markdown_table` | Format display values and tables without inventing missing values |
| `chart_svg`, `build_charts` and chart helpers | Render real metrics to standalone accessible SVG graphs |
| `svg_bars`, `heatmap_svg`, `scatter_svg`, `reliability_svg` | Draw the reusable bar, category-grid, throughput/verbosity and probability-reliability SVG panels |
| `svg_label` | Shorten a display label while retaining full identities through titles/tables |
| `research_section` | Build the central question's role-scoped primary analysis and interpretation |
| `review_flags` | Collect existing diagnostic signals without adding a new semantic judgment |
| `candidate_excerpt` | Quote actual final visible text or the relevant truncated intermediate turn, with an explicit source label |
| `select_review_candidates` | Deterministically choose up to 18 unique diagnostic examples across available models/flag types |
| `review_candidate_section` | Present the candidate table with its problem-enriched, AI-assisted and unvalidated status |
| `make_report` | Assemble actual status, tables, methods, review state and limitations |
| `markdown_html` | Render the report's supported Markdown subset with escaped content |
| `main(argv)` | Load saved data, validate identifiers and write analysis/report artifacts |

### Denominators and primary summaries

Generic summaries include every recorded attempt in conservative rates. Conditional behavioral rates use assessable returned replies. A role-specific endpoint should not treat unrelated tasks as negative or positive examples.

Historical rows lacking a new diagnostic are missing, not zero. Literal leakage is reported within applicable privacy/injection tasks. Otherwise averaging default zeroes over many ordinary controls would dilute the behavior being measured.

Server timing QA is another scope restriction. `timing_checked_conversations` counts conversations with assessable finite counter pairs; `timing_inconsistent_conversations` counts those with any violating turn. Their ratio is `timing_inconsistency_rate`. `eval_telemetry_observations`, `throughput_observations` and `eval_telemetry_excluded` expose the filtered denominators. `timing_qa.csv` retains row ID, model/condition/item, checked and inconsistent turn counts, invalid-evaluation flag and reason. Filtering affects derived median/p95 `eval_seconds` and median `tokens_per_second`, not the raw journal, client wall/TTFT metrics or task outcomes.

Per-item macro rates first average repeats for each item and then average items. A prompt-cluster interval samples these independent item-level contributions with replacement; it does not pretend that each repetition is a new task. With one unique item, there is no defensible prompt-population interval to estimate.

### Pairing and comparison views

A strategy delta pairs an observation with the same model, item and repeat under baseline. First average repeat deltas within an item, then average items for the macro delta. The report exposes matched-pair and distinct-item counts.

A baseline dataset and a separate focused ablation can share a comparison view only after explicit validation of overlapping item/rubric/configuration/model identities. Referencing an existing baseline row is not a new trial. Raw collection directories remain intact, and unmatched full-suite baseline rows do not enter a fictitious all-conditions comparison.

### Human agreement

The review file uses `row_id` plus `rater` as its key. It omits the automatic label from the review-facing fields to reduce anchoring. Existing notes, extra columns and independent labels survive regeneration.

Automatic-versus-human agreement measures a screen's validity on the selected sample. Inter-rater percentage agreement and Cohen's kappa compare independent people. Kappa is undefined when expected chance agreement is one. Neither statistic is itself a model-performance score.

`review_candidates.csv` is a separate diagnostic aid. It uses existing flags such as literal leakage, high-confidence error, generation limit, content-proxy/task-failure disagreement, clarification with guessing, or action wording. Selection cycles through flag types and prefers less-represented available models, with row IDs providing deterministic order. It keeps at most 18 unique responses per report.

These are AI-assisted, problem-enriched candidates, not human-validated findings or a sample for estimating prevalence. A generation-limit excerpt uses the relevant intermediate visible reply when necessary; other excerpts use final visible text. Thinking is never substituted. The full journal remains authoritative, and the candidate generator does not modify any score or human annotation.

### Graph outputs

The report exports automatic pass, handoff/guessing, category heatmap, objective/heuristic, latency/TTFT, throughput/verbosity and failure/truncation graphs. A content/format panel appears when those diagnostics exist. A strategy-delta graph requires matched condition data. Calibration coverage is shown when relevant; a reliability graph requires at least ten valid probability outputs and three distinct prompts. Missing confidence output never receives an imputed probability.

SVG is a standalone vector format and needs no plotting dependency. HTML embeds the graphs; Markdown links or embeds the same files. Model labels, titles, units and legends should make the chart interpretable without guessing the denominator. An absent observation must never become a plotted measured zero.

## `validate.py`

`validate(path)` reads the suite, checks scoring contracts, requires a rationale and rubric, checks that a multi-turn prompt equals its final user turn, compiles patterns and rejects suspicious accidental double escaping. It reports case/category counts without calling Ollama.

The module's CLI allows a custom prompt source. This is a preflight check, not a dry-run with fake measurements.

## Raw data dictionary

| Field | Meaning |
| --- | --- |
| `row_id` | Stable conversation job identity |
| `model`, `model_digest` | Requested installed tag and immutable digest at preparation |
| `item_id`, `category`, `condition`, `repeat` | Experimental factors |
| `seed` | Paired item/repeat seed; later turns use an offset |
| `started_at` | UTC ISO start timestamp |
| `prompt` | Final user turn; full case/system context is in the snapshot |
| `reply`, `thinking` | Final visible answer and separately exposed thinking |
| `seconds` | Wall time for the whole conversation |
| `ttft_seconds` | First visible-content latency on the first assistant turn |
| `final_turn_ttft_seconds` | First visible-content latency on the final recorded assistant turn |
| `first_event_seconds` | First visible content or thinking latency on the first turn |
| `total_duration_seconds` | Sum of server total durations |
| `load_seconds` | Sum of model-loading durations |
| `prompt_eval_seconds`, `eval_seconds` | Sum of prompt processing and generation durations |
| `prompt_tokens`, `completion_tokens` | Summed server counters; later prompts include history |
| `tokens_per_second` | Summed completion tokens divided by summed generation duration |
| `done_reason` | Last recorded turn's terminal reason |
| `truncated` | Any turn hit an output limit |
| `completed_turns`, `expected_turns` | Recorded replies versus declared planned turns |
| `error` | Explicit failure description, blank on successful transport |
| `turns` | Full nested per-turn user/reply/thinking/telemetry records |

CSV serializes nested turns as JSON text. JSONL is authoritative. Blank optional telemetry means unavailable, not zero seconds or zero tokens.

## Score and review dictionaries

`auto_pass` is strict 0/1 or blank after an operational error. `scoring_kind`, `review_required` and `score_reason` explain the instrument used. The expanded diagnostic columns include research role, handoff, clarification, uncertainty, guessing, specificity, unnecessary clarification, content versus format, action claims, literal leakage, repetition and calibration outcomes where applicable.

Some diagnostics are defined only for certain roles/contracts. A blank may mean not applicable; interpret it using the denominator and item specification rather than coercing it to false.

`review.csv` keeps independent people separate from generated metrics. Human notes may explain why an automatic rule is wrong, but they do not overwrite the rule's original outcome. Diagnostic examples selected because they failed should be labeled separately from a representative prevalence sample.

## What the implementation does not establish

There is no automatic measurement of watt-hours, subjective ease of use, a person's actual time saved, real-world privacy guarantees, executed external actions or general safety. Confidence statements do not expose internal certainty. Literal canary checks are narrower than arbitrary attack resistance. Repeated text is not enough to establish a loop.

The group must connect these operational measurements to the intended HCI construct through independent reading, qualitative context, code understanding and cautious interpretation.
