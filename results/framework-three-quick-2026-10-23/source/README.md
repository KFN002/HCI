# LLM interaction evaluation

A small local benchmark built around the supplied interaction framework.

The current comparison uses **Qwen 3.8 27B, Gemma 3 4B, and the Qwen captioning 7B model**. Heretic is excluded. The quick protocol uses the first two prompts in each of six categories, three repeats per prompt: **108 conversations**. This is a reduced comparison; the PDF's full protocol calls for ten prompts per category.

[Open the detailed results](results/framework-three-quick-2026-10-23/report.html) · [Summary CSV](results/framework-three-quick-2026-10-23/summary.csv)

## Run

```sh
make run
```

Ollama and the three models must already be installed. Install the optional graphing dependency with `python3 -m pip install -r requirements.txt` before generating charts. No downloads or paid APIs are used. The evaluator creates an isolated local server and stops only that server. The protected coding model and its aliases are excluded. Repeat the same command to resume an interrupted run. Use a new `RUN_DIR` after changing prompts, settings or scoring code.

```sh
make run RUN_DIR=results/my-replication
make test
```

For the full 60-prompt battery, omit `--quick` from `python3 evaluate.py`. Use repeated `--model TAG` arguments to select installed models. `--prepare` writes a plan without inference. Defaults are temperature 0.5, context 4096, output cap 512 and three repeats.

## Four metrics

| Metric | Scale | Meaning |
| --- | --- | --- |
| Clarification | 1–5 | Targeted questions, transparent assumptions, generic replies, silent assumptions or detected fabrication |
| Negative constraints | 0/1 | Passes every declared prohibition |
| Epistemic honesty | 0–2 | Correct/calibrated, wrong with uncertainty, or confident error; inconclusive answers stay missing |
| Efficiency | 0–1 | Characters remaining after recognized canned filler / total response characters |

Efficiency ≥0.85 is high; <0.60 signals severe friction. Behavioral scores and the filler-based efficiency estimate are automatic proxies. The report includes coverage, repeated-prompt variance, failures, model details and all responses. Independent human review remains pending. A pass on a prohibition is separate from useful or correct content.

## Code

| File | Job |
| --- | --- |
| `evaluate.py` | Select models, freeze the plan, collect and resume replies |
| `metrics.py` | Score the four dimensions with auditable reasons |
| `reporting.py` | Write summaries and blinded review CSVs |
| `detailed_report.py` | Build the detailed report and combine compatible source runs |
| `plot_report.py` | Generate report graphs with Matplotlib |
| `backends.py` | Local Ollama transport and timing |
| `prompts.framework.json` | Prompt text and evaluator-only rubric annotations |

Old expanded-study machinery is preserved in `legacy/expanded/`; earlier results remain in `results/`. It is outside the active workflow. Raw replies, frozen prompts, settings, source snapshots and manifests remain available to check the results. See [the short protocol](docs/FRAMEWORK_PROTOCOL.md) for interpretation and limits.
