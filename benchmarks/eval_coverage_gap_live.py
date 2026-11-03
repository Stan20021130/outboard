"""Live benchmark evaluation for Coverage-Gap Edge-Case Augmentation against the Click repository."""
import difflib
import json
import os
from pathlib import Path
import time
import ast
from openai import OpenAI

from trajectory_offload.contracts import CoverageAugmentTask, ExecutionPolicy, CheckSpec, Settings, UncoveredBranch
from trajectory_offload.repository import Snapshot
from trajectory_offload.specialized import augment_coverage_edge_cases

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / ".trajectory/v14-opt-test-03/cases/regression"

def main():
    settings = Settings(
        model="deepseek-v4.1-flash",
        reasoning_effort="low",
        max_output_tokens=4000,
        max_total_tokens=100000,
        max_seconds=120,
        prefetch_context=True,
    )
    print(f"Loading snapshot from: {REPO} ...", flush=True)
    snapshot = Snapshot.capture(REPO, settings)
    print(f"Snapshot ready: {snapshot.snapshot_id[:16]}... (files: {len(snapshot.files)})", flush=True)

    # Define the coverage gap task on click/types.py
    task = CoverageAugmentTask(
        objective=(
            "Add edge-case coverage tests for click.IntRange when clamp=False and values fall outside "
            "the boundary (raising click.BadParameter), as well as open boundary limits."
        ),
        snapshot_id=snapshot.snapshot_id,
        target_path="src/click/types.py",
        test_file="tests/test_coverage_edge.py",
        branches=[
            UncoveredBranch(path="src/click/types.py", line=685, condition="if lt_min or gt_max:", target_branch="true"),
            UncoveredBranch(path="src/click/types.py", line=762, condition="if not open:", target_branch="false"),
        ],
        check_id="regression"
    )
    policy = ExecutionPolicy(checks=[
        CheckSpec(id="regression", argv=["{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"], timeout_seconds=60)
    ], protected_paths=["pyproject.toml"])

    client = OpenAI(base_url="http://127.0.0.1:8788/v1", api_key="local-dev")
    print("\nStarting augment_coverage_edge_cases with real model (deepseek-v4.1-flash) ...", flush=True)
    t0 = time.monotonic()
    result = augment_coverage_edge_cases(REPO, task, settings, policy, client=client)
    elapsed = time.monotonic() - t0

    print(f"\nCompleted in {elapsed:.2f}s! Status: {result['status']}, Verdict: {result['verdict']}", flush=True)
    print(f"Metrics: {json.dumps(result['metrics'], indent=2)}")

    print("\n=== Generated Test Patch ===")
    print(result.get("test_patch", ""))

    print("\n=== Verification Findings ===")
    print(json.dumps(result.get("verification", {}), indent=2))

    # Independent Oracle Grading
    patch_text = result.get("test_patch", "")
    assert result["status"] == "completed", f"Task did not complete cleanly: {result.get('diagnostics')}"
    assert result["verdict"] == "passed", f"Sandbox single-test did not pass: {result.get('diagnostics')}"
    assert result["verification"].get("tests_passed") is True, "Single test verification passed flag not True"
    assert "def test_" in patch_text, "No test functions generated"
    assert "IntRange" in patch_text or "BadParameter" in patch_text, "Domain assertions missing"

    # AST validation on generated test
    test_lines = [line[1:] for line in patch_text.splitlines() if line.startswith("+") and not line.startswith("+++")]
    test_code = "\n".join(test_lines)
    tree = ast.parse(test_code)
    test_funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    print(f"\nGenerated test functions ({len(test_funcs)}): {test_funcs}")
    assert len(test_funcs) >= 1, "At least one test function required"

    print("\nALL ORACLE CHECKS PASSED! Coverage-gap augmentation successfully validated.")

if __name__ == "__main__":
    main()
