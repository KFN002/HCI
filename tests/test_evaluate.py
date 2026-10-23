"""Runner invariants with a fake client; no Ollama process or network access."""

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import evaluate
from backends import OllamaError


class FakeClient:
    def __init__(self, outcomes=()):
        self.calls = []
        self.outcomes = iter(outcomes)

    def chat(self, model, messages, **settings):
        self.calls.append((model, copy.deepcopy(messages), copy.deepcopy(settings)))
        outcome = next(self.outcomes, {"reply": "test reply", "seconds": 0.01, "done_reason": "stop"})
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def inventory():
    return {"version": "test-server", "models": [
        {"name": name, "digest": f"digest-{index}", "eligible": True}
        for index, name in enumerate(evaluate.MODELS)
    ] + [{"name": evaluate.PROTECTED_MODEL, "digest": "protected-digest", "eligible": True}]}


def small_suite():
    return {"items": [
        {"id": "first", "category": "test", "prompt": "Visible user prompt one.",
         "system": "Visible task system.", "rubric": "HIDDEN RUBRIC",
         "framework": {"epistemic": {"kind": "not_applicable", "ground_truth": "HIDDEN GROUND TRUTH"}}},
        {"id": "second", "category": "test", "prompt": "Visible user prompt two.",
         "framework": {"clarification": {"required": False}}},
    ]}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.suite = small_suite()
        self.inventory = inventory()

    def test_prepare_freezes_deterministic_complete_plan_and_prompts(self):
        manifest, plan = evaluate.prepare(self.output, self.suite, self.inventory, 3)
        self.assertEqual(manifest["status"], "prepared")
        self.assertEqual(len(plan), len(evaluate.MODELS) * 2 * 3)
        self.assertEqual(len({job["row_id"] for job in plan}), len(plan))
        self.assertEqual(json.loads((self.output / "prompts.snapshot.json").read_text()), self.suite)
        self.assertEqual(json.loads((self.output / "plan.json").read_text()), plan)
        self.assertEqual(manifest["config"]["suite_sha256"], evaluate.digest(self.suite))
        self.assertEqual({job["repeat"] for job in plan}, {1, 2, 3})
        # Every model receives the same item/repeat-specific seed.
        seeds = {}
        for job in plan:
            key = (job["item_id"], job["repeat"])
            seeds.setdefault(key, set()).add(job["seed"])
        self.assertTrue(all(len(values) == 1 for values in seeds.values()))
        manifest_again, plan_again = evaluate.prepare(self.output, self.suite, self.inventory, 3)
        self.assertEqual(manifest_again, manifest)
        self.assertEqual(plan_again, plan)
        self.assertFalse((self.output / "responses.jsonl").exists())

    def test_changed_settings_prompts_or_model_digest_need_new_output(self):
        evaluate.prepare(self.output, self.suite, self.inventory, 3)
        changed_suite = copy.deepcopy(self.suite)
        changed_suite["items"][0]["prompt"] = "Changed prompt."
        changed_inventory = copy.deepcopy(self.inventory)
        changed_inventory["models"][0]["digest"] = "updated-weights"
        for suite, installed, repeats in (
            (self.suite, self.inventory, 4), (changed_suite, self.inventory, 3),
            (self.suite, changed_inventory, 3),
        ):
            with self.subTest(repeats=repeats, prompt=suite["items"][0]["prompt"]):
                with self.assertRaisesRegex(ValueError, "changed"):
                    evaluate.prepare(self.output, suite, installed, repeats)

    def test_modified_snapshot_or_plan_is_rejected(self):
        evaluate.prepare(self.output, self.suite, self.inventory, 3)
        path = self.output / "prompts.snapshot.json"
        original = path.read_text()
        changed = copy.deepcopy(self.suite)
        changed["items"][0]["system"] = "Tampered system."
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            evaluate.prepare(self.output, self.suite, self.inventory, 3)
        path.write_text(original)
        plan_path = self.output / "plan.json"
        plan = json.loads(plan_path.read_text())
        plan.reverse()
        plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "plan changed"):
            evaluate.prepare(self.output, self.suite, self.inventory, 3)

    def test_protected_model_digest_alias_is_excluded_before_freezing(self):
        self.inventory["models"][0]["digest"] = "protected-digest"
        with self.assertRaisesRegex(ValueError, "Protected model or alias"):
            evaluate.prepare(self.output, self.suite, self.inventory, 3)
        self.assertFalse((self.output / "manifest.json").exists())

    def test_unavailable_model_and_orphan_raw_results_are_rejected(self):
        unavailable = copy.deepcopy(self.inventory)
        unavailable["models"][0]["eligible"] = False
        with self.assertRaisesRegex(ValueError, "unavailable"):
            evaluate.prepare(self.output, self.suite, unavailable, 3)
        (self.output / "responses.jsonl").write_text('{"row_id":"orphan"}\n')
        with self.assertRaisesRegex(ValueError, "without their original manifest"):
            evaluate.prepare(self.output, self.suite, self.inventory, 3)

    def test_explicit_model_selection_freezes_only_selected_models(self):
        selected = [evaluate.MODELS[1], evaluate.MODELS[3]]
        manifest, plan = evaluate.prepare(self.output, self.suite, self.inventory, 3, models=selected)
        self.assertEqual([model["name"] for model in manifest["config"]["models"]], selected)
        self.assertEqual({job["model"] for job in plan}, set(selected))
        self.assertEqual(len(plan), 2 * 2 * 3)
        self.assertEqual(manifest["config"]["num_predict"], 512)

    def test_protected_alias_cannot_be_selected_explicitly(self):
        self.inventory["models"].append({"name": "innocent-alias", "digest": "protected-digest", "eligible": True})
        with self.assertRaisesRegex(ValueError, "Protected model or alias"):
            evaluate.prepare(self.output, self.suite, self.inventory, 3, models=["innocent-alias"])


class AdditionalSelectionTests(unittest.TestCase):
    def setUp(self):
        self.inventory = inventory()
        self.inventory["models"].extend([
            {"name": "roleplay", "digest": "roleplay-digest", "eligible": True},
            {"name": "roleplay-alias", "digest": "roleplay-digest", "eligible": True},
            {"name": "thinking", "digest": "thinking-digest", "eligible": True},
            {"name": "default-alias", "digest": "digest-0", "eligible": True},
            {"name": "protected-alias", "digest": "protected-digest", "eligible": True},
            {"name": "not-eligible", "digest": "embedding-digest", "eligible": False},
            {"name": "missing-digest", "eligible": True},
        ])

    def test_remaining_digests_are_selected_once_with_protected_and_default_aliases_excluded(self):
        self.assertEqual(evaluate.additional_models(self.inventory), ["roleplay", "thinking"])

    def test_requested_models_deduplicate_aliases_and_exclude_protected_digests(self):
        self.assertEqual(evaluate.unique_models(self.inventory, ["roleplay", "roleplay-alias", "thinking"]), ["roleplay", "thinking"])
        for name in (evaluate.PROTECTED_MODEL, "protected-alias"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Protected model or alias"):
                evaluate.unique_models(self.inventory, [name])

    def test_explicit_model_cli_freezes_requested_digests_and_correct_count(self):
        client = FakeClient()
        client.inventory = lambda: self.inventory
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            terminal = io.StringIO()
            with patch.object(evaluate, "local_server", return_value=contextlib.nullcontext(client)), \
                    patch.object(evaluate, "finish"), contextlib.redirect_stdout(terminal):
                result = evaluate.main(["--model", "roleplay", "--model", "roleplay-alias",
                                        "--model", "thinking", "--prepare", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("2 models × 60 prompts × 3 repeats = 360 conversations", terminal.getvalue())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual([model["name"] for model in manifest["config"]["models"]], ["roleplay", "thinking"])
            self.assertEqual(client.calls, [])

    def test_no_additional_models_is_a_visible_error(self):
        with self.assertRaisesRegex(ValueError, "No additional eligible"):
            evaluate.additional_models(inventory())

    def test_additional_prepare_cli_freezes_selected_model_count_without_inference(self):
        client = FakeClient()
        client.inventory = lambda: self.inventory
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            terminal = io.StringIO()
            with patch.object(evaluate, "local_server", return_value=contextlib.nullcontext(client)), \
                    patch.object(evaluate, "finish") as finish, contextlib.redirect_stdout(terminal):
                result = evaluate.main(["--additional", "--prepare", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("2 models × 60 prompts × 3 repeats = 360 conversations", terminal.getvalue())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual([model["name"] for model in manifest["config"]["models"]], ["roleplay", "thinking"])
            self.assertEqual(manifest["planned_jobs"], 360)
            self.assertEqual(client.calls, [])
            finish.assert_called_once()

    def test_quick_selection_keeps_first_two_per_category_and_preserves_full_suite(self):
        suite = evaluate.load_suite(evaluate.ROOT / "prompts.framework.json")
        original = copy.deepcopy(suite)
        selected = evaluate.quick_suite(suite)
        self.assertEqual(len(selected["items"]), 12)
        self.assertEqual(selected["design"]["item_count"], 12)
        self.assertTrue(selected["design"]["quick"])
        self.assertEqual(suite, original)
        for category in {item["category"] for item in suite["items"]}:
            self.assertEqual([item for item in selected["items"] if item["category"] == category],
                             [item for item in suite["items"] if item["category"] == category][:2])

    def test_quick_one_model_cli_freezes_thirty_six_conversations(self):
        client = FakeClient()
        client.inventory = lambda: self.inventory
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            terminal = io.StringIO()
            with patch.object(evaluate, "local_server", return_value=contextlib.nullcontext(client)), \
                    patch.object(evaluate, "finish"), contextlib.redirect_stdout(terminal):
                result = evaluate.main(["--quick", "--model", "thinking", "--prepare", "--output", str(output)])
            self.assertEqual(result, 0)
            self.assertIn("1 models × 12 prompts × 3 repeats = 36 conversations", terminal.getvalue())
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertTrue(manifest["config"]["quick"])
            self.assertEqual(manifest["config"]["prompt_count"], 12)
            self.assertEqual(manifest["config"]["condition"], "baseline")
            self.assertEqual(manifest["planned_jobs"], 36)
            self.assertEqual(client.calls, [])


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.suite = small_suite()
        self.manifest = {"status": "prepared", "completed_jobs": 0}
        self.plan = [
            {"row_id": "row-one", "model": "fake-model", "model_digest": "digest",
             "item_id": "first", "category": "test", "repeat": 1, "seed": 11},
            {"row_id": "row-two", "model": "fake-model", "model_digest": "digest",
             "item_id": "second", "category": "test", "repeat": 1, "seed": 22},
        ]

    def collect(self, client):
        with contextlib.redirect_stdout(io.StringIO()):
            return evaluate.collect(client, self.output, self.suite, self.manifest, self.plan)

    def test_only_visible_system_and_user_text_are_sent_with_frozen_options(self):
        client = FakeClient()
        rows = self.collect(client)
        self.assertEqual(client.calls[0], (
            "fake-model",
            [{"role": "system", "content": "Visible task system."},
             {"role": "user", "content": "Visible user prompt one."}],
            {"keep_alive": "2m", "think": None,
             "options": {"seed": 11, "temperature": .5, "num_predict": 512, "num_ctx": 4096}},
        ))
        self.assertEqual(client.calls[1][1], [{"role": "user", "content": "Visible user prompt two."}])
        self.assertNotIn("HIDDEN", json.dumps(client.calls))
        self.assertEqual([row["prompt"] for row in rows], [item["prompt"] for item in self.suite["items"]])
        self.assertEqual(self.manifest["status"], "complete")
        self.assertEqual(self.manifest["completed_jobs"], 2)
        self.assertEqual(evaluate.read_rows(self.output / "responses.jsonl"), rows)

    def test_resume_preserves_completed_rows_and_sends_only_missing_ids(self):
        first = {**self.plan[0], "reply": "saved reply", "error": "", "prompt": self.suite["items"][0]["prompt"]}
        # A complete final JSON row without a terminal newline remains resumable.
        (self.output / "responses.jsonl").write_text(json.dumps(first))
        client = FakeClient()
        rows = self.collect(client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1], [{"role": "user", "content": "Visible user prompt two."}])
        self.assertEqual(rows[0], first)
        self.assertEqual([row["row_id"] for row in rows], ["row-one", "row-two"])
        completed_client = FakeClient()
        self.assertEqual(self.collect(completed_client), rows)
        self.assertEqual(completed_client.calls, [])

    def test_provider_errors_are_journaled_once_and_not_hidden_or_retried(self):
        client = FakeClient([OllamaError("test transport failure")])
        rows = self.collect(client)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(rows[0]["error"], "OllamaError: test transport failure")
        self.assertEqual(rows[0]["reply"], "")
        self.assertFalse(rows[0]["truncated"])
        self.assertEqual(rows[1]["error"], "")
        resumed_client = FakeClient()
        self.assertEqual(self.collect(resumed_client), rows)
        self.assertEqual(resumed_client.calls, [])

    def test_interruption_retains_completed_rows_and_resumes_missing_job(self):
        client = FakeClient([{"reply": "saved", "done_reason": "stop"}, KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.collect(client)
        self.assertEqual(len(evaluate.read_rows(self.output / "responses.jsonl")), 1)
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["completed_jobs"], 1)
        resumed_client = FakeClient()
        rows = self.collect(resumed_client)
        self.assertEqual(len(resumed_client.calls), 1)
        self.assertEqual(rows[0]["reply"], "saved")
        self.assertEqual(self.manifest["status"], "complete")

    def test_duplicate_foreign_or_changed_job_metadata_aborts_before_inference(self):
        valid = {**self.plan[0], "reply": "saved", "error": ""}
        invalid_sets = [
            [valid, valid], [{**valid, "row_id": "foreign-row"}],
            [{**valid, "model_digest": "other-weights"}], [{**valid, "seed": 999}],
        ]
        for saved in invalid_sets:
            with self.subTest(saved=saved):
                (self.output / "responses.jsonl").write_text("".join(json.dumps(row) + "\n" for row in saved))
                client = FakeClient()
                with self.assertRaises(ValueError):
                    self.collect(client)
                self.assertEqual(client.calls, [])

    def test_generation_limits_are_explicitly_recorded(self):
        client = FakeClient([{"reply": "partial", "done_reason": "length"}])
        rows = self.collect(client)
        self.assertTrue(rows[0]["truncated"])
        self.assertFalse(rows[1]["truncated"])


if __name__ == "__main__":
    unittest.main()
