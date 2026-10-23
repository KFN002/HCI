"""Small in-memory model fixtures verify scheduling and persistence without inference."""

import copy
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from benchmark import CONDITIONS, read_jsonl
import run_tests


def item(item_id="one", messages=None):
    """A minimal test case; no real model is used to answer it."""
    value = {"id": item_id, "category": "control", "prompt": "Return OK.",
             "rubric": "Return OK.", "scoring": {"type": "exact", "expected": ["OK"]}}
    if messages:
        value["messages"] = [{"role": "user", "content": content} for content in messages]
    return value


def inventory():
    """Two names share weights; a third model has its own digest."""
    return {"version": "test-fixture", "models": [
        {"name": "model-a", "digest": "digest-a", "eligible": True},
        {"name": "alias-a", "digest": "digest-a", "eligible": True},
        {"name": "model-b", "digest": "digest-b", "eligible": True},
        {"name": "embedding", "digest": "digest-e", "eligible": False,
         "skip_reason": "No completion capability"},
    ]}


def answer(reply="OK", reason="stop", thinking=""):
    """Backend-shaped deterministic response with no network dependency."""
    return {"reply": reply, "thinking": thinking, "seconds": .2,
            "ttft_seconds": .05 if reply else None, "first_event_seconds": .02,
            "total_duration_seconds": .2, "load_seconds": .01,
            "prompt_eval_seconds": .04, "eval_seconds": .15,
            "prompt_tokens": 8, "completion_tokens": 3,
            "tokens_per_second": 20, "done_reason": reason}


class FakeClient:
    """Only fixtures; all supplied messages are captured before later mutations."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])
        self.inventory_calls = 0

    def inventory(self):
        self.inventory_calls += 1
        return inventory()

    def chat(self, model, messages, **options):
        self.calls.append((model, copy.deepcopy(messages), copy.deepcopy(options)))
        result = self.responses.pop(0) if self.responses else answer()
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prompts = self.root / "prompts.json"
        self.write_suite([item()])
        self.args = SimpleNamespace(
            run_dir=str(self.root / "run"), prompts=str(self.prompts),
            categories=None, inventory_file=None, model=None, include_aliases=False,
            repeats=2, conditions=list(CONDITIONS), seed=12345, temperature=.5,
            num_predict=128, num_ctx=2048, timeout=30, keep_alive="2m",
            think="default", max_jobs=None)
        self.source_patch = patch.object(run_tests, "generation_hash", return_value="test-source")
        self.environment_patch = patch.object(run_tests, "environment", return_value={"fixture": True})
        self.source_patch.start()
        self.environment_patch.start()

    def tearDown(self):
        self.source_patch.stop()
        self.environment_patch.stop()
        self.temporary.cleanup()

    def write_suite(self, items):
        self.prompts.write_text(json.dumps({"version": 2, "items": items}), encoding="utf-8")

    def prepare(self, client=None):
        return run_tests.prepare(self.args, client or FakeClient())

    def test_prepare_freezes_artifacts_and_never_calls_chat(self):
        client = FakeClient()
        manifest, suite, jobs = self.prepare(client)
        self.assertEqual(client.inventory_calls, 1)
        self.assertEqual(client.calls, [])
        self.assertEqual(manifest["status"], "prepared")
        self.assertEqual(manifest["planned_jobs"], 12)
        self.assertEqual(manifest["planned_requests"], 12)
        run_dir = Path(self.args.run_dir)
        for filename in ["manifest.json", "prompts.snapshot.json", "inventory.json",
                         "conditions.json", "plan.csv", "responses.csv", "responses.jsonl"]:
            self.assertTrue((run_dir / filename).is_file(), filename)
        self.assertEqual(read_jsonl(run_dir / "responses.jsonl"), [])
        with (run_dir / "responses.csv").open(newline="") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_offline_inventory_prepare_never_uses_the_client(self):
        saved = self.root / "inventory.json"
        saved.write_text(json.dumps(inventory()), encoding="utf-8")
        self.args.inventory_file = str(saved)
        client = FakeClient()
        self.prepare(client)
        self.assertEqual(client.inventory_calls, 0)
        self.assertEqual(client.calls, [])

    def test_aliases_deduplicate_by_digest_and_explicit_selection_validates(self):
        models = run_tests.select_models(inventory())
        self.assertEqual(len(models), 2)
        first = next(model for model in models if model["digest"] == "digest-a")
        self.assertEqual(set([first["name"], *first["aliases"]]), {"alias-a", "model-a"})
        self.assertEqual(len(run_tests.select_models(inventory(), include_aliases=True)), 3)
        self.assertEqual(run_tests.select_models(inventory(), ["ollama:model-a"])[0]["name"], "model-a")
        for selection in [["missing"], ["embedding"]]:
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                run_tests.select_models(inventory(), selection)

    def test_cline_model_and_same_digest_alias_are_excluded(self):
        data = inventory()
        data["models"].extend([
            {"name": "qwen3-coder:latest", "digest": "protected", "eligible": True},
            {"name": "coding-alias", "digest": "protected", "eligible": True},
        ])
        self.assertFalse(any(m["digest"] == "protected" for m in run_tests.select_models(data)))
        for name in ("qwen3-coder:latest", "coding-alias"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "protected"):
                run_tests.select_models(data, [name])

    def test_thinking_off_is_capability_aware(self):
        models = [{"name": "reasoner", "digest": "a", "capabilities": ["thinking"]},
                  {"name": "plain", "digest": "b", "capabilities": ["completion"]}]
        self.args.think = "off"
        config = run_tests.config_from_args(self.args, {"items": [item()]}, models)
        self.assertIs(config["thinking_by_model"]["reasoner"], False)
        self.assertIsNone(config["thinking_by_model"]["plain"])
        self.args.think = "on"
        with self.assertRaisesRegex(ValueError, "support thinking"):
            run_tests.config_from_args(self.args, {"items": [item()]}, models)

    def test_thinking_only_weights_cannot_be_silently_disabled(self):
        model = {"name": "qwen3:4b", "digest": "test", "capabilities": ["thinking"],
                 "model_info": {"general.finetune": "Thinking", "general.version": "2507"}}
        self.args.think = "off"
        with self.assertRaisesRegex(ValueError, "thinking only"):
            run_tests.config_from_args(self.args, {"items": [item()]}, [model])

    def test_schedule_reproducible_and_seeds_paired_across_conditions_and_models(self):
        manifest, suite, jobs = self.prepare()
        self.assertEqual(jobs, run_tests.make_plan(suite, manifest["models"], manifest["config"]))
        self.assertEqual(len({job["row_id"] for job in jobs}), len(jobs))
        for repeat in [1, 2]:
            self.assertEqual(len({job["seed"] for job in jobs if job["repeat"] == repeat}), 1)
        self.assertNotEqual(jobs[0]["seed"], next(job["seed"] for job in jobs
                                                if job["repeat"] != jobs[0]["repeat"]))

    def test_identical_prepare_preserves_manifest_and_changed_config_refused(self):
        manifest, suite, jobs = self.prepare()
        again, _, again_jobs = self.prepare()
        self.assertEqual(manifest, again)
        self.assertEqual(jobs, again_jobs)
        self.args.num_predict += 1
        with self.assertRaisesRegex(ValueError, "changed"):
            self.prepare()

    def test_changed_suite_refused_before_chat(self):
        self.prepare()
        self.write_suite([item("different")])
        client = FakeClient()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.prepare(client)
        self.assertEqual(client.calls, [])

    def test_edited_frozen_snapshot_or_conditions_refused(self):
        self.prepare()
        run_dir = Path(self.args.run_dir)
        snapshot = run_dir / "prompts.snapshot.json"
        original = snapshot.read_text(encoding="utf-8")
        changed = json.loads(original)
        changed["items"][0]["scoring"]["expected"] = ["wrong"]
        snapshot.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.prepare()
        snapshot.write_text(original, encoding="utf-8")
        (run_dir / "conditions.json").write_text('{}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "strategies changed"):
            self.prepare()

    def test_edited_schedule_and_changed_ollama_version_refused(self):
        self.prepare()
        plan = Path(self.args.run_dir) / "plan.csv"
        original = plan.read_text(encoding="utf-8")
        plan.write_text(original.replace("control", "edited"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schedule changed"):
            self.prepare()
        plan.write_text(original, encoding="utf-8")
        client = FakeClient()
        with patch.object(client, "inventory", return_value={**inventory(), "version": "changed"}):
            with self.assertRaisesRegex(ValueError, "version changed"):
                self.prepare(client)
    def test_changed_model_digest_refused_before_chat(self):
        self.prepare()
        client = FakeClient()
        changed = inventory()
        changed["models"][0]["digest"] = "new-weights"
        with patch.object(client, "inventory", return_value=changed), \
                self.assertRaisesRegex(ValueError, "changed"):
            self.prepare(client)

    def test_multi_turn_uses_actual_replies_then_resets_conversation(self):
        self.write_suite([item(messages=["Remember blue.", "What colour?"])])
        manifest, suite, jobs = self.prepare()
        client = FakeClient([answer("Understood", thinking="check"), answer("blue"),
                             answer("Understood again"), answer("blue")])
        row = run_tests.conversation(client, jobs[0], suite["items"][0], manifest["config"])
        self.assertEqual(row["reply"], "blue")
        self.assertEqual(row["completed_turns"], 2)
        self.assertEqual(row["expected_turns"], 2)
        self.assertEqual(row["prompt_tokens"], 16)
        self.assertEqual(row["completion_tokens"], 6)
        self.assertEqual(row["tokens_per_second"], 20)
        second_messages = client.calls[1][1]
        self.assertEqual(second_messages[-2], {"role": "assistant", "content": "Understood",
                                                "thinking": "check"})
        self.assertEqual(second_messages[-1]["content"], "What colour?")
        self.assertEqual(client.calls[1][2]["options"]["seed"],
                         (client.calls[0][2]["options"]["seed"] + 1) % 2147483647)
        run_tests.conversation(client, jobs[0], suite["items"][0], manifest["config"])
        self.assertFalse(any(message["role"] == "assistant" for message in client.calls[2][1]))

    def test_transport_empty_and_truncation_outcomes_remain_distinct(self):
        manifest, suite, jobs = self.prepare()
        case = suite["items"][0]
        error = run_tests.conversation(FakeClient([RuntimeError("OOM")]), jobs[0], case, manifest["config"])
        self.assertIn("OOM", error["error"])
        self.assertEqual(error["completed_turns"], 0)
        self.assertIsNone(error["completion_tokens"])
        empty = run_tests.conversation(FakeClient([answer("", thinking="budget spent")]),
                                       jobs[0], case, manifest["config"])
        self.assertIn("No visible answer", empty["error"])
        self.assertEqual(empty["reply"], "")
        truncated = run_tests.conversation(FakeClient([answer("partial", reason="length")]),
                                           jobs[0], case, manifest["config"])
        self.assertEqual(truncated["error"], "")
        self.assertEqual(truncated["truncated"], 1)
        self.assertEqual(truncated["done_reason"], "length")

    def test_append_resume_keeps_error_and_exports_multiline_unicode(self):
        self.args.model = ["model-a"]
        self.args.repeats = 1
        self.args.conditions = ["baseline", "structured"]
        self.args.max_jobs = 1
        manifest, suite, jobs = self.prepare()
        failed = FakeClient([RuntimeError("temporary failure")])
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(run_tests.execute(self.args, failed, manifest, suite, jobs), 0)
        journal = Path(self.args.run_dir) / "responses.jsonl"
        before = journal.read_bytes()
        self.assertEqual(len(read_jsonl(journal)), 1)
        self.assertEqual(len(failed.calls), 1)
        manifest, suite, jobs = self.prepare()
        success = FakeClient([answer("München\n\"quoted\", comma")])
        with patch("sys.stdout", new_callable=io.StringIO):
            run_tests.execute(self.args, success, manifest, suite, jobs)
        self.assertEqual(len(success.calls), 1)
        self.assertTrue(journal.read_bytes().startswith(before))
        rows = read_jsonl(journal)
        self.assertEqual(len(rows), 2)
        self.assertIn("temporary failure", rows[0]["error"])
        with (Path(self.args.run_dir) / "responses.csv").open(newline="", encoding="utf-8") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(exported[1]["reply"], "München\n\"quoted\", comma")
        self.assertEqual(manifest["status"], "complete")
        with patch("sys.stdout", new_callable=io.StringIO):
            run_tests.execute(self.args, success, manifest, suite, jobs)
        self.assertEqual(len(success.calls), 1, "completed and error rows must never auto-retry")

    def test_damaged_or_unplanned_journal_refused_without_chat(self):
        manifest, suite, jobs = self.prepare()
        journal = Path(self.args.run_dir) / "responses.jsonl"
        client = FakeClient()
        for content in ['{"row_id":', '{"row_id":"unplanned"}\n']:
            journal.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                run_tests.execute(self.args, client, manifest, suite, jobs)
            self.assertEqual(client.calls, [])

    def test_valid_last_record_without_newline_resumes_with_separator(self):
        self.args.model = ["model-a"]
        self.args.repeats = 1
        self.args.conditions = ["baseline", "structured"]
        self.args.max_jobs = 1
        manifest, suite, jobs = self.prepare()
        client = FakeClient()
        with patch("sys.stdout", new_callable=io.StringIO):
            run_tests.execute(self.args, client, manifest, suite, jobs)
        journal = Path(self.args.run_dir) / "responses.jsonl"
        journal.write_bytes(journal.read_bytes().rstrip(b"\n"))
        with patch("sys.stdout", new_callable=io.StringIO):
            run_tests.execute(self.args, client, manifest, suite, jobs)
        self.assertEqual(len(read_jsonl(journal)), 2)
        self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
