"""Completion and independent review are separate; evidence guards stay mandatory."""

import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from trajectory_offload.cli import add_runtime_options, settings_from_args
from trajectory_offload.contracts import InvestigationTask, Settings
from trajectory_offload.runtime import investigate
from trajectory_offload.server import create_server
from unittest.mock import patch
from test_runtime import FakeClient, report, response


class ReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trajectory-review-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "repo"
        self.root.mkdir()
        self.source = self.root / "settings.py"
        self.original = "def timeout_value(config):\n    return config['timeout_seconds']\n"
        self.source.write_text(self.original, encoding="utf-8")
        self.task = InvestigationTask(objective="Locate timeout_seconds")
        self.settings = Settings(model="test-model", max_model_calls=2, log_dir=base / "logs")

    def test_default_completion_does_not_claim_independent_review(self):
        client = FakeClient([response(json.dumps(report()))])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verification"]["structure_and_citations_valid"])
        self.assertEqual(result["verification"]["semantic_review_status"], "not_requested")
        self.assertFalse(result["verification"]["semantic_support_checked"])
        self.assertFalse(result["verification"]["semantic_support_passed"])
        self.assertEqual(result["metrics"]["verification_model_calls"], 0)
        self.assertEqual(len(client.calls), 1)

    def test_optional_review_can_reject_otherwise_valid_evidence(self):
        review = {"supported": False, "unsupported_claims": ["The runtime claim is unsupported."],
                  "criteria_supported": [], "notes": []}
        client = FakeClient([response(json.dumps(report())), response(json.dumps(review))])
        result = investigate(self.root, self.task, replace(self.settings, semantic_verification=True), client=client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verification"]["semantic_review_status"], "rejected")
        self.assertTrue(result["verification"]["semantic_support_checked"])
        self.assertEqual(result["metrics"]["verification_model_calls"], 1)

    def test_requested_review_timeout_is_distinct_from_not_requested(self):
        client = FakeClient([response(json.dumps(report())), TimeoutError()])
        result = investigate(self.root, self.task, replace(self.settings, semantic_verification=True), client=client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["verification"]["semantic_review_status"], "incomplete")
        self.assertFalse(result["metrics"]["usage_complete"])

    def test_no_review_keeps_citation_coverage_and_observation_guards(self):
        forged = report("not in the source")
        outside = report()
        outside["findings"][0]["evidence"][0]["path"] = "../settings.py"
        missing = report()
        missing["criteria_coverage"] = []
        for label, payload, prefetch in (("forged", forged, True), ("outside", outside, True),
                                         ("coverage", missing, True), ("unobserved", report(), False)):
            with self.subTest(label=label):
                settings = replace(self.settings, max_model_calls=1, prefetch_context=prefetch)
                result = investigate(self.root, self.task, settings, client=FakeClient([response(json.dumps(payload))]))
                self.assertNotEqual(result["status"], "completed")
                self.assertFalse(result["verification"]["structure_and_citations_valid"])
                self.assertEqual(result["verification"]["semantic_review_status"], "not_requested")

    def test_no_review_keeps_snapshot_guard(self):
        class MutatingClient(FakeClient):
            def create(inner, **kwargs):
                result = super().create(**kwargs)
                self.source.write_text(self.original + "# changed\n", encoding="utf-8")
                return result
        result = investigate(self.root, self.task, self.settings,
                             client=MutatingClient([response(json.dumps(report()))]))
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["verification"]["snapshot_unchanged"])

    def test_cli_review_is_opt_in_and_legacy_off_flag_is_supported(self):
        parser = argparse.ArgumentParser()
        add_runtime_options(parser)
        for flags, expected in (([], False), (["--semantic-review"], True),
                                (["--semantic-verification"], True), (["--no-semantic-verification"], False)):
            with self.subTest(flags=flags):
                settings = settings_from_args(parser.parse_args(["--model", "test-model", *flags]))
                self.assertEqual(settings.semantic_verification, expected)

    def test_host_required_review_cannot_be_disabled_by_call_argument(self):
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = FakeClient([response(json.dumps(report())), response(json.dumps(review))])
        server = create_server(self.root, replace(self.settings, semantic_verification=True), client=client)
        asyncio.run(server.call_tool("investigate_repository", {"objective": self.task.objective, "semantic_review": False}))
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[-1]["tool_choice"]["name"], "submit_support_review")

    def test_no_review_keeps_final_time_and_reported_usage_limits(self):
        for overrun in ("time", "tokens"):
            with self.subTest(overrun=overrun):
                clock = [0.0]
                final = response(json.dumps(report()))
                if overrun == "tokens":
                    final.usage.input_tokens = final.usage.total_tokens = self.settings.max_total_tokens + 1
                class LateClient(FakeClient):
                    def create(inner, **kwargs):
                        result = super().create(**kwargs)
                        if overrun == "time":
                            clock[0] = 2.0
                        return result
                with patch("trajectory_offload.runtime.time.monotonic", side_effect=lambda: clock[0]):
                    result = investigate(self.root, self.task, replace(self.settings, max_seconds=1),
                                         client=LateClient([final]))
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["verification"]["semantic_review_status"], "not_requested")
                self.assertTrue(result["diagnostics"])


if __name__ == "__main__":
    unittest.main()
