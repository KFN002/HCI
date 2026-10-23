# Reproduce the compact three-model benchmark

This runs the installed Llama 3.2, Granite 4 and Phi-4 Mini weights. Cline's `qwen3-coder:latest` is excluded. Keep Cline's existing Ollama service running on port 11434 unchanged.

## Start an isolated benchmark server

In a separate terminal, first check that port 11435 is unused. Then start a second Ollama process that reuses installed files without pruning or cloud access:

```bash
OLLAMA_HOST=127.0.0.1:11435 \
OLLAMA_MODELS="$HOME/.ollama/models" \
OLLAMA_NOPRUNE=1 OLLAMA_NO_CLOUD=1 \
OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_NUM_PARALLEL=1 \
OLLAMA_KEEP_ALIVE=2m ollama serve
```

This is an independent process and scheduler. Do not stop or reconfigure the server on port 11434. Both still share host resources, so concurrent Cline use can affect timing.

## Run or resume the recorded study

From the project directory, in another terminal:

```bash
make pipeline \
  RUN_DIR=results/three-models-compact-2026-10-21 \
  REPEATS=3 CONDITIONS=baseline \
  MODEL_ARGS='--model llama3.2:3b --model granite4:3b --model phi4-mini:3.8b' \
  EXTRA_ARGS='--prompts prompts.compact.json' \
  OLLAMA_URL=http://127.0.0.1:11435
```

An unchanged existing completed run is not regenerated. For a fresh replication, use a new `RUN_DIR`, such as `results/compact-replication-02`. Changing code, settings or model digests requires a separate directory.

The compact suite contains the first case in the full suite's order from each of 14 categories. Three repeats across three models give 126 conversations and 153 chat requests. Only baseline prompting is measured; the original full suite retains three prompting conditions for a later experiment.

## Stop only the isolated server

After the pipeline finishes, press Ctrl-C in the terminal where you started port 11435. Leave Cline's original server alone. Never use `pkill ollama` or a broad stop command while Cline is using it.

## Inspect results

Read `FINDINGS.md` for the concise interpretation, `report.md` / `report.html` for the generated report, and `summary.csv` / `scores.csv` for quantitative data and row-level scoring reasons. Independent human ratings belong in `review.csv`; run `make report RUN_DIR=...` to regenerate analysis without inference and preserve those annotations.
