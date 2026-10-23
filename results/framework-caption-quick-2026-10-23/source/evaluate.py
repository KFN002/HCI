"""Run the 60-prompt framework locally: python3 evaluate.py.

Only --prepare skips inference. All replies are journaled; an identical command
resumes missing cells. A private Ollama process owns the benchmark scheduler.
"""

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import time

from backends import OllamaClient, OllamaError
import metrics
import reporting

MODELS = ["gemma3:4b", "qwen2.5:3b", "llama3.2:3b", "granite4:3b", "phi4-mini:3.8b"]
PROTECTED_MODEL = "qwen3-coder:latest"
ROOT = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_suite(path):
    suite = json.loads(Path(path).read_text(encoding="utf-8"))
    items = suite.get("items", [])
    counts = Counter(item["category"] for item in items)
    if len(items) != 60 or len(counts) != 6 or set(counts.values()) != {10}:
        raise ValueError("The framework requires six categories of ten prompts (60 total)")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Prompt IDs must be unique")
    for item in items:
        if not isinstance(item["prompt"], str) or not item["prompt"].strip():
            raise ValueError(f"{item['id']}: missing prompt")
        if "system" in item and not isinstance(item["system"], str):
            raise ValueError(f"{item['id']}: system must be text")
        metrics.validate(item)
    return suite


def quick_suite(suite):
    """Keep the first two prompts in each category for a balanced short run."""
    counts = Counter()
    items = []
    for item in suite["items"]:
        if counts[item["category"]] < 2:
            items.append(item)
            counts[item["category"]] += 1
    return {**suite, "items": items,
            "design": {**suite.get("design", {}), "quick": True,
                       "item_count": len(items), "prompts_per_category": 2}}


def protected_state():
    try:
        return OllamaClient("http://127.0.0.1:11434", timeout=3)._json("/api/ps")
    except OllamaError as exc:
        return {"unavailable": str(exc)}


@contextmanager
def local_server(output, port):
    """Stop only the exact server we create; primary Ollama is read-only."""
    if port == 11434 or not 1024 <= port <= 65535:
        raise ValueError("Use a separate unprivileged port (default 11435)")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise ValueError(f"Port {port} is occupied; choose --port with an unused port")
    if not shutil.which("ollama"):
        raise ValueError("Ollama must be installed; this runner never downloads models")
    environment = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}",
                   "OLLAMA_MODELS": os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models")),
                   "OLLAMA_NOPRUNE": "1", "OLLAMA_NO_CLOUD": "1",
                   "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_KEEP_ALIVE": "2m"}
    audit = {"protected_before": protected_state(), "endpoint": f"http://127.0.0.1:{port}"}
    server = None
    try:
        with (output / "server.log").open("a", encoding="utf-8") as log:
            server = subprocess.Popen(["ollama", "serve"], env=environment, stdout=log, stderr=subprocess.STDOUT)
            audit["owned_server_pid"] = server.pid
            client = OllamaClient(audit["endpoint"], timeout=180)
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Temporary Ollama exited; inspect server.log")
                try:
                    OllamaClient(audit["endpoint"], timeout=2)._json("/api/version")
                    break
                except OllamaError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Temporary Ollama did not become ready")
                    time.sleep(.25)
            yield client
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        audit.update(temporary_server_stopped=server is None or server.poll() is not None,
                     protected_after=protected_state())
        save_json(output / "coexistence.json", audit)


def additional_models(inventory):
    """Select each remaining eligible digest once, preserving inventory order."""
    excluded = {model.get("digest") for model in inventory["models"]
                if model["name"] in [*MODELS, PROTECTED_MODEL] and model.get("digest")}
    selected = []
    for model in inventory["models"]:
        name, model_digest = model["name"], model.get("digest")
        if (name in [*MODELS, PROTECTED_MODEL] or not model.get("eligible")
                or not model_digest or model_digest in excluded):
            continue
        selected.append(name)
        excluded.add(model_digest)
    if not selected:
        raise ValueError("No additional eligible model digests are installed")
    return selected


def unique_models(inventory, names):
    """Validate requested tags and retain one name per eligible weight digest."""
    if not names:
        raise ValueError("Select at least one model")
    available = {model["name"]: model for model in inventory["models"]}
    protected_digest = available.get(PROTECTED_MODEL, {}).get("digest")
    selected, seen = [], set()
    for name in names:
        model = available.get(name, {})
        if not model.get("eligible") or not model.get("digest"):
            raise ValueError(f"Required model unavailable: {name}")
        if name == PROTECTED_MODEL or (protected_digest and model["digest"] == protected_digest):
            raise ValueError(f"Protected model or alias excluded: {name}")
        if model["digest"] not in seen:
            selected.append(name)
            seen.add(model["digest"])
    return selected


def prepare(output, suite, inventory, repeats, models=None):
    models = unique_models(inventory, MODELS if models is None else models)
    available = {model["name"]: model for model in inventory["models"]}
    config = {"repeats": repeats, "temperature": .5, "num_predict": 512,
              "num_ctx": 4096, "seed": 20261021, "condition": "baseline",
              "quick": bool(suite.get("design", {}).get("quick")), "prompt_count": len(suite["items"]),
              "suite_sha256": digest(suite), "scorer_version": metrics.SCORER_VERSION,
              "ollama_version": inventory.get("version"),
              "models": [{"name": name, "digest": available[name]["digest"]} for name in models],
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("evaluate.py", "metrics.py", "backends.py")}}
    rng = random.Random(config["seed"])
    model_order = list(config["models"])
    rng.shuffle(model_order)
    plan = []
    for model in model_order:
        block = []
        for item in suite["items"]:
            for repeat in range(1, repeats + 1):
                block.append({"model": model["name"], "model_digest": model["digest"],
                              "item_id": item["id"], "category": item["category"], "repeat": repeat,
                              "seed": int(digest([config["seed"], item["id"], repeat])[:8], 16) % 2147483647})
        rng.shuffle(block)
        plan.extend(block)
    for job in plan:
        job["row_id"] = digest([config, job])[:24]
    path = output / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["config"] != config:
            raise ValueError("Prompts, settings, models or source changed; use a new --output directory")
        if digest(json.loads((output / "prompts.snapshot.json").read_text())) != config["suite_sha256"]:
            raise ValueError("Frozen prompt snapshot changed")
        if json.loads((output / "plan.json").read_text()) != plan:
            raise ValueError("Frozen run plan changed")
    else:
        if (output / "responses.jsonl").exists() and (output / "responses.jsonl").stat().st_size:
            raise ValueError("Raw responses exist without their original manifest")
        manifest = {"status": "prepared", "planned_jobs": len(plan), "completed_jobs": 0,
                    "created_at": datetime.now(timezone.utc).isoformat(), "config": config}
        save_json(path, manifest)
        save_json(output / "prompts.snapshot.json", suite)
        save_json(output / "plan.json", plan)
        save_json(output / "inventory.json", inventory)
        source = output / "source"
        source.mkdir(exist_ok=True)
        for name in ("evaluate.py", "metrics.py", "reporting.py", "backends.py"):
            shutil.copy2(ROOT / name, source / name)
    return manifest, plan


def collect(client, output, suite, manifest, plan):
    path = output / "responses.jsonl"
    rows = read_rows(path)
    expected = {job["row_id"]: job for job in plan}
    done = set()
    for row in rows:
        if row["row_id"] in done or row["row_id"] not in expected:
            raise ValueError("Duplicate or out-of-plan response ID")
        if any(row.get(key) != value for key, value in expected[row["row_id"]].items()):
            raise ValueError("Response metadata differs from frozen plan")
        done.add(row["row_id"])
    items = {item["id"]: item for item in suite["items"]}
    manifest["status"] = "running"
    save_json(output / "manifest.json", manifest)
    try:
        with path.open("a", encoding="utf-8") as journal:
            if path.stat().st_size:
                with path.open("rb") as binary:
                    binary.seek(-1, os.SEEK_END)
                    if binary.read(1) != b"\n":
                        journal.write("\n")
            for job in plan:
                if job["row_id"] in done:
                    continue
                item = items[job["item_id"]]
                messages = ([{"role": "system", "content": item["system"]}] if item.get("system") else [])
                messages.append({"role": "user", "content": item["prompt"]})
                row = {**job, "prompt": item["prompt"], "started_at": datetime.now(timezone.utc).isoformat(),
                       "reply": "", "error": ""}
                try:
                    row.update(client.chat(job["model"], messages, keep_alive="2m", think=None,
                                           options={"seed": job["seed"], "temperature": .5,
                                                    "num_predict": 512, "num_ctx": 4096}))
                except (OllamaError, ValueError, OSError) as exc:
                    row["error"] = f"{type(exc).__name__}: {exc}"
                row["truncated"] = row.get("done_reason") in {"length", "max_tokens", "max_length"}
                journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                rows.append(row)
                manifest["completed_jobs"] = len(rows)
                save_json(output / "manifest.json", manifest)
                print(f"[{len(rows)}/{len(plan)}] {job['model']} {job['item_id']} #{job['repeat']}: "
                      f"{row['error'] or str(round(row.get('seconds', 0), 2)) + 's'}", flush=True)
    finally:
        manifest.update(status="complete" if len(rows) == len(plan) else "partial", completed_jobs=len(rows))
        save_json(output / "manifest.json", manifest)
    return rows


def finish(output, suite, manifest, rows):
    items = {item["id"]: item for item in suite["items"]}
    scored = [{**row, **metrics.score(items[row["item_id"]], row.get("reply", ""),
                                     error=row.get("error", ""), truncated=bool(row.get("truncated")))} for row in rows]
    path = output / "scores.jsonl"
    temporary = path.with_suffix(".jsonl.tmp")
    temporary.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in scored), encoding="utf-8")
    temporary.replace(path)
    reporting.generate(output, scored, manifest, suite)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="Freeze a plan without inference")
    parser.add_argument("--quick", action="store_true", help="Use the first two prompts per category (12 prompts)")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--additional", action="store_true", help="Run remaining eligible model digests, excluding the default five and protected model")
    selection.add_argument("--model", action="append", help="Select an installed tag; repeat for multiple models (aliases deduplicated)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompts", type=Path, default=ROOT / "prompts.framework.json")
    parser.add_argument("--repeats", type=int, choices=[3, 4, 5], default=3)
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args(argv)
    if args.output is None:
        selection_name = "additional" if args.additional else "selected" if args.model else "five"
        args.output = ROOT / f"results/framework-{selection_name}{'-quick' if args.quick else ''}-2026-10-23"
    try:
        suite = load_suite(args.prompts)
        if args.quick:
            suite = quick_suite(suite)
        args.output.mkdir(parents=True, exist_ok=True)
        with local_server(args.output, args.port) as client:
            inventory = client.inventory()
            models = additional_models(inventory) if args.additional else args.model
            manifest, plan = prepare(args.output, suite, inventory, args.repeats, models=models)
            print(f"{len(manifest['config']['models'])} models × {len(suite['items'])} prompts × {args.repeats} repeats = {len(plan)} conversations", flush=True)
            try:
                rows = read_rows(args.output / "responses.jsonl") if args.prepare else collect(client, args.output, suite, manifest, plan)
            except KeyboardInterrupt:
                rows = read_rows(args.output / "responses.jsonl")
                finish(args.output, suite, manifest, rows)
                print("Interrupted. Saved replies retained; repeat the command to resume.", file=sys.stderr)
                return 130
            finish(args.output, suite, manifest, rows)
        print(f"{manifest['status'].upper()}: {args.output / 'report.html'}", flush=True)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
