"""Native-main / offload / reviewed-offload ablation with unchanged final acceptance.

Reuses the frozen Click tasks and the original DSH runner. All three conditions
run the same v0.4 backend; only the host's optional review policy differs.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import dsh_acceptance as acceptance
from dsh_report import summarize
import dsh_study as study
from trajectory_offload.contracts import Settings
from trajectory_offload.repository import Snapshot
from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT


PROJECT = Path(__file__).resolve().parent.parent
DATASET = PROJECT / ".trajectory/investigator-optimization/dsh-v3"
BASE = PROJECT / ".trajectory/review-policy-optimization/dsh-v4"
CONDITIONS = ("native", "offload", "reviewed")
DRIVERS = ("dsh_review_policy.py", "dsh_review_mcp.py", "dsh_study.py", "dsh_acceptance.py")
original_profile = study.profile
active_condition = None


def profile(home, workspace, ledger, task, arm, run_dir):
    original_profile(home, workspace, ledger, task, arm, run_dir)
    path = home / "cordis.patch.yml"
    rows = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        for plugin in row.get("insert", []):
            if plugin["id"] == "trajectory-mcp":
                plugin["config"]["args"][0] = str(PROJECT / "benchmarks/dsh_review_mcp.py")
                if active_condition == "reviewed":
                    plugin["config"]["args"].append("--semantic-review")
        if row.get("id") == "system-prompt" and arm == "offload":
            row["config"]["personaPrefix"] += (
                " Leave semantic_review at its default false; the experiment host controls the review policy. "
                "Preserve all required final validation regardless of the investigation's review status.")
    study.write_json(path, rows)


study.profile = profile


def hashes():
    return {name: hashlib.sha256((PROJECT / "benchmarks" / name).read_bytes()).hexdigest() for name in DRIVERS}


def prepare():
    path = BASE / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    previous = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    tasks = previous["tasks"]
    snapshots = {}
    for condition in CONDITIONS:
        for task in tasks:
            root = BASE / condition
            case = root / "cases" / task["id"]
            shutil.copytree(DATASET / "cases" / task["id"], case,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
            for folder, suffix in (("policies", ".json"), ("oracles", ".py")):
                target = root / folder / (task["id"] + suffix)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(DATASET / folder / target.name, target)
            snapshot = Snapshot.capture(case, Settings(model=study.MODEL)).snapshot_id
            snapshots.setdefault(task["id"], []).append(snapshot)
    if any(len(set(values)) != 1 for values in snapshots.values()):
        raise ValueError("conditions did not start from identical source snapshots")
    orders = (("native", "offload", "reviewed"), ("reviewed", "offload", "native"),
              ("offload", "native", "reviewed"), ("reviewed", "native", "offload"))
    schedule = [{"task": task["id"], "repeat": repeat, "condition": condition,
                 "arm": "direct" if condition == "native" else "offload"}
                for number, task in enumerate(tasks) for repeat in range(2)
                for condition in orders[number * 2 + repeat]]
    manifest = {**{key: previous[key] for key in ("model", "reasoning_effort", "dsh_version", "max_seconds", "max_total_tokens",
                 "max_output_tokens", "subagent_max_seconds", "subagent_max_total_tokens", "repository")},
                "runtime_fingerprint": IMPLEMENTATION_FINGERPRINT, "driver_hashes": hashes(), "tasks": tasks, "repeats": 2,
                "schedule": schedule, "conditions": list(CONDITIONS), "starting_snapshots": snapshots,
                "design": "same native DSH main versus specialized offload with or without mandatory independent review; no generic subagent",
                "acceptance": "identical original tests, config and hidden oracle; all attempts and fallback count",
                "review_control": "same tool description and parent instructions in both offload conditions; host enforces review in reviewed condition",
                "task_origin": "same two previously used controlled Click regressions, not new held-out tasks",
                "dataset_manifest_sha256": hashlib.sha256((DATASET / "manifest.json").read_bytes()).hexdigest()}
    study.write_json(path, manifest)
    for condition in CONDITIONS:
        study.write_json(BASE / condition / "manifest.json", {**manifest,
            "schedule": [entry for entry in schedule if entry["condition"] == condition]})
    return manifest


def check_frozen(manifest):
    if manifest["runtime_fingerprint"] != IMPLEMENTATION_FINGERPRINT or manifest["driver_hashes"] != hashes():
        raise ValueError("runtime or frozen driver changed; preserve this study and use a new directory")


def run(manifest):
    global active_condition
    for entry in manifest["schedule"]:
        check_frozen(manifest)
        active_condition = entry["condition"]
        study.BASE = acceptance.BASE = BASE / active_condition
        result = study.run_trial(manifest, entry)
        root = study.BASE / "runs" / result["run_id"]
        task = next(task for task in manifest["tasks"] if task["id"] == entry["task"])
        audit = acceptance.audit(root, task)
        patch = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"], cwd=root / "workspace", capture_output=True, check=True).stdout
        (root / "verified.patch").write_bytes(patch)
        applied = study.command(["git", "apply", "--check", str(root / "verified.patch")], study.BASE / "cases" / task["id"])
        study.write_json(root / "patch-check.json", applied)
        print(json.dumps({"condition": active_condition, "run": result["run_id"], "success": audit["success"],
                          "tokens": result["total_tokens"], "seconds": result["agent_wall_seconds"],
                          "offloads": result["offload_calls"], "patch_applies": applied["exit_code"] == 0}), flush=True)


def report(manifest):
    groups, rows, integrity, child_checks = {}, [], {}, []
    for condition in CONDITIONS:
        root = BASE / condition
        data = summarize(root)
        groups[condition] = data["arms"]["direct" if condition == "native" else "offload"]
        integrity[condition] = data["integrity"]
        study.write_json(root / "summary.json", data)
        for trial in data["trials"]:
            folder = root / "runs" / f"{trial['task']}-r{trial['repeat']}-{trial['arm']}"
            children = [entry["result"] for file in (folder / "subagent").glob("*.jsonl")
                        for entry in map(json.loads, file.read_text(encoding="utf-8").splitlines()) if entry["event"] == "result"]
            events = [json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            opted_in = any(event.get("type") == "tool_call" and event.get("tool", "").endswith("investigate_repository")
                           and isinstance(event.get("input"), dict) and event["input"].get("semantic_review") is True for event in events)
            policy_ok = not opted_in and all((child["verification"]["semantic_review_status"] == "not_requested")
                                           == (condition != "reviewed") for child in children)
            child_checks.append(sum(child["metrics"]["total_tokens"] for child in children) == trial["metrics"]["subagent"]["total_tokens"])
            within_budget = trial["total_tokens"] <= manifest["max_total_tokens"]
            rows.append({**trial, "condition": condition, "review_policy_conformant": policy_ok,
                         "within_global_token_budget": within_budget,
                         "success": trial["success"] and policy_ok and within_budget, "children": [
                             {"status": child["status"], "verification": child["verification"], "metrics": child["metrics"]} for child in children]})
    for condition, group in groups.items():
        if group is None: continue
        trials = [row for row in rows if row["condition"] == condition]
        group["successes"] = sum(row["success"] for row in trials)
        group["tokens_per_success"] = group["total_tokens"] / group["successes"] if group["usage_complete"] and group["successes"] else None
        group["mean_child_seconds"] = sum(child["metrics"]["elapsed_seconds"] for row in trials for child in row["children"]) / len(trials)
        group["mean_review_seconds"] = sum(child["metrics"]["phase_seconds"].get("verification", 0) for row in trials for child in row["children"]) / len(trials)
    lookup = {(row["condition"], row["task"], row["repeat"]): row for row in rows}
    comparisons = []
    for task in manifest["tasks"]:
        for repeat in range(2):
            for left, right in (("native", "offload"), ("reviewed", "offload")):
                a, b = lookup.get((left, task["id"], repeat)), lookup.get((right, task["id"], repeat))
                if a is None or b is None: continue
                comparisons.append({"task": task["id"], "repeat": repeat, "baseline": left, "candidate": right,
                                    "both_successful": a["success"] and b["success"],
                                    "token_delta": b["total_tokens"] - a["total_tokens"] if a["usage_complete"] and b["usage_complete"] else None,
                                    "seconds_delta": b["agent_wall_seconds"] - a["agent_wall_seconds"]})
    output = {"runtime_fingerprint": manifest["runtime_fingerprint"], "manifest_sha256": hashlib.sha256((BASE / "manifest.json").read_bytes()).hexdigest(),
              "completed": len(rows), "planned": len(manifest["schedule"]), "groups": groups, "integrity": integrity,
              "all_child_usage_crosschecks_match": all(child_checks), "all_review_policies_conformant": all(row["review_policy_conformant"] for row in rows),
              "comparisons": comparisons, "trials": rows, "actual_price_savings_established": False,
              "quality_noninferiority_established": False}
    study.write_json(BASE / "summary.json", output)
    print(json.dumps({key: value for key, value in output.items() if key not in {"trials", "comparisons"}}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "report"))
    args = parser.parse_args()
    manifest = prepare()
    if args.action == "run": run(manifest)
    elif args.action == "report": report(manifest)
    else: print(json.dumps({"conditions": CONDITIONS, "runs": len(manifest["schedule"]), "runtime": IMPLEMENTATION_FINGERPRINT}), flush=True)
