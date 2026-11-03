import json
from pathlib import Path
import subprocess
import tempfile
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from trajectory_offload.contracts import Evidence, InvestigationReport, InvestigationTask, KnownSource, Settings
from trajectory_offload.evaluation import _codex_command, _copy_sources, _native_events, _price, evaluate_dataset, isolated_workspace, profile_runs
from trajectory_offload.repository import RepositoryTools, Snapshot, build_repo_recon, safe_relative
from trajectory_offload.runtime import Run, investigate


SOURCE = "def timeout_value(config):\n    return config['timeout_seconds']\n"


def report(quote="return config['timeout_seconds']", claim="The function reads timeout_seconds from config."):
    return {"status": "completed", "summary": claim,
            "findings": [{"claim": claim, "evidence": [{"path": "settings.py", "start_line": 1, "end_line": 2, "quote": quote}]}],
            "relations": [], "related_tests": [],
            "criteria_coverage": [{"criterion_index": 0, "status": "supported", "finding_indices": [0],
                                   "relation_indices": [], "test_indices": [], "explanation": "Direct source evidence."}],
            "unresolved_questions": []}


class Item(SimpleNamespace):
    def model_dump(self, **kwargs):
        return vars(self)


class Usage(SimpleNamespace):
    def model_dump(self):
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens}


def response(text="", calls=(), status="completed"):
    return SimpleNamespace(id="response-test", model="test-model", status=status, output_text=text,
                           output=list(calls), usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15,
                               input_tokens_details=SimpleNamespace(cached_tokens=3),
                               output_tokens_details=SimpleNamespace(reasoning_tokens=2)))


def read_call():
    return Item(type="function_call", name="read_source_ranges", call_id="call-read",
                arguments=json.dumps({"ranges": [{"path": "settings.py", "start_line": 1, "end_line": 2}]}))


class FakeClient:
    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        step = next(self.steps)
        if isinstance(step, Exception):
            raise step
        return step


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trajectory-test-")
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "settings.py").write_text(SOURCE, encoding="utf-8")
        self.task = InvestigationTask(objective="Locate how timeout_seconds is consumed.")
        self.settings = Settings(model="test-model", max_model_calls=4, log_dir=self.base / "logs", prefetch_context=False,
                                 semantic_verification=True)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_loop_observes_then_verifies_and_accounts(self):
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(report())), response(json.dumps(review))])
        before = (self.root / "settings.py").read_bytes()
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["verification"]["semantic_support_passed"])
        self.assertEqual(result["metrics"]["total_tokens"], 45)
        self.assertEqual(result["metrics"]["reasoning_tokens"], 6)
        self.assertEqual(result["metrics"]["verification_model_calls"], 1)
        self.assertEqual(result["metrics"]["tool_calls"], 1)
        self.assertEqual((self.root / "settings.py").read_bytes(), before)
        self.assertTrue(Path(result["log_path"]).exists())
        self.assertTrue(all(call["model"] == "test-model" for call in client.calls))

    def test_function_submissions_work_with_no_text_format_support(self):
        final = Item(type="function_call", name="submit_investigation", call_id="submit",
                     arguments=json.dumps(report()))
        review = Item(type="function_call", name="submit_support_review", call_id="review",
                      arguments=json.dumps({"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}))
        client = FakeClient([response(calls=[read_call()]), response(calls=[final]), response(calls=[review])])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["tool_calls"], 3)
        self.assertEqual(client.calls[-1]["tool_choice"], {"type": "function", "name": "submit_support_review"})

    def test_source_range_clips_at_eof_and_exposes_actual_range(self):
        tools = RepositoryTools(Snapshot.capture(self.root, self.settings), [])
        excerpt = tools.excerpt("settings.py", 1, 80)
        self.assertEqual(excerpt["end_line"], 2)
        self.assertEqual(excerpt["source"], SOURCE.rstrip("\n"))

    def test_forged_quote_cannot_complete(self):
        settings = Settings(model="test-model", max_model_calls=3, log_dir=self.base / "logs")
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(report("return 999")))])
        result = investigate(self.root, self.task, settings, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["verification"]["structure_and_citations_valid"])
        self.assertTrue(any("quote" in e for e in result["verification"]["errors"]))

    def test_unobserved_but_real_citation_is_rejected(self):
        settings = Settings(model="test-model", max_model_calls=2, log_dir=self.base / "logs", prefetch_context=False)
        client = FakeClient([response(json.dumps(report()))])
        result = investigate(self.root, self.task, settings, client=client)
        self.assertNotEqual(result["status"], "completed")
        self.assertTrue(any("not observed" in e for e in result["verification"]["errors"]))

    def test_semantically_unsupported_claim_with_valid_quote_is_partial(self):
        review = {"supported": False, "unsupported_claims": ["No evidence that the bug was fixed."],
                  "criteria_supported": [], "notes": []}
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(report(claim="The runtime bug is fixed."))),
                             response(json.dumps(review))])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["verification"]["structure_and_citations_valid"])
        self.assertFalse(result["verification"]["semantic_support_passed"])

    def test_schema_retry_is_counted(self):
        settings = Settings(model="test-model", max_model_calls=5, log_dir=self.base / "logs", semantic_verification=True)
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = FakeClient([response(calls=[read_call()]), response("not JSON"), response(json.dumps(report())), response(json.dumps(review))])
        result = investigate(self.root, self.task, settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["model_calls"], 4)
        self.assertEqual(result["metrics"]["total_tokens"], 60)

    def test_unknown_write_tool_is_never_executed(self):
        call = Item(type="function_call", name="write_file", call_id="bad", arguments='{"path":"settings.py","text":"bad"}')
        settings = Settings(model="test-model", max_model_calls=2, semantic_verification=False, log_dir=self.base / "logs")
        client = FakeClient([response(calls=[call]), response(json.dumps(report()))])
        result = investigate(self.root, self.task, settings, client=client)
        # The blocked call never edits source; the later valid report can complete
        # from prefetched evidence without claiming independent review.
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["verification"]["semantic_review_status"], "not_requested")
        self.assertEqual((self.root / "settings.py").read_text(), SOURCE)
        self.assertIn("unavailable tool", Path(result["log_path"]).read_text())

    def test_insufficient_token_budget_makes_no_api_call(self):
        settings = Settings(model="test-model", max_total_tokens=100, log_dir=self.base / "logs")
        client = FakeClient([])
        result = investigate(self.root, self.task, settings, client=client)
        self.assertEqual(client.calls, [])
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["verification"]["structure_and_citations_valid"])

    def test_verifier_timeout_preserves_evidence_but_not_completed_status(self):
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(report())), TimeoutError()])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["findings"])
        self.assertFalse(result["metrics"]["usage_complete"])

    def test_late_successful_verification_cannot_bypass_wall_clock_limit(self):
        from dataclasses import replace
        clock = [0.0]
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        class LateClient(FakeClient):
            def create(inner, **kwargs):
                item = super().create(**kwargs)
                if len(inner.calls) == 3:
                    clock[0] = 2.0
                return item
        client = LateClient([response(calls=[read_call()]), response(json.dumps(report())), response(json.dumps(review))])
        with patch("trajectory_offload.runtime.time.monotonic", side_effect=lambda: clock[0]):
            result = investigate(self.root, self.task, replace(self.settings, max_seconds=1), client=client)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["verification"]["semantic_support_passed"])
        self.assertTrue(any("wall-clock" in reason for reason in result["unresolved_questions"]))

    def test_final_provider_usage_over_budget_is_not_completed(self):
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        final = response(json.dumps(review))
        final.usage = Usage(input_tokens=100000, output_tokens=5, total_tokens=100005)
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(report())), final])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["metrics"]["usage_complete"])
        self.assertTrue(any("token usage exceeded" in reason for reason in result["unresolved_questions"]))

    def test_repository_mutation_is_detected(self):
        class MutatingClient(FakeClient):
            def create(inner, **kwargs):
                result = super().create(**kwargs)
                if len(inner.calls) == 3:
                    (self.root / "settings.py").write_text(SOURCE + "# external change\n")
                return result
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = MutatingClient([response(calls=[read_call()]), response(json.dumps(report())), response(json.dumps(review))])
        result = investigate(self.root, self.task, self.settings, client=client)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["verification"]["snapshot_unchanged"])

    def test_logs_cannot_write_inside_investigated_repository(self):
        with self.assertRaises(ValueError):
            investigate(self.root, self.task, Settings(model="test-model", log_dir=self.root / "logs"), client=FakeClient([]))

    def test_scope_cannot_escape_or_read_unscoped_files(self):
        (self.root / "other.py").write_text("secret = 1\n")
        snapshot = Snapshot.capture(self.root, self.settings)
        tools = RepositoryTools(snapshot, ["settings.py"])
        with self.assertRaises(ValueError):
            tools.excerpt("other.py", 1, 1)
        for path in ("../other.py", "/etc/passwd", "C:/Windows/file", "..\\other.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_relative(path)

    def test_prefetch_follows_src_layout_imports_without_answer_files(self):
        package = self.root / "src" / "package"
        package.mkdir(parents=True)
        (package / "settings.py").write_text("from .helper import DEFAULT\nAPI_TIMEOUT = DEFAULT\n")
        (package / "helper.py").write_text("DEFAULT = 30\n")
        (self.root / "consumer.py").write_text("from package.settings import API_TIMEOUT\n")
        tools = RepositoryTools(Snapshot.capture(self.root, self.settings), [])
        seed = tools.prefetch_context("Investigate API_TIMEOUT propagation.", [])
        paths = {e["path"] for e in seed["excerpts"]}
        self.assertIn("src/package/helper.py", paths)
        self.assertIn("consumer.py", paths)
        self.assertLessEqual(sum(e["end_line"] - e["start_line"] + 1 for e in seed["excerpts"]), 360)

    def test_prefetch_supplies_real_observed_evidence_without_a_model_read_round(self):
        from dataclasses import replace
        settings = replace(self.settings, prefetch_context=True)
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = FakeClient([response(json.dumps(report())), response(json.dumps(review))])
        result = investigate(self.root, self.task, settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["metrics"]["model_calls"], 2)
        self.assertEqual(result["metrics"]["tool_calls"], 1)
        self.assertIn("context_construction", result["metrics"]["phase_seconds"])

    def test_git_snapshot_includes_uncommitted_and_untracked_but_not_ignored(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("ignored.py\n")
        (self.root / "ignored.py").write_text("ignored = True\n")
        first = Snapshot.capture(self.root, self.settings)
        self.assertIn("settings.py", first.files)
        self.assertNotIn("ignored.py", first.files)
        (self.root / "settings.py").write_text(SOURCE + "# uncommitted\n")
        second = Snapshot.capture(self.root, self.settings)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)

    def test_isolated_copy_preserves_bom_and_line_endings_exactly(self):
        original = b'\xef\xbb\xbfdef value():\r\n    return 30\r\n'
        (self.root / "settings.py").write_bytes(original)
        snapshot = Snapshot.capture(self.root, self.settings)
        copy = self.base / "copy"
        _copy_sources(snapshot, copy)
        self.assertEqual((copy / "settings.py").read_bytes(), original)
        self.assertEqual(Snapshot.capture(copy, self.settings).snapshot_id, snapshot.snapshot_id)

    def test_secret_files_are_not_exposed(self):
        (self.root / ".env").write_text("API_KEY=secret")
        (self.root / "auth.json").write_text('{"token":"secret"}')
        snapshot = Snapshot.capture(self.root, self.settings)
        self.assertNotIn(".env", snapshot.files)
        self.assertNotIn("auth.json", snapshot.files)

    def test_native_baseline_is_real_codex_and_uses_same_model(self):
        catalog = {"models": [{"slug": "test-model"}]}
        with patch("trajectory_offload.evaluation.subprocess.run", return_value=SimpleNamespace(stdout=json.dumps(catalog))):
            command = _codex_command(self.root, self.settings, "codex.exe", self.base / "out", None, "read-only")
        self.assertIn("exec", command)
        self.assertIn("--ignore-user-config", command)
        self.assertEqual(command[command.index("-m") + 1], "test-model")
        self.assertEqual(command[command.index("-s") + 1], "read-only")
        self.assertEqual(command[-1], "-")

    def test_repair_host_receives_the_requested_review_policy(self):
        from dataclasses import replace
        catalog = {"models": [{"slug": "test-model"}]}
        for review in (False, True):
            with self.subTest(review=review), patch("trajectory_offload.evaluation.subprocess.run",
                    return_value=SimpleNamespace(stdout=json.dumps(catalog))):
                command = _codex_command(self.root, replace(self.settings, semantic_verification=review),
                    "codex.exe", self.base / "out", None, "workspace-write", offload_mode="specialized")
                option = next(value for value in command if value.startswith("mcp_servers.trajectory-investigator.args="))
                arguments = json.loads(option.split("=", 1)[1])
                self.assertIn("--semantic-verification" if review else "--no-semantic-verification", arguments)

    def test_cost_does_not_count_reasoning_tokens_twice(self):
        metrics = {"usage_complete": True, "input_tokens": 1000, "cached_input_tokens": 400,
                   "output_tokens": 100, "reasoning_tokens": 50}
        self.assertAlmostEqual(_price(metrics, {"input": 2, "cached_input": 1, "output": 3}), 0.0019)
        self.assertIsNone(_price(metrics, None))
        metrics["usage_complete"] = False
        self.assertIsNone(_price(metrics, {"input": 2, "cached_input": 1, "output": 3}))

    def test_native_accounting_marks_missing_usage_and_counts_reasoning_as_output_detail(self):
        run = Run(self.settings, self.root, "native", self.task)
        event = {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20,
                                                       "output_tokens": 8, "reasoning_output_tokens": 3}}
        _native_events(subprocess.CompletedProcess([], 0, json.dumps(event), ""), run)
        self.assertEqual(run.metrics["total_tokens"], 108)
        self.assertEqual(run.metrics["reasoning_tokens"], 3)
        self.assertFalse(run.metrics["model_calls_complete"])
        missing = Run(self.settings, self.root, "native", self.task)
        _native_events(subprocess.CompletedProcess([], 1, "", "executor blocked by policy"), missing)
        self.assertFalse(missing.metrics["usage_complete"])
        self.assertTrue(missing.metrics["executor_policy_blocked"])

    def test_measured_token_anchor_avoids_recounting_replayed_ascii_as_bytes(self):
        settings = Settings(model="test-model", max_total_tokens=30000, max_output_tokens=2000, log_dir=self.base / "logs")
        replies = [response("{}"), response("{}")]
        for reply in replies:
            reply.usage = Usage(input_tokens=3000, output_tokens=1000, total_tokens=4000)
        client = FakeClient(replies)
        run = Run(settings, self.root, "specialized", self.task, client)
        history = [{"role": "user", "content": "x" * 26000}]
        run.request(history, "Return JSON", {"type": "object"})
        history.append({"role": "user", "content": "z" * 100})
        run.request(history, "Return JSON", {"type": "object"})
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(run.metrics["total_tokens"], 8000)

    def test_sandbox_cleanup_failure_does_not_discard_verified_result(self):
        run = Run(self.settings, self.root, "specialized", self.task)
        with patch("trajectory_offload.evaluation.shutil.rmtree", side_effect=PermissionError("sandbox-owned cache")):
            with isolated_workspace(run, "trajectory-cleanup-test-") as directory:
                (directory / "result.txt").write_text("verified")
        self.assertEqual(run.metrics["cleanup_retained_path"], str(directory))
        self.assertTrue(directory.exists())
        self.assertEqual(directory.resolve().parent, Path(tempfile.gettempdir()).resolve())
        shutil.rmtree(directory)

    def test_profile_includes_native_main_agent_actions(self):
        logs = self.base / "native-logs"
        logs.mkdir()
        events = [
            {"event": "native_tool", "item": {"type": "command_execution"}},
            {"event": "native_tool", "item": {"type": "command_execution"}},
            {"event": "result", "result": {"run_id": "native-test", "mode": "native", "status": "completed",
                                            "metrics": {"total_tokens": 100, "elapsed_seconds": 1}}},
        ]
        (logs / "native.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        result = profile_runs(logs)
        self.assertEqual(result["run_count"], 1)
        self.assertEqual(result["successful_recurring_tool_sequences"][0]["sequence"],
                         ["native:command_execution", "native:command_execution"])

    def test_known_sources_contract_validation_and_rejection(self):
        with self.assertRaises(ValueError):
            KnownSource(path="settings.py", start_line=5, end_line=3)

        with self.assertRaises(ValueError):
            KnownSource(path="settings.py", start_line=1, end_line=102)

        with self.assertRaises(ValueError):
            InvestigationTask(
                objective="Test limits",
                known_sources=[
                    KnownSource(path="settings.py", start_line=1, end_line=90),
                    KnownSource(path="settings.py", start_line=1, end_line=90),
                    KnownSource(path="settings.py", start_line=1, end_line=90),
                ]
            )

        with self.assertRaises(ValueError):
            InvestigationTask(objective="Test criteria", acceptance_criteria=["A", "B"], remaining_criteria=[0, 2])

        task_bad_snap = InvestigationTask(objective="Test", expected_snapshot_id="bad-snapshot-hash")
        res = investigate(self.root, task_bad_snap, self.settings, client=FakeClient([]))
        self.assertEqual(res["status"], "failed")
        self.assertIn("expected_snapshot_id does not match", res["diagnostics"][0])

        task_bad_path = InvestigationTask(objective="Test", known_sources=[KnownSource(path="nonexistent.py", start_line=1, end_line=2)])
        res = investigate(self.root, task_bad_path, self.settings, client=FakeClient([]))
        self.assertEqual(res["status"], "failed")
        self.assertIn("known source path is outside", res["diagnostics"][0])

        task_bad_quote = InvestigationTask(
            objective="Test",
            known_sources=[KnownSource(path="settings.py", start_line=1, end_line=2, expected_quote="wrong quote text")]
        )
        res = investigate(self.root, task_bad_quote, self.settings, client=FakeClient([]))
        self.assertEqual(res["status"], "failed")
        self.assertIn("known source expected quote does not match", res["diagnostics"][0])

    def test_known_sources_handoff_and_result_metadata(self):
        snap = Snapshot.capture(self.root, self.settings)
        task = InvestigationTask(
            objective="Locate how timeout_seconds is consumed.",
            acceptance_criteria=["Criterion 0: timeout_seconds"],
            remaining_criteria=[0],
            expected_snapshot_id=snap.snapshot_id,
            known_sources=[
                KnownSource(path="settings.py", start_line=1, end_line=2, expected_quote="return config['timeout_seconds']")
            ]
        )
        review = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}
        client = FakeClient([response(json.dumps(report())), response(json.dumps(review))])
        result = investigate(self.root, task, self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertIn("handoff", result)
        self.assertEqual(result["handoff"]["known_source_ranges"], 1)
        self.assertEqual(result["handoff"]["known_source_lines"], 2)
        self.assertEqual(result["handoff"]["remaining_criteria"], [0])

    def test_static_repo_reconnaissance(self):
        # Setup sample files in self.root
        pyproject = self.root / "pyproject.toml"
        pyproject.write_text("[project]\nname = 'demo'\ndependencies = ['pytest']\n", encoding="utf-8")
        src_dir = self.root / "src" / "demo"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "__init__.py").write_text("# init\n", encoding="utf-8")
        (src_dir / "core.py").write_text("def run(): pass\n", encoding="utf-8")
        test_dir = self.root / "tests"
        test_dir.mkdir(parents=True, exist_ok=True)
        (test_dir / "test_core.py").write_text("def test_run(): assert True\n", encoding="utf-8")

        snap = Snapshot.capture(self.root, self.settings)
        recon = build_repo_recon(snap)
        self.assertIn("pyproject.toml", recon["manifests"])
        self.assertIn("src/demo", recon["source_layout"]["source_roots"])
        self.assertIn("tests", recon["source_layout"]["test_dirs"])
        self.assertEqual(recon["source_layout"]["framework"], "pytest")
        self.assertIn("tests/test_core.py", recon["source_layout"]["sample_tests"])
        self.assertTrue(any(ep.endswith("core.py") for ep in recon["entry_points"]))
        self.assertGreaterEqual(recon["stats"]["python_files"], 3)


if __name__ == "__main__":
    unittest.main()

