"""Execution contracts exercised with actual Python processes and isolated source copies."""

from dataclasses import replace
import difflib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trajectory_offload.contracts import (CheckSpec, DiagnosisTask, ExecutionPolicy, InvestigationTask,
                                          Settings, VerificationTask)
from trajectory_offload.execution import ExecutionSession, execute_task
from trajectory_offload.patching import patched_files
from trajectory_offload.repository import RepositoryTools, Snapshot
from trajectory_offload.runtime import investigate
from trajectory_offload.server import create_server
from trajectory_offload.evaluation import profile_runs
from test_runtime import FakeClient, Item, response


SOURCE = "def value():\n    return 30\n"
REVIEW = {"supported": True, "unsupported_claims": [], "criteria_supported": [0], "notes": []}


def report():
    return {"status": "completed", "summary": "value unconditionally returns 30.",
            "findings": [{"claim": "value unconditionally returns 30.", "evidence": [
                {"path": "settings.py", "start_line": 1, "end_line": 2, "quote": "return 30"}]}],
            "relations": [], "related_tests": [], "unresolved_questions": [], "criteria_coverage": [
                {"criterion_index": 0, "status": "supported", "finding_indices": [0], "relation_indices": [],
                 "test_indices": [], "explanation": "The function body supplies the observed value."}]}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trajectory-v2-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "settings.py").write_text(SOURCE, encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_value.py").write_text(
            "import unittest\nfrom settings import value\nclass ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 7)\n", encoding="utf-8")
        self.settings = Settings(model="test-model", max_model_calls=5, log_dir=self.base / "logs", max_seconds=40,
                                 semantic_verification=True)
        self.snapshot = Snapshot.capture(self.root, self.settings)
        self.policy = ExecutionPolicy(checks=[CheckSpec(id="unit", argv=["{python}", "-m", "unittest", "discover", "-s", "tests"])])
        self.patch = "".join(difflib.unified_diff(SOURCE.splitlines(True), SOURCE.replace("30", "7").splitlines(True),
                                                fromfile="a/settings.py", tofile="b/settings.py"))

    def verification(self, **kwargs):
        return VerificationTask(objective="Verify value returns the required 7.", snapshot_id=self.snapshot.snapshot_id,
                                patch=self.patch, check_ids=["unit"], **kwargs)

    def diagnosis(self, **kwargs):
        return DiagnosisTask(objective="Explain the failing value assertion using source and run evidence.",
                             snapshot_id=self.snapshot.snapshot_id, check_ids=["unit"], failure_signature="30 != 7", **kwargs)

    def test_patch_runs_baseline_and_regression_without_modifying_original(self):
        client = FakeClient([])
        result = execute_task(self.root, self.verification(), self.settings, self.policy, client=client)
        self.assertEqual((result["status"], result["verdict"]), ("completed", "passed"))
        self.assertEqual([record["status"] for record in result["check_runs"]], ["failed", "passed"])
        self.assertEqual(result["metrics"]["process_calls"], 2)
        self.assertEqual(result["metrics"]["model_calls"], 0)
        self.assertTrue(result["verification"]["original_snapshot_unchanged"])
        self.assertEqual(Snapshot.capture(self.root, self.settings).snapshot_id, self.snapshot.snapshot_id)
        self.assertEqual(profile_runs(self.settings.log_dir)["run_count"], 1)

    def test_stale_snapshot_and_unknown_check_never_launch_process(self):
        stale = self.verification().model_copy(update={"snapshot_id": "0" * 64})
        unknown = self.verification().model_copy(update={"check_ids": ["shell"]})
        for task in (stale, unknown):
            result = execute_task(self.root, task, self.settings, self.policy, client=FakeClient([]))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["metrics"]["process_calls"], 0)

    def test_completed_verification_can_report_a_failing_patch(self):
        task = self.verification(analyze_failures=False).model_copy(update={"patch": self.patch.replace("+    return 7", "+    return 31")})
        result = execute_task(self.root, task, self.settings, self.policy, client=FakeClient([]))
        self.assertEqual((result["status"], result["verdict"]), ("completed", "failed"))
        self.assertEqual([record["status"] for record in result["check_runs"]], ["failed", "failed"])
        self.assertEqual(result["metrics"]["process_calls"], 2)

    def test_original_tests_and_path_traversal_cannot_be_patched(self):
        for target in ("../outside.py", "tests/test_value.py", ".codex/config.toml"):
            task = self.verification().model_copy(update={"patch": self.patch.replace("settings.py", target)})
            result = execute_task(self.root, task, self.settings, self.policy)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["metrics"]["process_calls"], 0)
        bad_context = self.verification().model_copy(update={"patch": self.patch.replace("-    return 30", "-    return 99")})
        self.assertEqual(execute_task(self.root, bad_context, self.settings, self.policy)["status"], "failed")

    def test_unified_patch_preserves_crlf_bom_and_supports_add_delete(self):
        raw = b"\xef\xbb\xbfdef value():\r\n    return 30\r\n"
        (self.root / "settings.py").write_bytes(raw)
        snapshot = Snapshot.capture(self.root, self.settings)
        changed = patched_files(snapshot, self.patch, ["tests"])
        self.assertEqual(changed["settings.py"], raw.replace(b"30", b"7"))
        addition = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+value = 1\n"
        self.assertEqual(patched_files(snapshot, addition, ["tests"])["new.py"], b"value = 1\n")
        deletion = "--- a/settings.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-def value():\n-    return 30\n"
        self.assertIsNone(patched_files(snapshot, deletion, ["tests"])["settings.py"])

    def test_timeout_output_limit_and_source_mutation_cannot_pass(self):
        checks = [CheckSpec(id="sleep", argv=["{python}", "-c", "import time; time.sleep(2)"], timeout_seconds=0.15),
                  CheckSpec(id="loud", argv=["{python}", "-c", "print('x' * 40000)"]),
                  CheckSpec(id="mutate", argv=["{python}", "-c", "from pathlib import Path; Path('settings.py').write_text('changed=1')"])]
        for check, expected in zip(checks, ("timeout", "output_limit", "source_mutated")):
            task = self.verification(analyze_failures=False).model_copy(update={"check_ids": [check.id]})
            result = execute_task(self.root, task, replace(self.settings, max_process_output_bytes=10000),
                                  ExecutionPolicy(checks=[check]), client=FakeClient([]))
            self.assertNotEqual(result["verdict"], "passed")
            self.assertEqual(result["check_runs"][0]["status"], expected)
            self.assertEqual(Snapshot.capture(self.root, self.settings).snapshot_id, self.snapshot.snapshot_id)

    def test_diagnosis_repeats_failure_and_can_choose_followup_check(self):
        call = Item(type="function_call", name="run_checks", call_id="followup-1", arguments='{"check_ids":["unit"]}')
        forged_stage = Item(type="function_call", name="run_checks", call_id="invalid-stage",
                            arguments='{"check_ids":["unit"],"stage":"baseline"}')
        client = FakeClient([response(calls=[forged_stage]), response(calls=[call]),
                             response(json.dumps(report())), response(json.dumps(REVIEW))])
        result = execute_task(self.root, self.diagnosis(), self.settings, self.policy, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["reproduction"], "reproduced")
        self.assertFalse(result["root_cause_confirmed"])
        self.assertEqual(result["metrics"]["process_calls"], 3)
        self.assertEqual(result["metrics"]["model_calls"], 4)
        self.assertEqual([record["stage"] for record in result["check_runs"]], ["reproduction", "reproduction", "followup"])
        review_input = json.loads(client.calls[-1]["input"][0]["content"])
        self.assertEqual(len(review_input["execution_evidence"]["checks"]), 3)
        self.assertNotIn("log_path", result["analysis"])
        self.assertEqual(profile_runs(self.settings.log_dir)["run_count"], 1)

    def test_different_failure_and_budget_do_not_become_confirmed_diagnosis(self):
        other = self.diagnosis().model_copy(update={"failure_signature": "not-the-error"})
        result = execute_task(self.root, other, self.settings, self.policy, client=FakeClient([]))
        self.assertEqual(result["reproduction"], "different_failure")
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["analysis"])
        limited = execute_task(self.root, self.diagnosis(), replace(self.settings, max_process_calls=1),
                               self.policy, client=FakeClient([]))
        self.assertEqual(limited["status"], "partial")
        self.assertEqual(limited["metrics"]["process_calls"], 1)

    def test_distinct_check_outcomes_are_not_misclassified_as_flakiness(self):
        policy = ExecutionPolicy(checks=[*self.policy.checks, CheckSpec(id="other", argv=["{python}", "-c", "pass"])])
        task = self.diagnosis().model_copy(update={"check_ids": ["unit", "other"]})
        client = FakeClient([response(json.dumps(report())), response(json.dumps(REVIEW))])
        result = execute_task(self.root, task, self.settings, policy, client=client)
        self.assertEqual(result["reproduction"], "reproduced")
        self.assertEqual(result["reproduction_by_check"], {"unit": "reproduced", "other": "not_reproduced"})
        self.assertEqual(result["metrics"]["process_calls"], 4)

    def test_runner_does_not_inherit_api_key(self):
        check = CheckSpec(id="env", argv=["{python}", "-c", "import os; assert 'OPENAI_API_KEY' not in os.environ"])
        task = self.verification().model_copy(update={"check_ids": ["env"]})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "must-not-leak"}):
            result = execute_task(self.root, task, self.settings, ExecutionPolicy(checks=[check]))
        self.assertEqual(result["verdict"], "passed")

    def test_source_change_after_passing_check_is_caught_by_final_gate(self):
        original = ExecutionSession.run_checks

        def change_after_check(session, check_ids, *, stage="followup"):
            records = original(session, check_ids, stage=stage)
            if stage == "patched":
                (session.workspace / "settings.py").write_text("changed_after_check = True\n", encoding="utf-8")
            return records

        with patch.object(ExecutionSession, "run_checks", change_after_check):
            result = execute_task(self.root, self.verification(), self.settings, self.policy, client=FakeClient([]))
        self.assertTrue(all(record["source_unchanged"] for record in result["check_runs"]))
        self.assertFalse(result["verification"]["execution_source_unchanged"])
        self.assertEqual((result["status"], result["verdict"]), ("partial", "inconclusive"))

    def test_impact_supplies_import_and_test_evidence_without_claiming_exhaustiveness(self):
        tools = RepositoryTools(self.snapshot, [])
        context = tools.impact_context(["settings.py"], ["value"])
        self.assertIn("tests/test_value.py", context["candidate_tests"])
        self.assertEqual(context["import_dependents"][0]["imports"], "settings.py")
        self.assertFalse(context["coverage_complete"])
        task = InvestigationTask(objective="Describe the observed use of value and its bounded static impact.",
                                 task_type="change_impact", changed_paths=["settings.py"], symbols=["value"])
        client = FakeClient([response(json.dumps(report())), response(json.dumps(REVIEW))])
        result = investigate(self.root, task, self.settings, client=client)
        self.assertEqual(result["status"], "completed")
        self.assertIn("tests/test_value.py", result["impact"]["candidate_tests"])
        self.assertFalse(result["impact"]["coverage_complete"])

    def test_execution_tools_are_absent_without_a_startup_policy(self):
        import asyncio
        tools = asyncio.run(create_server(self.root, self.settings).list_tools())
        self.assertEqual([tool.name for tool in tools],
                         ["get_repository_state", "investigate_repository", "cluster_test_failures"])


if __name__ == "__main__":
    unittest.main()
