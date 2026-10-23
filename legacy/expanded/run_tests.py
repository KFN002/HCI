"""Prepare by default. Only --execute sends inference requests to local Ollama.

Each complete conversation is journaled and fsynced before proceeding. Repeating
an identical command resumes missing jobs; a recorded error is never auto-retried.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import shutil
import subprocess
import sys
import time
import score as scoring_module

from backends import OllamaClient
from benchmark import (CONDITIONS, RESPONSE_FIELDS, canonical_hash, initial_messages,
                       load_suite, read_jsonl, user_turns, write_csv, write_json)
from score import validate_scoring


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def environment():
    """Capture context without expensive monitoring or personal identifiers."""
    info = {"platform": platform.platform(), "machine": platform.machine(),
            "python": platform.python_version(), "cpu_count": os.cpu_count()}
    if sys.platform == "darwin":
        for label, command in (("memory_bytes", ["sysctl", "-n", "hw.memsize"]),
                               ("cpu", ["sysctl", "-n", "machdep.cpu.brand_string"]),
                               ("power_state", ["pmset", "-g", "batt"])):
            try:
                info[label] = subprocess.check_output(command, text=True, timeout=2,
                                                      stderr=subprocess.DEVNULL).strip()
            except (OSError, subprocess.SubprocessError):
                info[label] = "unavailable"
    return info


def select_models(inventory, requested=None, include_aliases=False, excluded=None):
    """Cover eligible digests while honoring protected models and their aliases.

    Names alone are not enough: a second local tag can point to Cline's exact
    weights. Exclusion therefore checks both the requested tag and its digest.
    No exclusion calls an unload endpoint or changes the other Ollama service.
    """
    available = {model["name"]: model for model in inventory["models"]}
    excluded = set(excluded or ["qwen3-coder:latest"])
    protected_digests = {available[name].get("digest") for name in excluded
                         if name in available and available[name].get("digest")}
    def protected(model):
        return model["name"] in excluded or model.get("digest") in protected_digests
    if requested:
        names = list(dict.fromkeys(name.removeprefix("ollama:") for name in requested))
        missing = set(names) - set(available)
        if missing:
            raise ValueError(f"Models not installed locally: {sorted(missing)}")
        for name in names:
            if protected(available[name]):
                raise ValueError(f"{name}: protected/excluded model; no inference allowed")
            if not available[name].get("eligible"):
                raise ValueError(f"{name}: {available[name].get('skip_reason')}")
        candidates = [available[name] for name in names]
    else:
        candidates = [model for model in available.values()
                      if model.get("eligible") and not protected(model)]
    selected, by_digest = [], {}
    for model in sorted(candidates, key=lambda value: value["name"]):
        digest = model.get("digest") or model["name"]
        if not include_aliases and digest in by_digest:
            by_digest[digest]["aliases"].append(model["name"])
        else:
            copied = {**model, "aliases": []}
            selected.append(copied)
            by_digest[digest] = copied
    if not selected:
        raise ValueError("No eligible local text-generation models; inspect inventory skip reasons")
    return selected


def generation_hash():
    """Reject resuming after edits to messages, backend or job generation."""
    root = Path(__file__).resolve().parent
    return canonical_hash({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                           for name in ("benchmark.py", "backends.py", "run_tests.py")})


def make_plan(suite, models, config):
    """Seeded shuffle in model blocks limits reloads; item/repeat seeds are paired."""
    rng = random.Random(config["seed"])
    order = list(models)
    rng.shuffle(order)
    jobs = []
    for model in order:
        block = []
        for item in suite["items"]:
            for repeat in range(1, config["repeats"] + 1):
                # The condition/model are deliberately absent from the seed.
                # This permits matched comparisons across the two study stages.
                seed = int(canonical_hash([config["seed"], item["id"], repeat])[:8], 16) % 2147483647
                for condition in config["conditions"]:
                    identity = [config, model["name"], item["id"], condition, repeat]
                    block.append({"row_id": canonical_hash(identity)[:24],
                                  "model": model["name"], "model_digest": model.get("digest", ""),
                                  "item_id": item["id"], "category": item["category"],
                                  "condition": condition, "repeat": repeat, "seed": seed,
                                  "expected_turns": len(user_turns(item))})
        rng.shuffle(block)
        jobs.extend(block)
    return [{"order": index, **job} for index, job in enumerate(jobs, 1)]


def config_from_args(args, suite, models):
    # An explicit off policy is applied only to models advertising thinking.
    # Sending unsupported reasoning options to every model can create API errors.
    for model in models:
        metadata = model.get("model_info", {})
        controls = model.get("thinking", {}).get("values")
        thinking_only = ((isinstance(controls, list) and not any(value is False for value in controls))
                         or (metadata.get("general.finetune") == "Thinking"
                             and metadata.get("general.version") == "2507"))
        if args.think == "off" and "thinking" in model.get("capabilities", []) and thinking_only:
            raise ValueError(f"{model['name']}: installed weights support thinking only; use a separate reasoning-budget study")
    thinking = {model["name"]: (None if args.think == "default" or
                                "thinking" not in model.get("capabilities", [])
                                else args.think == "on") for model in models}
    if args.think == "on" and any("thinking" not in model.get("capabilities", []) for model in models):
        raise ValueError("--think on requires every selected model to support thinking")
    return {"repeats": args.repeats, "conditions": args.conditions, "seed": args.seed,
            "temperature": args.temperature, "num_predict": args.num_predict,
            "num_ctx": args.num_ctx, "timeout": args.timeout, "keep_alive": args.keep_alive,
            "think": None if args.think == "default" else args.think == "on",
            "thinking_by_model": thinking,
            "suite_sha256": canonical_hash(suite), "generation_source_sha256": generation_hash(),
            "models": [{"name": model["name"], "digest": model.get("digest", "")}
                       for model in models]}


def prepare(args, client):
    """Freeze suite, config, digests and schedule without loading any model."""
    run_dir = Path(args.run_dir)
    suite = load_suite(args.prompts)
    for item in suite["items"]:
        validate_scoring(item)
    if args.categories:
        known = {item["category"] for item in suite["items"]}
        if set(args.categories) - known:
            raise ValueError(f"Unknown categories: {set(args.categories) - known}")
        suite = {**suite, "items": [item for item in suite["items"]
                                   if item["category"] in args.categories]}
    if args.inventory_file:
        inventory = json.loads(Path(args.inventory_file).read_text(encoding="utf-8"))
    else:
        inventory = client.inventory()
    models = select_models(inventory, args.model, args.include_aliases,
                           getattr(args, "exclude_model", None))
    config = config_from_args(args, suite, models)
    jobs = make_plan(suite, models, config)
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["config"] != config:
            raise ValueError("Config, generation code, prompts or model digests changed. Choose a new "
                             "RUN_DIR to preserve results; reuse the original options to resume.")
        snapshot = load_suite(run_dir / "prompts.snapshot.json")
        if canonical_hash(snapshot) != manifest["suite_sha256"]:
            raise ValueError("Frozen prompt snapshot changed; restore the original snapshot before resuming")
        saved_conditions = json.loads((run_dir / "conditions.json").read_text(encoding="utf-8"))
        if saved_conditions != CONDITIONS:
            raise ValueError("Frozen prompt strategies changed; restore conditions.json before resuming")
        with (run_dir / "plan.csv").open(newline="", encoding="utf-8") as handle:
            saved_jobs = list(csv.DictReader(handle))
        if saved_jobs != [{key: str(value) for key, value in job.items()} for job in jobs]:
            raise ValueError("Frozen job schedule changed; restore plan.csv before resuming")
        if inventory.get("version") != manifest.get("ollama_version"):
            raise ValueError("Ollama version changed; use a new run directory for comparable results")
        current_scoring_hash = hashlib.sha256(Path(scoring_module.__file__).read_bytes()).hexdigest()
        if manifest.get("scoring_source_sha256") != current_scoring_hash:
            raise ValueError("Scoring code changed; preserve this run and choose a new directory")
    else:
        if (run_dir / "responses.jsonl").exists() and (run_dir / "responses.jsonl").stat().st_size:
            raise ValueError("Refusing response data without its original manifest")
        manifest = {"run_id": canonical_hash(config)[:16], "status": "prepared",
                    "created_at": utc_now(), "config": config, "models": models,
                    "planned_jobs": len(jobs),
                    "planned_requests": sum(job["expected_turns"] for job in jobs),
                    "completed_jobs": 0, "suite_sha256": canonical_hash(suite),
                    "environment": environment(), "ollama_version": inventory.get("version"),
                    "excluded_models": getattr(args, "exclude_model", ["qwen3-coder:latest"]),
                    "scorer_version": getattr(scoring_module, "SCORER_VERSION", "1.0"),
                    "scoring_source_sha256": hashlib.sha256(Path(scoring_module.__file__).read_bytes()).hexdigest(),
                    "order": "seeded model blocks; seeded shuffle within block; paired item/repeat seeds",
                    "timing": "cold/warm as observed; no inference warm-up; see thinking_by_model for actual policy"}
        write_json(manifest_path, manifest)
        write_json(run_dir / "inventory.json", inventory)
        write_json(run_dir / "prompts.snapshot.json", suite)
        write_json(run_dir / "conditions.json", CONDITIONS)
        # Save the actual executable sources, not just their hashes. This lets
        # another group inspect/reproduce the original instrument after edits.
        source_dir = run_dir / "source"
        source_dir.mkdir(exist_ok=True)
        for filename in ("benchmark.py", "backends.py", "run_tests.py", "score.py"):
            shutil.copy2(Path(__file__).resolve().parent / filename, source_dir / filename)
        write_csv(run_dir / "plan.csv", jobs, ["order", "row_id", "model", "model_digest", "item_id",
                                             "category", "condition", "repeat", "seed", "expected_turns"])
        (run_dir / "responses.jsonl").touch()
        write_csv(run_dir / "responses.csv", [], RESPONSE_FIELDS)
    return manifest, suite, jobs


def conversation(client, job, item, config):
    """Run an isolated conversation, retaining actual intermediate replies/failures."""
    messages = initial_messages(item, job["condition"])
    turns, error = [], ""
    start = time.perf_counter()
    think_override = config.get("thinking_by_model", {}).get(job["model"], config["think"])
    for index, user_message in enumerate(user_turns(item)):
        messages.append(dict(user_message))
        try:
            result = client.chat(job["model"], messages,
                                 options={"seed": (job["seed"] + index) % 2147483647,
                                          "temperature": config["temperature"],
                                          "num_predict": config["num_predict"], "num_ctx": config["num_ctx"]},
                                 keep_alive=config["keep_alive"], think=think_override)
            # Keep intermediate replies as evidence. A fluent final answer does
            # not erase an earlier failure, invented action, or truncated turn.
            turns.append({"user": user_message["content"], **result})
            if not result["reply"].strip():
                raise ValueError("No visible answer (possibly token budget consumed by thinking)")
            assistant = {"role": "assistant", "content": result["reply"]}
            if result.get("thinking"):
                assistant["thinking"] = result["thinking"]
            messages.append(assistant)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            break
    last = turns[-1] if turns else {}
    row = {key: value for key, value in job.items() if key != "order"}
    row.update({"prompt": item["prompt"], "reply": last.get("reply", ""),
                "research_role": item.get("research_role", "other"),
                "think_override": think_override,
                "thinking": last.get("thinking", ""), "seconds": time.perf_counter() - start,
                "ttft_seconds": turns[0].get("ttft_seconds") if turns else None,
                "final_turn_ttft_seconds": last.get("ttft_seconds"),
                "first_event_seconds": turns[0].get("first_event_seconds") if turns else None,
                "done_reason": last.get("done_reason", ""),
                "truncated": int(any(turn.get("done_reason") in ("length", "max_tokens") for turn in turns)),
                "completed_turns": len(turns), "error": error, "turns": turns})
    for key in ("total_duration_seconds", "load_seconds", "prompt_eval_seconds", "eval_seconds",
                "prompt_tokens", "completion_tokens"):
        values = [turn.get(key) for turn in turns]
        # Missing telemetry is not zero. Summing only known turns would report
        # an apparently fast conversation while hiding an unmeasured request.
        row[key] = sum(values) if values and all(value is not None for value in values) else None
    row["tokens_per_second"] = (row["completion_tokens"] / row["eval_seconds"]
                                 if row["completion_tokens"] is not None and row["eval_seconds"] else None)
    return row


def execute(args, client, manifest, suite, jobs):
    """Sequential append-only execution; errors stay outcomes, never hidden retries."""
    run_dir = Path(args.run_dir)
    rows = read_jsonl(run_dir / "responses.jsonl")
    expected = {job["row_id"] for job in jobs}
    if any(row["row_id"] not in expected for row in rows):
        raise ValueError("Response file contains records outside the frozen plan")
    done = {row["row_id"] for row in rows}
    items = {item["id"]: item for item in suite["items"]}
    manifest.update(status="running", last_started_at=utc_now(), environment_at_execution=environment())
    manifest.setdefault("execution_sessions", []).append({"started_at": manifest["last_started_at"],
                                                          "environment": manifest["environment_at_execution"]})
    write_json(run_dir / "manifest.json", manifest)
    interrupted, new_count = False, 0
    try:
        with (run_dir / "responses.jsonl").open("a", encoding="utf-8") as handle:
            # A complete last JSON object can survive a hard stop without its newline.
            # Add only the separator so appending cannot merge two valid records.
            journal = run_dir / "responses.jsonl"
            if journal.stat().st_size:
                with journal.open("rb") as binary:
                    binary.seek(-1, os.SEEK_END)
                    needs_separator = binary.read(1) != b"\n"
                if needs_separator:
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            for job in jobs:
                if job["row_id"] in done:
                    continue
                if args.max_jobs is not None and new_count >= args.max_jobs:
                    break
                started_at = utc_now()
                row = conversation(client, job, items[job["item_id"]], manifest["config"])
                row["started_at"] = started_at
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                rows.append(row)
                done.add(row["row_id"])
                new_count += 1
                manifest["completed_jobs"] = len(rows)
                write_json(run_dir / "manifest.json", manifest)
                outcome = row["error"] or f"{row['seconds']:.2f}s"
                print(f"[{len(rows)}/{len(jobs)}] {job['model']} {job['item_id']} "
                      f"{job['condition']} #{job['repeat']}: {outcome}", flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\nInterrupted; committed conversations are safe. Repeat the same command to resume.")
    finally:
        manifest.update(status="complete" if len(rows) == len(jobs) else "interrupted" if interrupted else "partial",
                        completed_jobs=len(rows), updated_at=utc_now())
        write_json(run_dir / "manifest.json", manifest)
        write_csv(run_dir / "responses.csv", rows, RESPONSE_FIELDS)
    return 130 if interrupted else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicitly allow model inference")
    parser.add_argument("--all-local", action="store_true", help="All eligible local models (default)")
    parser.add_argument("--model", action="append", help="Installed tag, optionally prefixed ollama:")
    parser.add_argument("--include-aliases", action="store_true", help="Also evaluate duplicate digest aliases")
    parser.add_argument("--exclude-model", action="append", default=["qwen3-coder:latest"],
                        help="Exclude a tag and every same-digest alias; Cline's coding model is always excluded")
    parser.add_argument("--prompts", default="prompts.json")
    parser.add_argument("--run-dir", default="results/prepared")
    parser.add_argument("--inventory-file", help="Saved inventory for offline preparation only")
    parser.add_argument("--categories", nargs="+")
    parser.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--temperature", type=float, default=0.5)
    parser.add_argument("--num-predict", type=int, default=512)
    parser.add_argument("--num-ctx", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--keep-alive", default="2m")
    parser.add_argument("--think", choices=["default", "on", "off"], default="default")
    parser.add_argument("--max-jobs", type=int, help="At most N new conversations; keep full plan")
    args = parser.parse_args(argv)
    if args.repeats < 1 or args.num_predict < 1 or args.num_ctx < 1 or args.timeout <= 0:
        parser.error("repeats, token/context limits and timeout must be positive")
    if not 0 <= args.temperature <= 2 or len(set(args.conditions)) != len(args.conditions):
        parser.error("temperature must be 0..2 and conditions must be unique")
    if args.max_jobs is not None and args.max_jobs < 1:
        parser.error("max-jobs must be positive")
    if args.execute and args.inventory_file:
        parser.error("Execution must verify live digests; omit --inventory-file")
    if args.all_local and args.model:
        parser.error("Choose --all-local or explicit --model selections")
    try:
        client = OllamaClient(timeout=args.timeout)
        manifest, suite, jobs = prepare(args, client)
        print(f"{len(manifest['models'])} unique selected models, {len(suite['items'])} cases, "
              f"{len(jobs)} conversations, {manifest['planned_requests']} model requests.")
        if not args.execute:
            print(f"PREPARED ONLY: {args.run_dir}; no model inference requested.")
            return 0
        return execute(args, client, manifest, suite, jobs)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
