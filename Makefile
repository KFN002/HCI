.DEFAULT_GOAL := help
PYTHON ?= python3
RUN_DIR ?= results/three-models-quick-demo
QWEN = hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M
GEMMA = gemma3:4b
CAPTION = hf.co/bartowski/thesby_Qwen2.5-VL-7B-NSFW-Caption-V3-GGUF:Q4_K_M

.PHONY: help run test
help:
	@echo "make run   - 3 models x 12 prompts x 3 repeats, then a detailed report"
	@echo "make test  - offline checks; no model inference"
run:
	$(PYTHON) evaluate.py --quick --model '$(QWEN)' --model '$(GEMMA)' --model '$(CAPTION)' --output '$(RUN_DIR)'
	$(PYTHON) detailed_report.py --source '$(RUN_DIR)' --output '$(RUN_DIR)/detailed'
test:
	$(PYTHON) -m unittest discover -s tests -v
