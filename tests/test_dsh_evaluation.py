"""Critical accounting and acceptance invariants for the DSH experiment."""

from http.client import HTTPConnection
import asyncio
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))
from dsh_acceptance import preserves_statements
from dsh_study import Ledger
from dsh_report import summarize


class EvaluationTests(unittest.TestCase):
    def test_dsh_adapters_expose_only_the_selected_scenario(self):
        scenarios = {"impact": "investigate_repository", "diagnosis": "diagnose_failure", "verification": "verify_patch",
                     "regression": "construct_regression_test", "multi_failure": "cluster_test_failures"}
        scripts = Path(__file__).resolve().parent.parent / "benchmarks"
        with tempfile.TemporaryDirectory(prefix="dsh-adapter-test-") as directory:
            base = Path(directory)
            repo = base / "repo"
            repo.mkdir()
            checks = base / "checks.json"
            checks.write_text(json.dumps({"checks": [{"id": "noop", "argv": ["{python}", "-c", "pass"]}]}))
            for scenario, expected in scenarios.items():
                with self.subTest(scenario=scenario):
                    args = ["dsh_mcp.py", "--repo", str(repo), "--checks", str(checks),
                            "--logs", str(base / "logs"), "--scenario", scenario]
                    with patch("sys.argv", args), patch("trajectory_offload.server.FastMCP.run", autospec=True) as start:
                        runpy.run_path(str(scripts / "dsh_mcp.py"), run_name="__main__")
                    server = start.call_args.args[0]
                    self.assertEqual({tool.name for tool in asyncio.run(server.list_tools())},
                                     {"get_repository_state", expected})
            args = ["dsh_review_mcp.py", "--repo", str(repo), "--checks", str(checks),
                    "--logs", str(base / "logs"), "--scenario", "impact"]
            with patch("sys.argv", args), patch("trajectory_offload.server.FastMCP.run", autospec=True) as start:
                runpy.run_path(str(scripts / "dsh_review_mcp.py"), run_name="__main__")
            self.assertEqual({tool.name for tool in asyncio.run(start.call_args.args[0].list_tools())},
                             {"get_repository_state", "investigate_repository"})

    def test_report_compares_main_usage_to_dsh_and_keeps_same_scenario_tasks(self):
        with tempfile.TemporaryDirectory(prefix="dsh-report-test-") as directory:
            root = Path(directory)
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(value), encoding="utf-8")
            schedule = [{"task": task, "repeat": 0, "arm": arm}
                        for task in ("first", "second") for arm in ("direct", "offload")]
            write(root / "manifest.json", {"tasks": [{"id": task, "scenario": "impact"} for task in ("first", "second")],
                "schedule": schedule, "repeats": 1, "design": "test", "dsh_version": "test", "model": "test", "repository": {}})
            for entry in schedule:
                child = 4 if entry["arm"] == "offload" else 0
                metrics = {role: {"total_tokens": tokens, "cached_input_tokens": 0, "model_calls": int(tokens > 0)}
                           for role, tokens in (("main", 10), ("subagent", child))}
                trial = {**entry, "success": True, "main_task_success": True, "end_to_end_seconds": 2, "grading_seconds": 1,
                    "agent_wall_seconds": 1, "protocol_conformant": True, "usage_complete": True, "metrics": metrics,
                    "total_tokens": 10 + child, "dsh_reported_total_tokens": 10, "offload_calls": int(child > 0), "request_models_valid": True}
                base = root / "runs" / f"{entry['task']}-r0-{entry['arm']}"
                write(base / "result.json", trial)
                write(base / "external-audit.json", {key: trial[key] for key in
                    ("success", "main_task_success", "end_to_end_seconds", "grading_seconds")})
                (base / "api-ledger.jsonl").write_text(json.dumps({"role": "main", "usage": {"input_tokens": 7}}) + "\n", encoding="utf-8")
            data = summarize(root)
            self.assertTrue(data["integrity"]["all_main_usage_crosschecks_match"])
            self.assertEqual(len(data["tasks"]), 2)
            self.assertEqual(data["scenarios"]["impact"]["offload"]["trials"], 2)
            self.assertEqual(data["arms"]["offload"]["mean_total_tokens"], 14)

    def test_new_tests_preserve_originals_but_weakened_assertions_do_not(self):
        original = "import pytest\ndef test_original():\n    assert value() == 7\n"
        addition = "\ndef test_added():\n    assert value() > 0\n"
        self.assertTrue(preserves_statements(original, original + addition))
        self.assertFalse(preserves_statements(original, original.replace("== 7", "!= 0") + addition))
        self.assertFalse(preserves_statements(original, "import pytest\n" + addition))

    def test_proxy_preserves_request_and_counts_cached_usage_once(self):
        usage = {"input_tokens": 19, "output_tokens": 5, "total_tokens": 24,
                 "input_tokens_details": {"cached_tokens": 13}}
        upstream = io.BytesIO(b'event: response.completed\n' + b'data: ' + json.dumps(
            {"type": "response.completed", "response": {"model": "deepseek-v4.1-flash", "usage": usage}}).encode() + b'\n\n')
        upstream.status = 200
        upstream.headers = {"Content-Type": "text/event-stream"}
        request = b'{"model":"deepseek-v4.1-flash","input":"unchanged request","stream":true}'
        with tempfile.TemporaryDirectory(prefix="dsh-ledger-test-") as directory:
            ledger = Ledger(Path(directory) / "ledger.jsonl", 100000)
            try:
                with patch("urllib.request.urlopen", return_value=upstream) as forward:
                    client = HTTPConnection("127.0.0.1", ledger.server.server_port, timeout=5)
                    client.request("POST", "/main/v1/responses", body=request, headers={"Content-Type": "application/json"})
                    reply = client.getresponse()
                    self.assertEqual(reply.status, 200)
                    self.assertIn(b"response.completed", reply.read())
                    client.close()
                    self.assertEqual(forward.call_args.args[0].data, request)
                    self.assertEqual(forward.call_args.args[0].full_url, "http://127.0.0.1:8788/v1/responses")
                self.assertEqual(len(ledger.rows), 1)
                self.assertEqual(ledger.rows[0]["usage"], usage)
                self.assertEqual(ledger.rows[0]["usage"]["total_tokens"], 24)
            finally:
                ledger.close()


if __name__ == "__main__": unittest.main()
