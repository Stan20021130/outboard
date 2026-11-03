"""Benchmark evaluation runner for v0.5-lite offload arm."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

import dsh_acceptance as acceptance
import dsh_study as study
from trajectory_offload.contracts import Settings
from trajectory_offload.repository import Snapshot
from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT

PROJECT = Path(__file__).resolve().parent.parent
SOURCE_DATASET = PROJECT / ".trajectory/review-policy-optimization/dsh-v4/offload"
BASE = PROJECT / ".trajectory/v05-lite-evaluation"

study.BASE = acceptance.BASE = BASE
PYTHON = study.PYTHON
MODEL = "deepseek-v4.1-flash"

TASKS = [
    {
        "id": "env-prefix-impact",
        "scenario": "impact",
        "brief": (
            "Fix the regression where hyphenated auto_envvar_prefix values no longer pick up "
            "underscore-normalized environment variables. Consider the static impact on nested commands, "
            "command-line overrides and explicit envvar behavior. Preserve public APIs and existing tests. "
            "Deliver the working source change, run the reported regression and normal non-stress suite, "
            "and summarize affected behavior and validation."
        )
    },
    {
        "id": "default-map-none-impact",
        "scenario": "impact",
        "brief": (
            "Fix the regression where an explicitly present None in default_map loses its DEFAULT_MAP parameter source. "
            "Investigate source resolution and its static impact on nested command defaults, callable defaults, and command-line/environment precedence. "
            "Keep missing-key fallback and falsy defaults correct, preserve public APIs and existing tests. "
            "Deliver the source fix, run the reported regression and normal non-stress suite, and summarize affected behavior and validation."
        )
    }
]

SCHEDULE = [
    {"task": "env-prefix-impact", "repeat": 0, "arm": "offload"},
    {"task": "env-prefix-impact", "repeat": 1, "arm": "offload"},
    {"task": "default-map-none-impact", "repeat": 0, "arm": "offload"},
    {"task": "default-map-none-impact", "repeat": 1, "arm": "offload"},
]


def profile_v05_lite(home, workspace, ledger, task, arm, run_dir):
    if arm != "offload":
        raise ValueError("only the specialized offload arm is authorized")
    rows = [{"id": key, "disabled": True} for key in study.DISABLED]
    rows += [
        {"id": "agent-default-model", "config": {"provider": "study", "model": MODEL, "reasoningEffort": "low"}},
        {"id": "llm-pi-ai", "config": {"providers": {"study": {"apiKeyEnv": "OPENAI_API_KEY", "api": "openai-responses",
            "baseURL": ledger.url("main"), "models": [{"id": MODEL, "contextWindow": 131072, "maxTokens": 8000,
            "input": ["text"], "reasoningEfforts": {"low": "low"}}], "retryPolicy": {"mode": "normal", "maxRetries": 2}}}}},
        {"id": "approval", "config": {"policy": "never"}},
        {"id": "sandbox-policy", "config": {"mode": "danger-full-access", "workspaceRoot": str(workspace)}}
    ]
    common = (
        "You are a coding agent powered by the {{model}} model. Complete the user's task in the current checkout. "
        f"The prepared Python interpreter is {PYTHON.as_posix()}; use it for pytest, with --color=no. "
        "PYTHONPATH points to this checkout's src. Preserve all existing tests and project configuration; new regression tests are allowed. "
        "Do not inspect other checkouts, hidden grading artifacts, or the evaluation harness. Do not use network access or install packages. "
        "Do not create commits, invoke external LLMs or delegate to any other agent. Do not ask for clarification: finish the well-specified task. "
        "The only permitted offload is the task-level MCP executor mcp__trajectory__investigate_repository. "
        "This controlled evaluation requires using it at least once, then continuing the complete task yourself. "
        "Get the current snapshot and available check IDs with mcp__trajectory__get_repository_state. "
        "Before editing, call investigate_repository with task_type=change_impact. Supply public API symbols "
        "or candidate changed paths; a short native localization step is allowed. Ask for source-supported dependencies, "
        "candidate tests and scope limits. Do not request a repair or runtime execution from this read-only executor. "
        "Leave semantic_review at its default false; the experiment host controls the review policy. "
        "Preserve all required final validation regardless of the investigation's review status. "
        "\n\n### v0.5-Lite Protocol & Tiered Discipline:\n"
        "1. Lightweight Delegation: Pass known_sources with already-observed ranges (path, start_line, end_line, optional expected_quote). "
        "If specifying remaining_criteria, pass ZERO-BASED indices (e.g. 0, 1).\n"
        "2. Zero-Redundancy Handoff: Directly consume and apply the subagent's evidence table and findings. "
        "STRICTLY PROHIBITED: Do NOT re-grep, search, or read files already verified by the subagent; formulate the atomic edit immediately.\n"
        "3. Tiered Verification Discipline: Sandboxes only run isolated single-test differentials. In the main workspace, run the focused "
        "target test first (e.g. pytest tests/test_reported_regression.py), and run the full test suite (pytest -q --color=no tests) "
        "EXACTLY ONCE as the final gate check before delivery."
    )
    rows.append({
        "insert": [{
            "id": "trajectory-mcp",
            "name": "@deepseek-ai/dsh-mcp-client",
            "required": True,
            "config": {
                "serverName": "trajectory",
                "transport": "stdio",
                "command": sys.executable,
                "args": [
                    str(PROJECT / "benchmarks/dsh_review_mcp.py"),
                    "--repo", str(workspace),
                    "--checks", str(BASE / "policies" / (task["id"] + ".json")),
                    "--logs", str(run_dir / "subagent"),
                    "--scenario", task["scenario"]
                ],
                "cwd": str(PROJECT),
                "env": {"OPENAI_API_KEY": "local-dev", "OPENAI_BASE_URL": ledger.url("subagent")},
                "toolCallTimeoutMs": 185000,
                "failOnStartupError": True
            }
        }]
    })
    rows.append({"id": "system-prompt", "config": {"personaPrefix": common}})
    home.mkdir(parents=True, exist_ok=True)
    study.write_json(home / "cordis.patch.yml", rows)


study.profile = profile_v05_lite


def prepare():
    BASE.mkdir(parents=True, exist_ok=True)
    manifest_path = BASE / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    source_cases = SOURCE_DATASET / "cases"
    source_policies = SOURCE_DATASET / "policies"
    source_oracles = SOURCE_DATASET / "oracles"

    for folder, source_dir in (("cases", source_cases), ("policies", source_policies), ("oracles", source_oracles)):
        dest = BASE / folder
        if not dest.exists():
            shutil.copytree(source_dir, dest, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

    snapshots = {}
    for task in TASKS:
        case_dir = BASE / "cases" / task["id"]
        snap = Snapshot.capture(case_dir, Settings(model=MODEL))
        snapshots[task["id"]] = snap.snapshot_id

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dsh_version": "0.2.1-rc.5",
        "model": MODEL,
        "reasoning_effort": "low",
        "max_seconds": 300,
        "max_total_tokens": 500000,
        "max_output_tokens": 8000,
        "subagent_max_seconds": 150,
        "subagent_max_total_tokens": 200000,
        "runtime_fingerprint": IMPLEMENTATION_FINGERPRINT,
        "tasks": TASKS,
        "starting_snapshots": snapshots,
        "schedule": SCHEDULE
    }
    study.write_json(manifest_path, manifest)
    return manifest


def remove_dir(path):
    if not path.exists():
        return
    if os.name == "nt":
        subprocess.run(["powershell", "-Command", f"Remove-Item -Recurse -Force '{path}'"], check=False)
    else:
        shutil.rmtree(path, ignore_errors=True)
    if path.exists():
        time.sleep(1)
        if os.name == "nt":
            subprocess.run(["cmd", "/c", f"rmdir /s /q \"{path}\""], check=False)


def run_all(manifest, target_task=None, target_repeat=None, rerun=False):
    results = []
    schedule = manifest["schedule"]
    if target_task:
        schedule = [e for e in schedule if e["task"] == target_task]
    if target_repeat is not None:
        schedule = [e for e in schedule if e["repeat"] == target_repeat]

    for entry in schedule:
        run_name = f"{entry['task']}-r{entry['repeat']}-{entry['arm']}"
        run_dir = BASE / "runs" / run_name
        if rerun and run_dir.exists():
            print(f"Cleaning previous run directory for rerun: {run_dir}", flush=True)
            remove_dir(run_dir)

        print(f"\n========================================================", flush=True)
        print(f"Starting trial: task={entry['task']}, repeat={entry['repeat']}, arm={entry['arm']}", flush=True)
        print(f"========================================================", flush=True)
        result = study.run_trial(manifest, entry)
        folder = BASE / "runs" / result["run_id"]
        task = next(t for t in manifest["tasks"] if t["id"] == entry["task"])
        audit = acceptance.audit(folder, task)
        patch = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
                               cwd=folder / "workspace", capture_output=True, check=True).stdout
        (folder / "verified.patch").write_bytes(patch)
        applied = study.command(["git", "apply", "--check", str(folder / "verified.patch")],
                                BASE / "cases" / task["id"])
        study.write_json(folder / "patch-check.json", applied)
        trial_summary = {
            "run_id": result["run_id"],
            "success": audit["success"],
            "main_task_success": audit["main_task_success"],
            "total_tokens": result["total_tokens"],
            "main_tokens": result["metrics"]["main"]["total_tokens"],
            "subagent_tokens": result["metrics"]["subagent"]["total_tokens"],
            "agent_wall_seconds": result["agent_wall_seconds"],
            "offload_calls": result["offload_calls"],
            "patch_applies": applied["exit_code"] == 0,
            "original_tests_preserved": audit["original_test_statements_preserved"]
        }
        results.append(trial_summary)
        print(f"Trial completed: {json.dumps(trial_summary, indent=2)}", flush=True)
        time.sleep(3)
    return results


def summarize(manifest):
    trials = []
    for entry in manifest["schedule"]:
        run_id = f"{entry['task']}-r{entry['repeat']}-{entry['arm']}"
        res_file = BASE / "runs" / run_id / "result.json"
        audit_file = BASE / "runs" / run_id / "external-audit.json"
        if res_file.exists() and audit_file.exists():
            res = json.loads(res_file.read_text(encoding="utf-8"))
            audit = json.loads(audit_file.read_text(encoding="utf-8"))
            trials.append({
                "run_id": run_id,
                "task": entry["task"],
                "repeat": entry["repeat"],
                "success": audit["success"],
                "main_task_success": audit["main_task_success"],
                "total_tokens": res["total_tokens"],
                "main_tokens": res["metrics"]["main"]["total_tokens"],
                "subagent_tokens": res["metrics"]["subagent"]["total_tokens"],
                "cached_tokens": (res["metrics"]["main"]["cached_input_tokens"] + res["metrics"]["subagent"]["cached_input_tokens"]),
                "agent_wall_seconds": res["agent_wall_seconds"],
                "offload_calls": res["offload_calls"]
            })

    if not trials:
        print("No completed trials found.")
        return

    n = len(trials)
    successes = sum(t["success"] for t in trials)
    mean_tokens = statistics.mean(t["total_tokens"] for t in trials)
    mean_main_tokens = statistics.mean(t["main_tokens"] for t in trials)
    mean_sub_tokens = statistics.mean(t["subagent_tokens"] for t in trials)
    mean_seconds = statistics.mean(t["agent_wall_seconds"] for t in trials)

    summary = {
        "trials": n,
        "successes": successes,
        "success_rate": f"{successes / n * 100:.1f}%",
        "mean_total_tokens": round(mean_tokens, 2),
        "mean_main_tokens": round(mean_main_tokens, 2),
        "mean_subagent_tokens": round(mean_sub_tokens, 2),
        "mean_agent_wall_seconds": round(mean_seconds, 2),
        "trials_detail": trials
    }
    study.write_json(BASE / "summary.json", summary)

    print("\n" + "=" * 60)
    print("v0.5-LITE OFFLOAD ARM EVALUATION SUMMARY")
    print("=" * 60)
    print(f"Total Trials: {n} | Successes: {successes}/{n} ({summary['success_rate']})")
    print(f"Mean Total Tokens: {mean_tokens:,.0f} (Main: {mean_main_tokens:,.0f}, Subagent: {mean_sub_tokens:,.0f})")
    print(f"Mean Wall Seconds: {mean_seconds:.2f}s")
    print("\nIndividual Trials:")
    for t in trials:
        print(f"  {t['run_id']:35s} | Success: {str(t['success']):5s} | Tokens: {t['total_tokens']:7,d} | Seconds: {t['agent_wall_seconds']:6.2f}s | Offload: {t['offload_calls']}")
    print("=" * 60)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "summarize"), default="run", nargs="?")
    parser.add_argument("--task", choices=("env-prefix-impact", "default-map-none-impact"))
    parser.add_argument("--repeat", type=int, choices=(0, 1))
    parser.add_argument("--rerun", action="store_true", help="Clean existing trial directory before running")
    args = parser.parse_args()

    manifest = prepare()
    if args.action == "prepare":
        print(f"Prepared evaluation environment at {BASE}")
        return

    if args.action == "run":
        run_all(manifest, target_task=args.task, target_repeat=args.repeat, rerun=args.rerun)
        summarize(manifest)
    elif args.action == "summarize":
        summarize(manifest)


if __name__ == "__main__":
    main()
