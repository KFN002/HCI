"""Step 4 - RUN: send every test prompt to every model, several times, and save the raw replies.

Example:
    python run_tests.py --model mock:cautious --model mock:eager --repeats 3
    python run_tests.py --model ollama:llama3.2 --repeats 3
"""

import argparse
import csv
import json
import os
import time

from backends import ask


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", required=True,
                        help="backend:model, repeat the flag for several models")
    parser.add_argument("--repeats", type=int, default=3,
                        help="how often each prompt is asked (replies vary from run to run)")
    parser.add_argument("--prompts", default="prompts.json")
    parser.add_argument("--out", default="results/responses.csv")
    args = parser.parse_args()

    with open(args.prompts, encoding="utf-8") as f:
        items = json.load(f)["items"]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    rows = []
    for spec in args.model:
        for item in items:
            for rep in range(1, args.repeats + 1):
                start = time.perf_counter()
                try:
                    reply, error = ask(spec, item["prompt"]), ""
                except Exception as exc:  # keep going; record the failure
                    reply, error = "", f"{type(exc).__name__}: {exc}"
                seconds = time.perf_counter() - start
                rows.append({
                    "model": spec, "item_id": item["id"], "category": item["category"],
                    "repeat": rep, "prompt": item["prompt"], "reply": reply,
                    "seconds": round(seconds, 3), "error": error,
                })
                status = "ERROR " + error if error else f"{seconds:.2f}s"
                print(f"{spec:30s} {item['id']} #{rep}  {status}")

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {len(rows)} replies to {args.out}")


if __name__ == "__main__":
    main()
