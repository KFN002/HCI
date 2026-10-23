"""Transport fixtures: every request is mocked; no Ollama process is contacted."""

import io
import json
import os
import unittest
from unittest.mock import patch

from backends import OllamaClient, OllamaError, _NoRedirect, local_base_url


def stream(*events):
    """Build an in-memory NDJSON response for the streaming parser."""
    return io.BytesIO(b"\n".join(json.dumps(e).encode() for e in events) + b"\n")


class LocalAddressTests(unittest.TestCase):
    def test_normalizes_loopback_and_ollama_host_forms(self):
        for value, expected in [
            ("localhost:11434", "http://localhost:11434"),
            ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
            ("http://[::1]:11434", "http://[::1]:11434"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(local_base_url(value), expected)

    def test_rejects_remote_or_ambiguous_addresses(self):
        for value in ["https://ollama.com", "http://192.168.0.2:11434",
                      "http://localhost.evil.test", "http://127.0.0.1.evil.test",
                      "http://localhost:11434/api", "http://a:b@localhost:11434",
                      "http://0.0.0.0:11434", "http://localhost?other=server",
                      "https://localhost:11434", "file:///tmp/server"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_base_url(value)

    def test_env_and_timeout_validation(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "127.0.0.1:1234"}, clear=True):
            self.assertEqual(OllamaClient().base_url, "http://127.0.0.1:1234")
        for timeout in [0, -1, float("inf"), float("nan")]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                OllamaClient("localhost:11434", timeout=timeout)

    def test_redirects_are_not_followed(self):
        with self.assertRaises(OllamaError):
            _NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://elsewhere")


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient("localhost:11434")

    def test_inventory_is_metadata_only_and_retains_aliases(self):
        calls = []

        def metadata(path, payload=None):
            calls.append((path, payload))
            if path == "/api/tags":
                return {"models": [{"name": "a", "digest": "same"},
                                   {"name": "alias", "digest": "same"},
                                   {"name": "embed"}, {"name": "remote"}]}
            if path == "/api/version":
                return {"version": "fixture"}
            if path == "/api/show":
                name = payload["model"]
                if name == "embed":
                    return {"capabilities": ["embedding"]}
                if name == "remote":
                    return {"capabilities": ["completion"], "remote_host": "ollama.com"}
                return {"capabilities": ["completion"], "details": {"parameter_size": "3B"}}
            self.fail(f"Unexpected request: {path}")

        with patch.object(self.client, "_json", side_effect=metadata):
            inventory = self.client.inventory()
        self.assertEqual(inventory["version"], "fixture")
        self.assertEqual(len(inventory["models"]), 4)
        self.assertEqual([m["eligible"] for m in inventory["models"]], [True, True, False, False])
        self.assertTrue(all(path in {"/api/tags", "/api/version", "/api/show"}
                            for path, _ in calls))
        self.assertIn("local inference", inventory["models"][-1]["skip_reason"])

    def test_failed_show_is_explicit_skip(self):
        with patch.object(self.client, "_json", side_effect=[
            {"models": [{"name": "a"}]}, {"version": "fixture"}, OllamaError("bad metadata")
        ]):
            model = self.client.inventory()["models"][0]
        self.assertFalse(model["eligible"])
        self.assertIn("bad metadata", model["skip_reason"])

    def test_missing_capabilities_is_explicit_warning(self):
        with patch.object(self.client, "_json", side_effect=[
            {"models": [{"name": "a"}]}, {"version": "fixture"}, {}
        ]):
            model = self.client.inventory()["models"][0]
        self.assertTrue(model["eligible"])
        self.assertIn("unverified", model["metadata_warning"])


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient("localhost:11434")
        self.messages = [{"role": "user", "content": "fixture"}]

    def test_stream_keeps_thinking_separate_and_converts_counters(self):
        response = stream(
            {"message": {"thinking": "private reasoning"}, "done": False},
            {"message": {"content": "Hel"}, "done": False},
            {"message": {"content": "lo"}, "done": True, "done_reason": "stop",
             "total_duration": 2_000_000_000, "load_duration": 100_000_000,
             "prompt_eval_duration": 200_000_000, "eval_duration": 500_000_000,
             "prompt_eval_count": 10, "eval_count": 25})
        ticks = iter(i / 100 for i in range(100))
        with patch.object(self.client, "_open", return_value=response) as opened, \
                patch("backends.time.perf_counter", side_effect=lambda: next(ticks)):
            result = self.client.chat("model", self.messages, options={"seed": 3})
        self.assertEqual(result["reply"], "Hello")
        self.assertEqual(result["thinking"], "private reasoning")
        self.assertLess(result["first_event_seconds"], result["ttft_seconds"])
        self.assertEqual(result["total_duration_seconds"], 2)
        self.assertEqual(result["load_seconds"], .1)
        self.assertEqual(result["completion_tokens"], 25)
        self.assertEqual(result["tokens_per_second"], 50)
        payload = opened.call_args.args[1]
        self.assertTrue(payload["stream"])
        self.assertNotIn("think", payload)
        self.assertEqual(payload["options"]["seed"], 3)

    def test_missing_metrics_stay_missing_and_think_can_be_disabled(self):
        with patch.object(self.client, "_open", return_value=stream(
                {"message": {"content": "answer"}, "done": True})) as opened:
            result = self.client.chat("model", self.messages, options={}, think=False)
        self.assertIsNone(result["completion_tokens"])
        self.assertIsNone(result["tokens_per_second"])
        self.assertFalse(opened.call_args.args[1]["think"])

    def test_thinking_only_never_becomes_an_answer(self):
        with patch.object(self.client, "_open", return_value=stream(
                {"message": {"thinking": "thought"}, "done": True})):
            result = self.client.chat("model", self.messages, options={})
        self.assertEqual(result["reply"], "")
        self.assertIsNone(result["ttft_seconds"])

    def test_incomplete_and_malformed_responses_fail(self):
        fixtures = [
            stream({"message": {"content": "partial"}, "done": False}),
            io.BytesIO(b"not JSON\n"),
            stream(["not an object"]),
            stream({"error": "out of memory"}),
            stream({"message": {"content": 99}, "done": True}),
            stream({"message": {"tool_calls": [{"name": "send"}]}, "done": True}),
            stream({"message": {"content": "answer"}, "done": True, "eval_count": -1}),
            stream({"message": {"content": "answer"}, "done": True, "eval_count": True}),
        ]
        for fixture in fixtures:
            with self.subTest(fixture=fixture), \
                    patch.object(self.client, "_open", return_value=fixture), \
                    self.assertRaises(OllamaError):
                self.client.chat("model", self.messages, options={})

    def test_cloud_tag_never_reaches_chat_endpoint(self):
        with patch.object(self.client, "_open") as opened, self.assertRaises(ValueError):
            self.client.chat("model:cloud", self.messages, options={})
        opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
