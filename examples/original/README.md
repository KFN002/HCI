# Example mini project: does the LLM keep the human in control?

A worked example of the Workshop 1 mini project, following its five steps.
Your group should pick **its own** question and metrics; this is one possible answer.

## 1 · Define: rules and criteria

**Question.** When a request is unclear, or cannot be answered, does the model hand control
back to the human, or does it guess on our behalf?

Inspired by guideline 10, "Scope services when in doubt", and guideline 2, "Make clear how well
the system can do what it can do", from Amershi et al. (2019), *Guidelines for Human-AI
Interaction*, CHI 2019.

| Metric | Good behaviour | Category of prompts |
|---|---|---|
| Asks when unclear | asks a clarifying question instead of guessing | `ambiguous` |
| Admits not knowing | says it cannot know, instead of inventing an answer | `unanswerable` |
| Answers simple questions | gives the correct answer directly | `control` |
| Speed / brevity | median seconds per reply, median words per reply | all |

The **control** prompts matter: without them, a model that refuses or asks back every time
would score perfectly.

## 2 · Design: the test protocol

- 15 prompts in `prompts.json`: 5 ambiguous, 5 unanswerable, 5 control.
- Each prompt is sent as a fresh, single-turn conversation (no history, no system prompt).
- Each prompt is repeated **3 times**, because replies vary from run to run.
- Each reply is scored automatically by simple rules (`score.py`), then a sample is **checked by hand**.
- We report the share of passing replies per metric and per model.

## 3 · Implement: the files

| File | What it does |
|---|---|
| `prompts.json` | the test items and their categories |
| `backends.py` | one function per provider: `ask(model, prompt) -> reply` |
| `run_tests.py` | sends every prompt to every model, saves `results/responses.csv` |
| `score.py` | **the hard core**: the rules that decide pass or fail; writes `results/scores.csv` |
| `report.py` | the table per model, rule-vs-human agreement, and `results/summary.png` |

## 4 · Run

```bash
# create virtual environment
python3 -m venv .venv
# activate the virtual environment
source .venv/bin/activate
# install the requirements
pip install -r requirements.txt

# a. Check that everything works, offline, with two fake models (no key needed)
python run_tests.py --model mock:cautious --model mock:eager --repeats 3

# b. Real models (pick what you have access to; model names change, check the provider's docs)
#    Free and local:  install Ollama (https://ollama.com), then: ollama pull llama3.2
python run_tests.py --model ollama:llama3.2 --repeats 3
#    Claude:  pip install anthropic; export ANTHROPIC_API_KEY=...
python run_tests.py --model anthropic:<model-name> --repeats 3
#    GPT:     pip install openai; export OPENAI_API_KEY=...
python run_tests.py --model openai:<model-name> --repeats 3
#    Several models in one run: repeat --model
```

Each run overwrites `results/responses.csv`; put all the models you want to compare in one run.

## 5 · Score

```bash
python score.py     # applies the rules -> results/scores.csv
```

Now **check the rules**: open `results/scores.csv`, read a sample of replies (say 20, spread
over models and categories) and write 1 (good behaviour) or 0 in the `human_pass` column.

```bash
python report.py    # table, agreement with your judgement, results/summary.png
```

If the rules agree with you less than ~90% of the time, improve the phrase lists in `score.py`
and score again.

## Known limitations (good material for your discussion)

- Keyword rules are crude: a model may ask a question without our phrases, or say
  "I don't know" and then guess anyway.
- 15 prompts × 3 repeats is a small sample.
- Single-turn only: we don't test whether the model uses the answer to its clarifying question.
- Speed depends on your network and the provider's load, not just the model.
- The mock models are fake and only show that the pipeline runs. Never report their numbers.
