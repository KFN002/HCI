"""Reproduce the expanded study on an isolated local Ollama server.

Default behavior prepares plans only. --execute explicitly authorizes inference.
The primary service is queried only with GET /api/ps; this program never sends
it a model request, keep-alive override, unload call, or configuration change.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

from backends import OllamaClient, OllamaError
from benchmark import write_json
from run_tests import utc_now

# qwen3:4b on this machine is the Thinking-2507 variant, not the original
# switchable Qwen3. A separate diagnostic pilot established that it needs a
# different reasoning budget. Keep it outside this fixed answer-budget cohort.
MODELS = ["gemma3:4b", "qwen2.5:3b", "llama3.2:3b", "granite4:3b", "phi4-mini:3.8b"]


def protected_state():
    """A read-only service snapshot is evidence of coexistence, not a keep-alive."""
    try:
        return OllamaClient("http://127.0.0.1:11434", timeout=3)._json("/api/ps")
    except OllamaError as exc:
        return {"unavailable": str(exc)}


def run_command(command, environment):
    """Run one stage; forward an interruption so the child can commit its journal."""
    print("Stage:", " ".join(map(str, command)), flush=True)
    child = subprocess.Popen(command, env=environment)
    try:
        code = child.wait()
    except KeyboardInterrupt:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=10)
        raise
    if code:
        raise RuntimeError(f"Study stage failed with exit code {code}; completed records were retained")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--run-prefix", default="results/expanded-five-2026-10-21")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--port", type=int, default=11435)
    parser.add_argument("--num-predict", type=int, default=512)
    args = parser.parse_args(argv)
    if args.port == 11434 or not 1024 <= args.port <= 65535 or args.repeats < 2:
        parser.error("Use a separate unprivileged port and at least two repetitions")
    if not shutil.which("ollama"):
        parser.error("Ollama is not installed; no models are downloaded automatically")
    # Never attach to, restart, or terminate an existing server on either port.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            parser.error("Benchmark port is already occupied; choose another unused port")
    prefix = Path(args.run_prefix)
    prefix.mkdir(parents=True, exist_ok=True)
    audit = {"started_at": utc_now(), "execute": args.execute, "models": MODELS,
             "protected_model": "qwen3-coder:latest", "protected_before": protected_state(),
             "benchmark_endpoint": f"http://127.0.0.1:{args.port}",
             "selection": "Five general-purpose 3–4B models, comparable answer budget and memory headroom for Cline. The installed qwen3:4b is thinking-only and excluded after a documented diagnostic pilot; not a universal best-model claim.",
             "events": []}
    environment = {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{args.port}",
                   "OLLAMA_URL": f"http://127.0.0.1:{args.port}",
                   "OLLAMA_MODELS": os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models")),
                   "OLLAMA_NOPRUNE": "1", "OLLAMA_NO_CLOUD": "1",
                   "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_NUM_PARALLEL": "1",
                   "OLLAMA_KEEP_ALIVE": "2m", "PYTHONUNBUFFERED": "1"}
    write_json(prefix / "coexistence.json", audit)
    server = None
    try:
        with (prefix / "server.log").open("a", encoding="utf-8") as server_log:
            # Popen gives us the exact process handle. Cleanup terminates this
            # process only; no pkill or global Ollama stop command is used.
            server = subprocess.Popen(["ollama", "serve"], env=environment,
                                      stdout=server_log, stderr=subprocess.STDOUT)
            audit["temporary_server_pid"] = server.pid
            client = OllamaClient(environment["OLLAMA_URL"], timeout=2)
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Temporary Ollama exited; inspect server.log")
                try:
                    client._json("/api/version")
                    break
                except OllamaError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Temporary Ollama did not become ready")
                    time.sleep(0.25)
            inventory = client.inventory()
            installed = {model["name"]: model for model in inventory["models"]}
            missing = [name for name in MODELS if name not in installed or not installed[name]["eligible"]]
            if missing:
                raise RuntimeError(f"Selected installed models are unavailable: {missing}")
            write_json(prefix / "model_selection.json", {"inventory": inventory, "selected": MODELS,
                       "excluded": [{"name": model["name"], "reason": "Protected coding model, larger weights, specialist tuning, or duplicate alias outside matched small-model cohort."}
                                    for model in inventory["models"] if model["name"] not in MODELS]})
            shared = ["--repeats", str(args.repeats), "--num-predict", str(args.num_predict), "--think", "off"]
            for model in MODELS:
                shared.extend(["--model", model])
            for suffix, prompts, conditions in (("-baseline", "prompts.json", ["baseline"]),
                                                ("-strategies", "prompts.study.json", ["structured", "self_check"])):
                run_dir = str(prefix) + suffix
                command = [sys.executable, "run_tests.py", "--run-dir", run_dir,
                           "--prompts", prompts, "--conditions", *conditions, *shared]
                if args.execute:
                    command.append("--execute")
                run_command(command, environment)
                run_command([sys.executable, "score.py", "--run-dir", run_dir], environment)
                run_command([sys.executable, "report.py", "--run-dir", run_dir, "--prepare"], environment)
                audit["events"].append({"at": utc_now(), "stage": suffix, "protected": protected_state()})
                write_json(prefix / "coexistence.json", audit)
            if args.execute:
                run_command([sys.executable, "combine_runs.py", "--source", str(prefix) + "-baseline",
                             "--source", str(prefix) + "-strategies", "--out", str(prefix)], environment)
                run_command([sys.executable, "score.py", "--run-dir", str(prefix)], environment)
                run_command([sys.executable, "report.py", "--run-dir", str(prefix)], environment)
            audit["status"] = "complete" if args.execute else "prepared"
    except (RuntimeError, OSError, KeyboardInterrupt) as exc:
        audit.update(status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
        print(f"Study stopped: {exc}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        audit.update(finished_at=utc_now(), temporary_server_stopped=server is None or server.poll() is not None,
                     protected_after=protected_state())
        write_json(prefix / "coexistence.json", audit)
    print(f"Study {audit['status']}: {prefix}. Temporary server stopped; Cline service unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
