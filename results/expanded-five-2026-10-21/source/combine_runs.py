"""Make an analysis-only view of compatible completed runs, without inference.

The full baseline and focused prompting experiment use different prompt sets.
We reuse the baseline's matching cells instead of spending compute to ask them
again. Pairing is valid only when prompts, seeds, model weights and generation
settings agree; this module checks those assumptions before copying records.
"""

import argparse
import csv
import json
import os
from pathlib import Path

from benchmark import RESPONSE_FIELDS, canonical_hash, read_jsonl, write_csv, write_json
from score import validate_records


def write_jsonl(path, rows):
    """Replace a derived table atomically; source journals are never modified."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    os.replace(temporary, path)


def combine(source_dirs, output_dir):
    """Validate common treatment settings and form the union of observed cells.

    A baseline-only cell outside the focused set stays unmatched. It must not
    be invented or compared with the average of a different prompt population.
    report.compare_strategies pairs model/item/repeat keys on the actual overlap.
    """
    output_dir = Path(output_dir)
    sources = [Path(path).resolve() for path in source_dirs]
    if not sources:
        raise ValueError("At least one compatible source run is required")
    if output_dir.resolve() in sources:
        raise ValueError("An analysis output directory cannot replace a source run")
    manifests, items, rows, plans = [], {}, [], []
    seen_ids, seen_cells = set(), set()
    paired_seeds, condition_text = {}, None
    common_config = None
    for source in sources:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("status") != "complete":
            raise ValueError(f"{source}: only completed source runs can be combined")
        # Suite and condition lists may differ by design. All other settings,
        # including paired seed policy and actual thinking overrides, must agree.
        config = {key: value for key, value in manifest["config"].items()
                  if key not in {"conditions", "suite_sha256"}}
        if common_config is not None and config != common_config:
            raise ValueError("Generation settings/model digests differ across source runs")
        if manifests and any(manifest.get(key) != manifests[0].get(key)
                             for key in ("scorer_version", "scoring_source_sha256", "ollama_version")):
            raise ValueError("Scorer or Ollama version differs across source runs")
        common_config = config
        current_conditions = json.loads((source / "conditions.json").read_text(encoding="utf-8"))
        if condition_text is not None and current_conditions != condition_text:
            raise ValueError("Frozen prompt-strategy text differs across source runs")
        condition_text = current_conditions
        provenance = json.loads((source / "scoring_provenance.json").read_text(encoding="utf-8"))
        if (provenance.get("scorer_version") != manifest.get("scorer_version") or
                provenance.get("source_sha256") != manifest.get("scoring_source_sha256")):
            raise ValueError("Actual scoring provenance disagrees with source manifest")
        suite = json.loads((source / "prompts.snapshot.json").read_text(encoding="utf-8"))
        if canonical_hash(suite) != manifest["suite_sha256"]:
            raise ValueError(f"{source}: frozen prompt snapshot was changed")
        for item in suite["items"]:
            if item["id"] in items and items[item["id"]] != item:
                raise ValueError(f"Shared prompt/rubric differs: {item['id']}")
            items[item["id"]] = item
        source_rows = read_jsonl(source / "responses.jsonl")
        if not source_rows:
            raise ValueError("Empty sources cannot be presented as a completed experiment")
        validate_records(source, source_rows, {item["id"]: item for item in suite["items"]})
        if len(source_rows) != manifest["planned_jobs"]:
            raise ValueError(f"{source}: committed rows do not match completed manifest")
        scored_rows = read_jsonl(source / "scores.jsonl")
        if ({row["row_id"] for row in scored_rows} != {row["row_id"] for row in source_rows} or
                any(row.get("scorer_version") != manifest.get("scorer_version") for row in scored_rows)):
            raise ValueError("Source score rows are missing or use different measurement versions")
        with (source / "plan.csv").open(encoding="utf-8", newline="") as handle:
            source_plan = {row["row_id"]: row for row in csv.DictReader(handle)}
        for original in source_rows:
            cell = tuple(str(original[key]) for key in ("model", "item_id", "condition", "repeat"))
            if original["row_id"] in seen_ids or cell in seen_cells:
                raise ValueError(f"Duplicate observed experimental cell: {cell}")
            if original["row_id"] not in source_plan:
                raise ValueError("Source response is outside its recorded plan")
            pair = tuple(str(original[key]) for key in ("model", "item_id", "repeat"))
            if pair in paired_seeds and paired_seeds[pair] != original["seed"]:
                raise ValueError("Matched strategy cells use different random seeds")
            paired_seeds[pair] = original["seed"]
            seen_ids.add(original["row_id"])
            seen_cells.add(cell)
            rows.append({**original, "source_run_id": manifest["run_id"],
                         "source_run_path": str(source)})
            plans.append({**source_plan[original["row_id"]], "order": len(plans) + 1})
        manifests.append(manifest)

    suite = {"version": 2, "title": "Compatible union of full baseline and focused prompting study",
             "items": list(items.values())}
    conditions = list(dict.fromkeys(row["condition"] for row in rows))
    coverage = {condition: len({row["item_id"] for row in rows if row["condition"] == condition})
                for condition in conditions}
    source_fingerprints = [{"path": str(source), "run_id": manifest["run_id"],
                            "suite_sha256": manifest["suite_sha256"]}
                           for source, manifest in zip(sources, manifests)]
    manifest = {**manifests[0], "run_id": canonical_hash(source_fingerprints)[:16],
                "analysis_only": True, "status": "complete", "source_runs": source_fingerprints,
                "suite_sha256": canonical_hash(suite), "planned_jobs": len(rows),
                "completed_jobs": len(rows),
                "planned_requests": sum(row["expected_turns"] for row in rows),
                "condition_item_counts": coverage,
                "analysis_note": "Condition populations differ. Compare strategies using matched item/repeat cells only.",
                "config": {**common_config, "conditions": conditions, "suite_sha256": canonical_hash(suite)}}
    output_dir.mkdir(parents=True, exist_ok=True)
    existing_path = output_dir / "manifest.json"
    if existing_path.exists():
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        if not existing.get("analysis_only") or existing.get("source_runs") != source_fingerprints:
            raise ValueError("Refusing to replace a different run with a derived analysis")
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "prompts.snapshot.json", suite)
    for filename in ("inventory.json", "conditions.json"):
        write_json(output_dir / filename, json.loads((sources[0] / filename).read_text(encoding="utf-8")))
    write_jsonl(output_dir / "responses.jsonl", rows)
    write_csv(output_dir / "responses.csv", rows, [*RESPONSE_FIELDS, "source_run_id", "source_run_path"])
    write_csv(output_dir / "plan.csv", plans, list(plans[0]))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    manifest = combine(args.source, args.out)
    print(f"Analysis view: {manifest['completed_jobs']} recorded conversations; "
          f"per-condition items: {manifest['condition_item_counts']}. No inference performed.")


if __name__ == "__main__":
    main()
