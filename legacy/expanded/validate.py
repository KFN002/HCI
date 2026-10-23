"""Offline validation only: inspect every prompt and rubric, never call a model."""

import argparse
from collections import Counter
import re

from benchmark import CONDITIONS, load_suite, user_turns
from score import SCORER_VERSION, validate_scoring


def validate(path="prompts.json"):
    """Check measurement metadata as well as syntax, without a serving process."""
    suite = load_suite(path)
    for item in suite["items"]:
        validate_scoring(item)
        if suite.get("version", 0) >= 3:
            if not item.get("research_role") or not item.get("expected_behavior"):
                raise ValueError(f"{item['id']}: study-v3 cases need research_role and expected_behavior")
        for field in ("rationale", "rubric"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"{item['id']}: missing {field}")
        if item.get("messages") and user_turns(item)[-1]["content"] != item["prompt"]:
            raise ValueError(f"{item['id']}: prompt must equal final user turn")
        for field in ("required_regex", "forbidden_regex"):
            for pattern in item["scoring"].get(field, []):
                re.compile(pattern)
                # Double-escaped regexes accidentally search for literal backslashes.
                if any(token in pattern for token in (r"\\s", r"\\b", r"\\d", r"\\x")):
                    raise ValueError(f"{item['id']}: suspicious double-escaped regex {pattern!r}")
    counts = Counter(item["category"] for item in suite["items"])
    print(f"Validated {len(suite['items'])} cases across {len(counts)} categories; "
          f"{len(CONDITIONS)} prompt strategies.")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")
    roles = Counter(item.get("research_role", "not declared") for item in suite["items"])
    print(f"Scorer version: {SCORER_VERSION}; research-role counts: {dict(sorted(roles.items()))}")
    print("Offline only: no network requests or inference.")
    return suite


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompts", default="prompts.json")
    validate(parser.parse_args().prompts)
