"""Shared study definitions. Nothing here contacts Ollama or exposes scoring rubrics."""

import csv
import hashlib
import json
import os
from pathlib import Path

# These are the experimental interventions. Keep their exact wording frozen
# within a run: editing a wrapper changes the treatment, not just presentation.
CONDITIONS = {
    "baseline": "",
    "structured": (
        "Follow the user's requested output format and constraints. Distinguish facts "
        "from assumptions. If essential information is missing, ask a focused question. "
        "Keep the human in control of consequential actions. Be concise."
    ),
    "self_check": (
        "Before giving your final response, privately check that it answers the request, "
        "follows all stated constraints, and does not invent facts or completed actions. "
        "Correct any mistakes you notice. Return only your final response in the format "
        "the user requested; do not describe your checking process."
    ),
}

RESPONSE_FIELDS = [
    "row_id", "model", "model_digest", "item_id", "category", "condition", "repeat",
    "seed", "started_at", "prompt", "reply", "thinking", "seconds", "ttft_seconds",
    "final_turn_ttft_seconds", "first_event_seconds", "total_duration_seconds",
    "load_seconds", "prompt_eval_seconds", "eval_seconds", "prompt_tokens",
    "completion_tokens", "tokens_per_second", "done_reason", "truncated",
    "completed_turns", "expected_turns", "research_role", "think_override", "error", "turns",
]


def canonical_hash(value):
    """Hash JSON independently of indentation/key ordering."""
    # This identifies content, not a filename or its whitespace. A snapshot
    # cannot be quietly edited while keeping the appearance of the same study.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def user_turns(item):
    """User turns; the runner inserts actual assistant replies between them."""
    turns = item.get("messages")
    if turns is not None:
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{item['id']}: messages must be a nonempty list")
        return turns
    return [{"role": "user", "content": item["prompt"]}]


def load_suite(path):
    """Reject malformed/duplicate cases before expensive inference starts."""
    with open(path, encoding="utf-8") as handle:
        suite = json.load(handle)
    if not isinstance(suite.get("items"), list) or not suite["items"]:
        raise ValueError("Prompt suite needs a nonempty items list")
    seen = set()
    for item in suite["items"]:
        for key in ("id", "category", "prompt", "scoring"):
            if key not in item:
                raise ValueError(f"Prompt missing {key}: {item.get('id', '?')}")
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in seen:
            raise ValueError(f"Invalid or duplicate item id: {item['id']}")
        seen.add(item["id"])
        if not isinstance(item["prompt"], str) or not item["prompt"].strip():
            raise ValueError(f"{item['id']}: empty prompt")
        if not isinstance(item["scoring"], dict) or not item["scoring"].get("type"):
            raise ValueError(f"{item['id']}: scoring.type required")
        for message in user_turns(item):
            if message.get("role") != "user" or not isinstance(message.get("content"), str):
                raise ValueError(f"{item['id']}: messages must contain user turns only")
            if not message["content"].strip():
                raise ValueError(f"{item['id']}: empty user turn")
    return suite


def initial_messages(item, condition):
    """Compose a strategy with case-specific system instructions, never scoring rules."""
    # Only task-specific system text is sent. Expected answers, scoring regexes,
    # missing-slot annotations and research-role labels remain evaluation data.
    parts = [part for part in (CONDITIONS[condition], item.get("system", "")) if part]
    return [{"role": "system", "content": "\n\n".join(parts)}] if parts else []


def write_json(path, value):
    """Atomic metadata replacement prevents half-written manifests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    os.replace(temporary, path)


def write_csv(path, rows, fields):
    """Explicit headers even before a run; preserve Unicode and multiline responses."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                             if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})
    os.replace(temporary, path)


def read_jsonl(path):
    """Fail loudly on damaged rows or duplicate IDs rather than silently lose data."""
    path = Path(path)
    if not path.exists():
        return []
    rows, seen = [], set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: damaged JSONL; preserve and repair "
                                 "the incomplete line before resuming") from exc
            if not isinstance(row, dict) or not isinstance(row.get("row_id"), str):
                raise ValueError(f"{path}:{number}: record must have a string row_id")
            # Duplicate IDs would inflate sample size and corrupt paired tests.
            # Refuse them rather than counting the same conversation twice.
            if row["row_id"] in seen or not row["row_id"]:
                raise ValueError(f"{path}:{number}: missing/duplicate row_id")
            seen.add(row["row_id"])
            rows.append(row)
    return rows
