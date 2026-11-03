"""Correct the byte-level test-file gate without rerunning or selecting agents.

New test functions may be added to an existing file. Original AST statements
must remain intact; acceptance runs original tests in a fresh source overlay.
Raw result.json records are never rewritten.
"""

import ast
import json
from pathlib import Path
import shutil
import subprocess
import time

from dsh_study import BASE, PYTHON, command, python_env, write_json


def preserves_statements(original: str, candidate: str) -> bool:
    try:
        before = [ast.dump(node, include_attributes=False) for node in ast.parse(original).body]
        after = iter(ast.dump(node, include_attributes=False) for node in ast.parse(candidate).body)
        return all(any(value == expected for value in after) for expected in before)
    except SyntaxError:
        return False


def audit(root: Path, task):
    path = root / "external-audit.json"
    if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
    result = json.loads((root / "result.json").read_text(encoding="utf-8"))
    baseline, candidate = BASE / "cases" / task["id"], root / "workspace"
    start = time.monotonic()
    preserved = True
    changed_tests = []
    for original in (baseline / "tests").rglob("*"):
        if not original.is_file() or "__pycache__" in original.parts: continue
        relative = original.relative_to(baseline)
        other = candidate / relative
        if not other.is_file(): preserved = False; changed_tests.append(str(relative)); continue
        if other.read_bytes() != original.read_bytes():
            changed_tests.append(str(relative))
            if original.suffix != ".py" or not preserves_statements(original.read_text(encoding="utf-8-sig"), other.read_text(encoding="utf-8-sig")):
                preserved = False
    config_preserved = (candidate / "pyproject.toml").read_bytes() == (baseline / "pyproject.toml").read_bytes()
    if result["original_tests_unchanged"]:
        regression = json.loads((root / "regression.json").read_text(encoding="utf-8"))
        oracle = json.loads((root / "oracle.json").read_text(encoding="utf-8"))
        seconds = result["grading_seconds"]
        method = "original tests already byte-identical; reuse independent grading"
    else:
        workspace = root / "external-acceptance-workspace"
        if workspace.exists(): raise ValueError("partial external acceptance workspace already exists")
        shutil.copytree(baseline, workspace, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        for original in (workspace / "src").rglob("*"):
            if original.is_file() and not (candidate / original.relative_to(workspace)).exists(): original.unlink()
        for source in (candidate / "src").rglob("*"):
            if not source.is_file() or "__pycache__" in source.parts: continue
            if source.is_symlink() or not source.resolve().is_relative_to(candidate / "src"):
                raise ValueError("candidate source escaped its workspace")
            target = workspace / source.relative_to(candidate)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        regression = command([PYTHON, "-m", "pytest", "-q", "--color=no", "tests"], workspace, python_env(workspace))
        oracle = command([PYTHON, BASE / "oracles" / (task["id"] + ".py")], workspace, python_env(workspace))
        write_json(root / "external-regression.json", regression); write_json(root / "external-oracle.json", oracle)
        seconds = time.monotonic() - start
        method = "fresh original tests and configuration overlaid with candidate production source"
    behavior = regression["exit_code"] == 0 and oracle["exit_code"] == 0
    quality = behavior and preserved and config_preserved
    data = {"method": method, "original_test_statements_preserved": preserved, "configuration_preserved": config_preserved,
            "changed_test_files": changed_tests, "behavior_correct": behavior, "main_task_success": quality,
            "success": quality and result["protocol_conformant"] and not result["timed_out"] and result["final"] is not None,
            "grading_seconds": seconds,
            "end_to_end_seconds": result["end_to_end_seconds"] - result["grading_seconds"] + seconds,
            "regression_exit": regression["exit_code"], "oracle_exit": oracle["exit_code"],
            "raw_success": result["success"], "note": "Uniform correction of an overstrict byte gate, not an agent retry; raw records preserved."}
    write_json(path, data)
    return data


if __name__ == "__main__":
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["schedule"]:
        root = BASE / "runs" / f"{entry['task']}-r{entry['repeat']}-{entry['arm']}"
        if not (root / "result.json").exists(): continue
        task = next(task for task in manifest["tasks"] if task["id"] == entry["task"])
        data = audit(root, task)
        # Keep the original artifact; raw bytes avoid Windows newline translation.
        diff = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"], cwd=root / "workspace",
                              capture_output=True, check=True).stdout
        (root / "verified.patch").write_bytes(diff)
        print(json.dumps({"run": root.name, "success": data["success"], "raw_success": data["raw_success"],
                          "original_tests_preserved": data["original_test_statements_preserved"]}), flush=True)
