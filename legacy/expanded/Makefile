.DEFAULT_GOAL := help
PYTHON ?= python3
OLLAMA_URL ?= http://127.0.0.1:11434
export OLLAMA_URL
RUN_DIR ?= results/prepared
REPEATS ?= 5
SEED ?= 20261021
TEMPERATURE ?= 0.5
NUM_PREDICT ?= 512
NUM_CTX ?= 4096
TIMEOUT ?= 180
CONDITIONS ?= baseline structured self_check
MODEL_ARGS ?= --all-local
EXTRA_ARGS ?=
RESEARCH_PREFIX ?= results/expanded-five-2026-10-21
FRAMEWORK_RUN ?= results/framework-five-2026-10-23

RUN_ARGS = --run-dir "$(RUN_DIR)" $(MODEL_ARGS) --repeats $(REPEATS) --seed $(SEED) --temperature $(TEMPERATURE) --num-predict $(NUM_PREDICT) --num-ctx $(NUM_CTX) --timeout $(TIMEOUT) --conditions $(CONDITIONS) $(EXTRA_ARGS)

.PHONY: help setup validate test check prepare plan run score report pipeline smoke research research-prepare framework framework-prepare

help:
	@echo "Local Ollama interaction study (default: help; no inference)"
	@echo "make check      - fast offline validation and unit tests"
	@echo "make prepare    - metadata discovery, frozen plan, empty CSVs, NOT RUN report"
	@echo "make pipeline   - RUN MODELS, then score, export CSVs and build reports"
	@echo "make run        - RUN MODELS only; same command resumes saved conversations"
	@echo "make report     - rescore existing replies and regenerate reports offline"
	@echo "make smoke      - RUN a separate two-conversation llama3.2 pilot"
	@echo "make research-prepare - prepare expanded five-model plans on an isolated temporary server"
	@echo "make research   - RUN expanded baseline + focused prompting study; graphs and combined CSVs"
	@echo "make framework  - RUN PDF framework: 60 cases x 3 repeats x 5 models on an isolated server"
	@echo "Options: RUN_DIR, REPEATS, MODEL_ARGS, CONDITIONS, NUM_PREDICT, EXTRA_ARGS"

setup:
	$(PYTHON) -m venv .venv
	@echo "No pip packages required. Optional: source .venv/bin/activate"

validate:
	$(PYTHON) validate.py
	$(PYTHON) validate.py --prompts prompts.framework.json

test:
	$(PYTHON) -m unittest discover -s tests -v

check: validate test

prepare: validate
	$(PYTHON) run_tests.py $(RUN_ARGS)
	$(PYTHON) score.py --run-dir "$(RUN_DIR)"
	$(PYTHON) report.py --run-dir "$(RUN_DIR)" --prepare

plan: prepare

run: validate
	$(PYTHON) run_tests.py --execute $(RUN_ARGS)

score:
	$(PYTHON) score.py --run-dir "$(RUN_DIR)"

report: score
	$(PYTHON) report.py --run-dir "$(RUN_DIR)"

pipeline:
	$(MAKE) run
	$(MAKE) report

smoke:
	$(MAKE) pipeline RUN_DIR=results/pilot MODEL_ARGS='--model llama3.2:3b' REPEATS=1 CONDITIONS=baseline EXTRA_ARGS='--max-jobs 2'

# This complete workflow owns its own Ollama process. It never restarts Cline's
# service, and stops only that owned process after collection or interruption.
# Research uses three repeats by default, independently of the full-suite
# REPEATS setting above. Override it by invoking research.py --repeats N.
research-prepare: validate
	$(PYTHON) research.py --run-prefix "$(RESEARCH_PREFIX)"

research: validate
	$(PYTHON) research.py --execute --run-prefix "$(RESEARCH_PREFIX)"

framework-prepare:
	$(PYTHON) validate.py --prompts prompts.framework.json
	$(PYTHON) research.py --framework --run-prefix "$(FRAMEWORK_RUN)"

framework:
	$(PYTHON) validate.py --prompts prompts.framework.json
	$(PYTHON) research.py --framework --execute --run-prefix "$(FRAMEWORK_RUN)"
