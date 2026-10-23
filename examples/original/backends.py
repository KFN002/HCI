"""How we talk to each model. One function per provider, all with the same shape:

    ask(model_name, prompt) -> reply text

Pick a backend on the command line, e.g.  --model mock:cautious  or  --model ollama:llama3.2
Only the backend you use needs its package or API key.
"""

import os

import requests


def ask_mock(model, prompt):
    """Offline stand-in so the whole pipeline runs without any API key.
    'cautious' asks and admits; 'eager' always guesses. NOT a real model."""
    p = prompt.lower()
    if model == "cautious":
        if "capital of germany" in p:
            return "Berlin."
        if "two hours" in p:
            return "120"
        if "7 times 8" in p:
            return "56"
        if "closest to the sun" in p:
            return "Mercury."
        if "water" in p:
            return "H2O"
        if any(w in p for w in ["breakfast", "exchange rate", "room number", "thinking of", "neighbour"]):
            return "I don't know that. I don't have access to that information."
        return "Could you clarify what you mean? Which one are you referring to?"
    # eager: answers everything confidently
    if "capital of germany" in p:
        return "Berlin."
    if "7 times 8" in p:
        return "56"
    return "Sure! Done. Here you go."


def ask_ollama(model, prompt):
    """A free local model via Ollama (https://ollama.com). Run `ollama pull <model>` first."""
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434") + "/api/chat"
    r = requests.post(url, json={
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }, timeout=300)
    r.raise_for_status()
    return r.json()["message"]["content"]


def ask_anthropic(model, prompt):
    """Claude via the Anthropic API. Needs `pip install anthropic` and ANTHROPIC_API_KEY."""
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def ask_openai(model, prompt):
    """GPT models via the OpenAI API. Needs `pip install openai` and OPENAI_API_KEY."""
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content


BACKENDS = {
    "mock": ask_mock,
    "ollama": ask_ollama,
    "anthropic": ask_anthropic,
    "openai": ask_openai,
}


def ask(spec, prompt):
    """spec is 'backend:model', e.g. 'ollama:llama3.2' or 'anthropic:claude-sonnet-5-5'."""
    backend, _, model = spec.partition(":")
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend '{backend}'. Choose from: {', '.join(BACKENDS)}")
    return BACKENDS[backend](model, prompt)
