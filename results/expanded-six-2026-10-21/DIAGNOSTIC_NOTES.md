# Excluded reasoning-configuration diagnostic pilot

This directory and `../expanded-six-2026-10-21-baseline/` contain an interrupted compatibility pilot, **not the final comparative study**. They are excluded from the expanded five-model report and its denominators.

The tag `qwen3:4b` initially looked like the original hybrid Qwen3 model. Its local `/api/show` metadata instead identifies the installed weights as the July 2025 **Thinking** variant: `general.finetune=Thinking`, `general.version=2507`, and a license link to `Qwen3-4B-Thinking-2507`. Its [official model card](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507) states that it supports thinking mode only.

The initial collection requested `think=false` under a 512-token answer budget. The model emitted lengthy reasoning into visible content, often exhausting that budget. Five separate diagnostic calls, preserved in `compatibility-preflight.json`, checked API toggles, the original hybrid model's `/no_think` prompt control, and an ephemeral closed-thinking assistant prefill. None established a functioning no-thinking mode. These diagnostics did not modify the installed model, its template, aliases or weights. They were not requests to Cline's `qwen3-coder:latest`.

We therefore excluded this variant from the fixed answer-budget cohort rather than treating its truncated reasoning as evidence of general task failure. A valid reasoning-model follow-up needs an explicit larger reasoning budget and a separate comparison design. The revised runner now rejects attempts to silently disable known thinking-only weights. The final study still contains five models, within the requested range of five to eight.

This is a concrete methodological observation: model tags and an advertised thinking capability do not establish which control values the installed variant supports. Inspect the underlying model metadata and the behavior of the actual serving configuration before interpreting response scores.
