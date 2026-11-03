"""Exercise the v1.4 contracts with real isolated pytest processes and a fake model."""

from dataclasses import replace
import difflib
import json
from pathlib import Path
import tempfile
import unittest

from trajectory_offload.contracts import (CheckSpec, CoverageAugmentTask, ExecutionPolicy, FailureGroupingTask,
                                          RegressionTask, Settings, UncoveredBranch, VerificationTask)
from trajectory_offload.execution import execute_task
from trajectory_offload.repository import RepositoryTools, Snapshot
from trajectory_offload.specialized import (_source_changes, _test_setup, augment_coverage_edge_cases,
                                           cluster_test_failures, construct_regression_test)
from test_runtime import FakeClient, read_call, response


class SpecializedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trajectory-v14-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        (self.root / "tests").mkdir()
        self.source = "def value():\n    return 30\n"
        (self.root / "settings.py").write_text(self.source, encoding="utf-8")
        (self.root / "tests/test_existing.py").write_text("def test_existing():\n    assert True\n", encoding="utf-8")
        self.settings = Settings(model="test-model", log_dir=self.base / "logs", max_seconds=40,
                                 max_process_seconds=15, prefetch_context=False)
        self.snapshot = Snapshot.capture(self.root, self.settings)
        self.fix = "".join(difflib.unified_diff(self.source.splitlines(True),
            self.source.replace("30", "7").splitlines(True), fromfile="a/settings.py", tofile="b/settings.py"))
        self.task = RegressionTask(objective="Add a regression test", expected_behavior="value() returns 7",
            snapshot_id=self.snapshot.snapshot_id, source_patch=self.fix, test_file="tests/test_regression.py",
            check_id="regression")
        self.policy = ExecutionPolicy(checks=[CheckSpec(id="regression", argv=[
            "{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"])])

    def draft(self, source=None):
        source = source or ("from settings import value\ndef test_value():\n"
                            "    assert value() == 7, 'TRAJECTORY_REGRESSION'\n")
        return {"test_source": source, "scenarios": ["value returns 7"], "unresolved_questions": []}

    def generate(self, draft=None, task=None, policy=None):
        client = FakeClient([response(json.dumps(draft or self.draft()))])
        result = construct_regression_test(self.root, task or self.task, self.settings, policy or self.policy, client)
        return result, client

    def test_constructs_patch_and_checks_expected_failure_then_success(self):
        result, client = self.generate()
        self.assertEqual((result["status"], result["verdict"]), ("completed", "passed"))
        self.assertEqual([run["stage"] for run in result["check_runs"]], ["before_fix", "after_fix"])
        self.assertEqual(result["metrics"]["process_calls"], 2)
        self.assertEqual(result["metrics"]["model_calls"], 1)
        self.assertTrue(result["verification"]["differential_passed"])
        self.assertFalse(result["verification"]["semantic_support_checked"])
        self.assertTrue(result["verification"]["original_snapshot_unchanged"])
        self.assertFalse((self.root / "tests/test_regression.py").exists())
        self.assertEqual((self.root / "settings.py").read_text(), self.source)
        self.assertNotIn(self.fix, json.dumps(client.calls[0]["input"]))

    def test_constructs_and_verifies_using_exact_source_edits(self):
        task = RegressionTask(objective="Add regression test", expected_behavior="value returns 7",
            snapshot_id=self.snapshot.snapshot_id, test_file="tests/test_regression.py", check_id="regression",
            source_edits=[{"path": "settings.py", "old": "return 30", "new": "return 7"}])
        result, _ = self.generate(task=task)
        self.assertEqual((result["status"], result["verdict"]), ("completed", "passed"))
        self.assertEqual(result["metrics"]["process_calls"], 2)
        self.assertIn("+++ b/tests/test_regression.py", result["test_patch"])

    def test_wrong_assertion_or_collection_error_cannot_pass(self):
        for source in ("from settings import value\ndef test_value():\n    assert value() == 99, 'TRAJECTORY_REGRESSION'\n",
                       "import missing_package\ndef test_value():\n    assert False, 'TRAJECTORY_REGRESSION'\n",
                       "from settings import value\ndef test_value():\n    assert value() == 7\n"):
            with self.subTest(source=source):
                result, _ = self.generate(self.draft(source))
                self.assertEqual((result["status"], result["verdict"]), ("partial", "inconclusive"))
                self.assertFalse(result["verification"]["differential_passed"])

    def test_stale_snapshot_and_invalid_patch_never_execute(self):
        stale = self.task.model_copy(update={"snapshot_id": "0" * 64})
        result, client = self.generate(task=stale)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(client.calls), 0)
        result, _ = self.generate(self.draft("def invalid_python(\n"))
        self.assertEqual(result["metrics"]["process_calls"], 0)
        self.assertIn("not valid Python", result["diagnostics"][0])
        wrong_profile = ExecutionPolicy(checks=[CheckSpec(id="regression", argv=["{python}", "-c", "pass"])])
        result, client = self.generate(policy=wrong_profile)
        self.assertEqual(result["metrics"]["process_calls"], 0)
        self.assertEqual(len(client.calls), 0)

    def test_parameterized_regression_profile_is_not_a_general_patch_check(self):
        task = VerificationTask(objective="Verify a fix", snapshot_id=self.snapshot.snapshot_id,
                                patch=self.fix, check_ids=["regression"], analyze_failures=False)
        result = execute_task(self.root, task, self.settings, self.policy, client=FakeClient([]))
        self.assertEqual(result["metrics"]["process_calls"], 0)
        self.assertIn("parameterized regression checks", result["diagnostics"][0])

    def test_regression_agent_can_read_source_before_submitting(self):
        client = FakeClient([response(calls=[read_call()]), response(json.dumps(self.draft("def invalid_python(\n")))])
        result = construct_regression_test(self.root, self.task, self.settings, self.policy, client)
        self.assertEqual(result["metrics"]["model_calls"], 2)
        self.assertEqual(result["metrics"]["tool_calls"], 1)
        self.assertEqual(result["metrics"]["process_calls"], 0)
        self.assertIn("not valid Python", result["diagnostics"][0])

    def test_existing_test_cannot_be_modified_by_fix_or_test_draft(self):
        old = "def test_existing():\n    assert True\n"
        forbidden = "".join(difflib.unified_diff(old.splitlines(True), old.replace("True", "False").splitlines(True),
                                               fromfile="a/tests/test_existing.py", tofile="b/tests/test_existing.py"))
        result, client = self.generate(task=self.task.model_copy(update={"source_patch": forbidden}))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(client.calls), 0)
        result, _ = self.generate(self.draft(forbidden))
        self.assertEqual(result["metrics"]["process_calls"], 0)
        self.assertIn("not valid Python", result["diagnostics"][0])

    def test_source_edits_preserve_exact_bytes_and_reject_ambiguity(self):
        raw = b"\xef\xbb\xbfdef value():\r\n    return 30\r\n"
        (self.root / "settings.py").write_bytes(raw)
        snapshot = Snapshot.capture(self.root, self.settings)
        task = RegressionTask(objective="test", expected_behavior="value returns 7", snapshot_id=snapshot.snapshot_id,
            source_edits=[{"path": "settings.py", "old": "return 30", "new": "return 7"}],
            test_file="tests/test_regression.py", check_id="regression")
        self.assertEqual(_source_changes(snapshot, task, self.policy.protected_paths)["settings.py"], raw.replace(b"30", b"7"))
        for edit in ({"path": "settings.py", "old": " ", "new": "_"},
                     {"path": "tests/test_existing.py", "old": "True", "new": "False"},
                     {"path": "../escape.py", "old": "a", "new": "b"}):
            invalid = task.model_copy(update={"source_edits": [type(task.source_edits[0])(**edit)]})
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                _source_changes(snapshot, invalid, self.policy.protected_paths)
        overlap = task.model_copy(update={"source_edits": [task.source_edits[0],
            type(task.source_edits[0])(path="settings.py", old="30", new="7")]})
        with self.assertRaisesRegex(ValueError, "overlap"):
            _source_changes(snapshot, overlap, self.policy.protected_paths)

    def test_source_changes_protect_paths_case_insensitively(self):
        (self.root / "Config").mkdir()
        (self.root / "Config/settings.py").write_text("value = 30\n", encoding="utf-8")
        snapshot = Snapshot.capture(self.root, self.settings)
        task = RegressionTask(objective="test", expected_behavior="value returns 7", snapshot_id=snapshot.snapshot_id,
            source_edits=[{"path": "Config/settings.py", "old": "30", "new": "7"}],
            test_file="tests/test_regression.py", check_id="regression")
        with self.assertRaisesRegex(ValueError, "protected"):
            _source_changes(snapshot, task, ["config"])
        patch = "".join(difflib.unified_diff(["value = 30\n"], ["value = 7\n"],
                                            fromfile="a/Config/settings.py", tofile="b/Config/settings.py"))
        patched = RegressionTask(objective="test", expected_behavior="value returns 7", snapshot_id=snapshot.snapshot_id,
            source_patch=patch, test_file="tests/test_regression.py", check_id="regression")
        with self.assertRaisesRegex(ValueError, "protected"):
            _source_changes(snapshot, patched, ["config"])

    def test_final_submission_receives_compacted_observed_context(self):
        client = FakeClient([response(calls=[read_call()]), response(calls=[read_call()]),
                             response(json.dumps(self.draft("def invalid_python(\n")))])
        result = construct_regression_test(self.root, self.task, self.settings, self.policy, client)
        self.assertEqual(result["metrics"]["model_calls"], 3)
        final_input = client.calls[-1]["input"]
        self.assertEqual(len(final_input), 1)
        compiled = json.loads(final_input[0]["content"])
        self.assertEqual(len(compiled["gathered_evidence"]), 2)
        self.assertIn("return 30", compiled["gathered_evidence"][0]["result"][0]["source"])

    def test_prefetched_test_setup_respects_scope(self):
        (self.root / "tests/conftest.py").write_text("import pytest\n", encoding="utf-8")
        snapshot = Snapshot.capture(self.root, self.settings)
        context = {"excerpts": [{"path": "tests/test_existing.py"}]}
        included = _test_setup(RepositoryTools(snapshot, []), context)
        self.assertEqual([entry["path"] for entry in included], ["tests/test_existing.py", "tests/conftest.py"])
        self.assertEqual(_test_setup(RepositoryTools(snapshot, ["settings.py"]), context), [])

    def test_groups_identical_symptoms_without_calling_model(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "test_a", "kind": "failure", "message": "AssertionError: x"},
            {"test_id": "test_b", "kind": "failure", "message": "AssertionError: x"}])
        client = FakeClient([])
        result = cluster_test_failures(self.root, task, self.settings, client)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["groups"][0]["observation_ids"], ["F1", "F2"])
        self.assertEqual(result["metrics"]["model_calls"], 0)
        self.assertFalse(result["root_cause_confirmed"])

    def test_junit_relations_are_hypotheses_and_invalid_references_are_rejected(self):
        xml = ('<testsuite><testcase classname="A" name="one"><failure message="first">trace1</failure></testcase>'
               '<testcase classname="A" name="two"><error message="second">trace2</error></testcase></testsuite>')
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, junit_xml=xml)
        analysis = {"relations": [{"group_ids": ["G1", "G2"], "evidence_ids": ["F1", "F2"], "source_ids": [],
                                   "hypothesis": "May share setup."}], "unresolved_questions": []}
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual((result["status"], result["analysis_status"]), ("completed", "completed"))
        self.assertEqual(result["relations"][0]["status"], "hypothesis")
        self.assertEqual(len(result["observations"]), 2)
        analysis["relations"][0]["evidence_ids"] = ["F1", "F9"]
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual((result["status"], result["analysis_status"]), ("partial", "incomplete"))
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["relations"], [])

    def test_relationship_cannot_cite_an_unseen_group_member(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure", "message": "same", "traceback": "seen"},
            {"test_id": "b", "kind": "failure", "message": "same", "traceback": "not seen"},
            {"test_id": "c", "kind": "error", "message": "other", "traceback": "seen"}])
        analysis = {"relations": [{"group_ids": ["G1", "G2"], "evidence_ids": ["F2", "F3"], "source_ids": [],
                                   "hypothesis": "May share setup."}], "unresolved_questions": []}
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual((result["status"], result["analysis_status"]), ("partial", "incomplete"))
        self.assertEqual(result["relations"], [])

    def test_failure_analysis_includes_exact_bounded_source_and_checks_citations(self):
        (self.root / "settings.py").write_text(
            "def normalize(auto_envvar_prefix):\n    return auto_envvar_prefix.replace('-', '-')\n"
            "class IntRange:\n    def _clamp(self, bound, direction):\n        return bound - direction\n", encoding="utf-8")
        snapshot = Snapshot.capture(self.root, self.settings)
        task = FailureGroupingTask(snapshot_id=snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure", "message": "auto_envvar_prefix wrong A"},
            {"test_id": "b", "kind": "failure", "message": "auto_envvar_prefix wrong B"},
            {"test_id": "c", "kind": "failure", "message": "IntRange IntRange IntRange"}],
            include_source_evidence=True)
        analysis = {"relations": [{"group_ids": ["G1", "G2"], "evidence_ids": ["F1", "F2"],
                                   "source_ids": ["S1"], "hypothesis": "May share normalization."}], "unresolved_questions": []}
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source_context_status"], "available")
        self.assertTrue(any("return bound - direction" in source["source"] for source in result["source_evidence"]))
        analysis["relations"][0]["source_ids"] = ["S999"]
        bad = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual((bad["status"], bad["analysis_status"]), ("partial", "incomplete"))

    def test_failure_analysis_reports_when_source_evidence_is_skipped_for_budget(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure", "message": "first"},
            {"test_id": "b", "kind": "failure", "message": "second"}], include_source_evidence=True)
        analysis = {"relations": [], "unresolved_questions": []}
        result = cluster_test_failures(self.root, task, replace(self.settings, max_tool_calls=1),
                                       FakeClient([response(json.dumps(analysis))]))
        self.assertEqual((result["status"], result["source_context_status"]), ("partial", "skipped_budget"))
        self.assertTrue(result["unresolved_questions"])

    def test_failure_grouping_uses_logs_only_by_default(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure", "message": "value error A"},
            {"test_id": "b", "kind": "failure", "message": "value error B"}])
        analysis = {"relations": [], "unresolved_questions": []}
        client = FakeClient([response(json.dumps(analysis))])
        result = cluster_test_failures(self.root, task, self.settings, client)
        self.assertEqual((result["status"], result["source_context_status"]), ("completed", "not_requested"))
        self.assertEqual(result["source_evidence"], [])
        self.assertEqual(json.loads(client.calls[0]["input"][0]["content"])["source_evidence"], [])

    def test_missing_messages_are_not_merged_and_bad_xml_is_rejected(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure"}, {"test_id": "b", "kind": "failure"}],
            analyze_relationships=False)
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([]))
        self.assertEqual(len(result["groups"]), 2)
        bad = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id,
                                  junit_xml='<!DOCTYPE x [<!ENTITY y "z">]><testsuite/>')
        result = cluster_test_failures(self.root, bad, self.settings, FakeClient([]))
        self.assertEqual(result["status"], "failed")

    def test_long_failure_output_is_bounded_and_marked_truncated(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": "a", "kind": "failure", "message": "m" * 2000, "traceback": "t" * 9000},
            {"test_id": "b", "kind": "failure", "message": "m" * 2000, "traceback": "t" * 9000}])
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([]))
        self.assertEqual(len(result["groups"]), 1)
        observation = result["observations"][0]
        self.assertTrue(observation["message_truncated"])
        self.assertTrue(observation["traceback_truncated"])
        self.assertLess(len(observation["traceback"]), 900)
        self.assertEqual(len(observation["traceback_sha256"]), 64)
        distinct = task.model_copy(update={"failures": [task.failures[0],
            task.failures[1].model_copy(update={"message": "m" * 1999 + "n"})], "analyze_relationships": False})
        result = cluster_test_failures(self.root, distinct, self.settings, FakeClient([]))
        self.assertEqual(len(result["groups"]), 2)
        self.assertNotEqual(result["observations"][0]["message_sha256"], result["observations"][1]["message_sha256"])

    def test_junit_rejects_oversized_observations_instead_of_merging_truncated_messages(self):
        xml = '<testsuite><testcase name="a"><failure message="' + 'x' * 4001 + '"/></testcase></testsuite>'
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, junit_xml=xml)
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([]))
        self.assertEqual(result["status"], "failed")

    def test_unanalyzed_groups_are_reported_as_partial(self):
        task = FailureGroupingTask(snapshot_id=self.snapshot.snapshot_id, failures=[
            {"test_id": f"test_{i}", "kind": "failure", "message": f"message_{i}"} for i in range(21)])
        analysis = {"relations": [], "unresolved_questions": []}
        result = cluster_test_failures(self.root, task, self.settings, FakeClient([response(json.dumps(analysis))]))
        self.assertEqual(len(result["groups"]), 21)
        self.assertEqual((result["status"], result["analysis_status"]), ("partial", "partial"))
        self.assertIn("first 20", result["unresolved_questions"][0])

    def test_augment_coverage_edge_cases_success(self):
        code = "def compute(x):\n    if x > 10:\n        return x * 2\n    return x\n"
        (self.root / "settings.py").write_text(code, encoding="utf-8")
        snap = Snapshot.capture(self.root, self.settings)
        task = CoverageAugmentTask(
            objective="Augment coverage for compute() edge case x > 10",
            snapshot_id=snap.snapshot_id,
            target_path="settings.py",
            test_file="tests/test_cov_edge.py",
            check_id="regression"
        )
        test_src = "from settings import compute\ndef test_compute_large():\n    assert compute(20) == 40\n"
        draft = {"test_source": test_src, "scenarios": ["x > 10 triggers multiplier"], "unresolved_questions": []}
        client = FakeClient([response(json.dumps(draft))])
        result = augment_coverage_edge_cases(self.root, task, self.settings, self.policy, client)
        self.assertEqual((result["status"], result["verdict"]), ("completed", "passed"))
        self.assertTrue(result["verification"]["tests_passed"])
        self.assertIn("def test_compute_large", result["test_patch"])
        self.assertEqual(len(result["verification"]["covered_branches"]), 1)


if __name__ == "__main__":
    unittest.main()
