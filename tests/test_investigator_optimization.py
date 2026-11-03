"""Regression checks for evidence-ID safety and focused source retrieval."""

import json
from pathlib import Path
import tempfile
import unittest

from trajectory_offload.contracts import InvestigationTask, Settings
from trajectory_offload.evidence import EvidenceCatalog, compact_evidence
from trajectory_offload.repository import RepositoryTools, Snapshot
from trajectory_offload.runtime import execute_tool, investigate
from test_runtime import FakeClient, read_call, report, response


class OptimizedInvestigatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trajectory-focus-")
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.settings = Settings(model="test-model", max_model_calls=3, log_dir=self.base / "logs", semantic_verification=True)

    def tearDown(self):
        self.temp.cleanup()

    def tools(self):
        return RepositoryTools(Snapshot.capture(self.root, self.settings), [])

    def test_qualified_names_resolve_the_requested_owner(self):
        (self.root / "settings.py").write_text("class Config:\n    def load(self): return 7\nclass Other:\n    def load(self): return 8\n")
        task = InvestigationTask(objective="Static impact of Config.load", task_type="change_impact", symbols=["Config.load"])
        tools = self.tools()
        self.assertEqual(tools.definitions(task.symbols[0])[0]["source"], "    def load(self): return 7")
        self.assertEqual(len(tools.definitions("settings.Config.load")), 1)
        self.assertEqual(len(tools.definitions("load")), 2)
        for bad in ("Config..load", "Config/load", "Config.load()"):
            with self.assertRaises(ValueError):
                tools.definitions(bad)

    def test_focus_finds_executable_body_and_tests_beyond_long_class_docs(self):
        (self.root / "settings.py").write_text('class Config:\n    """\n' + "    General configuration documentation.\n" * 140
            + '    """\n    def load(self, timeout_seconds):\n        return timeout_seconds * 2\n')
        (self.root / "test_settings.py").write_text("from settings import Config\ndef test_timeout_seconds():\n    assert Config().load(4) == 8\n")
        tools = self.tools()
        context = tools.focused_context("Trace timeout_seconds in Config.load", [], ["Config.load"], [])
        text = "\n".join(entry["source"] for entry in context["excerpts"])
        self.assertIn("return timeout_seconds * 2", text)
        self.assertIn("assert Config().load(4) == 8", text)
        self.assertNotIn("General configuration documentation.", text)
        self.assertLessEqual(context["source_lines"], 260)

    def test_catalog_preserves_exact_fragments_and_deduplicates_reads(self):
        (self.root / "settings.py").write_text("# " + "long" * 700 + "\nvalue = 3\n")
        tools, catalog = self.tools(), EvidenceCatalog()
        source = tools.excerpt("settings.py", 1, 2)
        first = catalog.encode(source)
        self.assertGreater(len(first["excerpts"]), 1)
        for entry in catalog.entries.values():
            self.assertIsNone(tools.validate_evidence(entry))
            self.assertLessEqual(len(entry.quote), 1800)
        repeated = catalog.encode(source)
        self.assertTrue(all(item.get("already_provided") for item in repeated["excerpts"]))
        self.assertEqual([item["id"] for item in first["excerpts"]], [item["id"] for item in repeated["excerpts"]])
        with self.assertRaises(ValueError):
            catalog.expand({"id": "E9999"})

    def test_model_selects_observed_ids_and_still_receives_independent_review(self):
        (self.root / "settings.py").write_text("def timeout_value(config):\n    return config['timeout_seconds']\n")
        final = report()
        final["findings"][0]["evidence"] = [{"id": "E1"}]
        client = FakeClient([response(json.dumps(final)), response(json.dumps(
            {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}))])
        result = investigate(self.root, InvestigationTask(objective="Locate timeout_seconds"), self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verification"]["semantic_support_passed"])
        self.assertIn("return config", result["findings"][0]["evidence"][0]["quote"])
        self.assertEqual(result["metrics"]["verification_model_calls"], 1)

    def test_overlapping_reads_reuse_old_source_and_deliver_new_lines(self):
        (self.root / "settings.py").write_text("a = 1\nb = 2\nc = 3\n")
        tools, catalog = self.tools(), EvidenceCatalog()
        catalog.encode(tools.excerpt("settings.py", 1, 2))
        extra = catalog.encode(tools.excerpt("settings.py", 2, 3))["excerpts"]
        self.assertEqual(extra[0]["id"], "E1")
        self.assertTrue(extra[0]["already_provided"])
        self.assertEqual(extra[1]["source"], "c = 3")
        for entry in catalog.entries.values():
            self.assertIsNone(tools.validate_evidence(entry))

    def test_batch_reads_paginate_without_losing_or_broadening_requested_source(self):
        (self.root / "settings.py").write_text("\n".join(f"value_{n} = {n}" for n in range(125)))
        output = execute_tool(self.tools(), "read_source_ranges", {"ranges": [
            {"path": "settings.py", "start_line": 1, "end_line": 130}]})
        self.assertEqual([(item["start_line"], item["end_line"]) for item in output], [(1, 100), (101, 125)])
        self.assertIn("value_124 = 124", output[-1]["source"])
        bad = execute_tool(self.tools(), "read_source_ranges", {"ranges": [
            {"path": "../other.py", "start_line": 1, "end_line": 3}]})
        self.assertIn("error", bad[0])

    def test_two_gathering_turns_then_synthesis_preserves_the_review(self):
        (self.root / "settings.py").write_text("def timeout_value(config):\n    return config['timeout_seconds']\n")
        settings = Settings(model="test-model", max_model_calls=8, prefetch_context=False, log_dir=self.base / "logs",
                            semantic_verification=True)
        client = FakeClient([response(calls=[read_call()]), response(calls=[read_call()]), response(json.dumps(report())),
            response(json.dumps({"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}))])
        result = investigate(self.root, InvestigationTask(objective="Locate timeout_seconds"), settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["source_gathering_turns"], 2)
        self.assertEqual(client.calls[2]["tool_choice"]["name"], "submit_investigation")
        self.assertEqual(result["metrics"]["verification_model_calls"], 1)

    def test_unknown_id_cannot_complete_or_reach_review(self):
        (self.root / "settings.py").write_text("timeout_seconds = 3\n")
        final = report()
        final["findings"][0]["evidence"] = [{"id": "E9999"}]
        settings = Settings(model="test-model", max_model_calls=2, log_dir=self.base / "logs")
        client = FakeClient([response(json.dumps(final))])
        result = investigate(self.root, InvestigationTask(objective="Locate timeout_seconds"), settings, client=client)
        self.assertNotEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["verification_model_calls"], 0)
        self.assertIn("unknown observed evidence ID", Path(result["log_path"]).read_text())

    def test_public_projection_is_lossless_and_shared(self):
        original = report()
        original["findings"].append(original["findings"][0])
        projected = compact_evidence(original)
        self.assertEqual(len(projected["evidence"]), 1)
        self.assertEqual(projected["findings"][0]["evidence"], projected["findings"][1]["evidence"])
        table = projected.pop("evidence")
        def expand(value):
            if isinstance(value, list):
                return [expand(item) for item in value]
            if isinstance(value, dict):
                return table[value["id"]] if set(value) == {"id"} else {key: expand(item) for key, item in value.items()}
            return value
        self.assertEqual(expand(projected), original)


if __name__ == "__main__":
    unittest.main()
