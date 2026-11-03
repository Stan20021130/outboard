"""Frozen v0.3 Investigator versus native DSH, reusing the audited v0.2 harness.

The original study and its driver remain unchanged. The held-out default-map
task is prepared only after the optimized runtime has been frozen.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess

import dsh_acceptance as acceptance
import dsh_study as study
from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT


PROJECT = Path(__file__).resolve().parent.parent
OLD = PROJECT / ".trajectory/dsh-study"
BASE = PROJECT / ".trajectory/investigator-optimization/dsh-v3"
study.BASE = acceptance.BASE = BASE
# Keep the already prepared interpreter, packages and DSH version exactly fixed.


def check_runtime():
    frozen = json.loads((BASE.parent / "optimized.json").read_text())
    if frozen["runtime_fingerprint"] != IMPLEMENTATION_FINGERPRINT:
        raise ValueError("runtime changed after freeze; use a new versioned study")


def heldout_task():
    return {
        "id": "default-map-none-impact", "scenario": "impact", "path": "src/click/core.py",
        "old": "if default_map_value is not None or ctx._default_map_has(self.name):",
        "new": "if default_map_value is not None:",
        "brief": "Fix the regression where an explicitly present None in default_map loses its DEFAULT_MAP parameter source. "
                 "Investigate source resolution and its static impact on nested command defaults, callable defaults, and command-line/environment precedence. "
                 "Keep missing-key fallback and falsy defaults correct, preserve public APIs and existing tests. "
                 "Deliver the source fix, run the reported regression and normal non-stress suite, and summarize affected behavior and validation.",
        "target": ["tests/test_reported_regression.py", "tests/test_defaults.py", "tests/test_context.py", "tests/test_options.py"],
        "repro": "import click\nfrom click.testing import CliRunner\n"
                 "def test_reported_none_default_source():\n"
                 "    @click.command()\n    @click.option('--name', default='fallback')\n    @click.pass_context\n"
                 "    def cli(ctx, name): click.echo(ctx.get_parameter_source('name').name)\n"
                 "    result = CliRunner().invoke(cli, [], default_map={'name':None})\n"
                 "    assert result.exit_code == 0, result.output\n    assert result.output == 'DEFAULT_MAP\\n'\n",
        "oracle": "import click\nfrom click.testing import CliRunner\n"
                  "seen=[]\n@click.command()\n@click.option('--count',type=int,default=7,envvar='STUDY_COUNT')\n@click.pass_context\n"
                  "def cli(ctx,count): seen.append((count,ctx.get_parameter_source('count').name))\n"
                  "runner=CliRunner()\n"
                  "for value in (None,0,-1,8):\n"
                  "    r=runner.invoke(cli,[],default_map={'count':value},env={'STUDY_COUNT':None})\n"
                  "    assert r.exit_code==0,(r.output,r.exception)\n    assert seen[-1]==(value,'DEFAULT_MAP'),seen[-1]\n"
                  "for args,env,want in [(['--count','5'],{'STUDY_COUNT':'6'},(5,'COMMANDLINE')),"
                  "([],{'STUDY_COUNT':'6'},(6,'ENVIRONMENT'))]:\n"
                  "    r=runner.invoke(cli,args,default_map={'count':None},env=env)\n"
                  "    assert r.exit_code==0,(r.output,r.exception)\n    assert seen[-1]==want,seen[-1]\n"
                  "r=runner.invoke(cli,[],default_map={},env={'STUDY_COUNT':None})\n"
                  "assert r.exit_code==0\nassert seen[-1]==(7,'DEFAULT'),seen[-1]\n"
                  "calls=[]\ndef factory(): calls.append(1); return None\n"
                  "r=runner.invoke(cli,[],default_map={'count':factory},env={'STUDY_COUNT':None})\n"
                  "assert r.exit_code==0\nassert seen[-1]==(None,'DEFAULT_MAP')\nassert len(calls)==1,len(calls)\n"
                  "@click.group()\ndef group(): pass\n"
                  "@group.command()\n@click.option('--count',type=int,default=7)\n@click.pass_context\n"
                  "def child(ctx,count): seen.append((count,ctx.get_parameter_source('count').name))\n"
                  "r=runner.invoke(group,['child'],default_map={'child':{'count':None}})\n"
                  "assert r.exit_code==0,(r.output,r.exception)\nassert seen[-1]==(None,'DEFAULT_MAP'),seen[-1]\n",
    }


def prepare():
    check_runtime()
    path = BASE / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    original = json.loads((OLD / "manifest.json").read_text(encoding="utf-8"))
    old_task, new_task = original["tasks"][0], heldout_task()
    for task in (old_task, new_task):
        workspace = BASE / "cases" / task["id"]
        if task is old_task:
            shutil.copytree(OLD / "cases" / task["id"], workspace,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        else:
            shutil.copytree(study.UPSTREAM, workspace, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
            source = (workspace / task["path"]).read_text(encoding="utf-8")
            if source.count(task["old"]) != 1:
                raise ValueError("held-out mutation anchor is not unique")
            (workspace / task["path"]).write_text(source.replace(task["old"], task["new"]), encoding="utf-8", newline="\n")
            (workspace / "tests/test_reported_regression.py").write_text(task["repro"], encoding="utf-8", newline="\n")
            for argv in (["git", "init", "-q"], ["git", "-c", "core.autocrlf=false", "add", "."],
                         ["git", "-c", "user.name=TrajectoryStudy", "-c", "user.email=study@localhost", "commit", "-q", "-m", "Evaluation starting state"]):
                result = study.command(argv, workspace)
                if result["exit_code"]:
                    raise RuntimeError(result["stderr"])
        task["baseline_commit"] = study.command(["git", "rev-parse", "HEAD"], workspace)["stdout"].strip()
        policy = {"checks": [{"id": "target", "argv": [str(study.PYTHON), "-m", "pytest", "-q", "--color=no", *task["target"]], "timeout_seconds": 60},
                             {"id": "regression", "argv": [str(study.PYTHON), "-m", "pytest", "-q", "--color=no", "tests"], "timeout_seconds": 75}],
                  "protected_paths": ["tests", "pyproject.toml"]}
        study.write_json(BASE / "policies" / (task["id"] + ".json"), policy)
        oracle = BASE / "oracles" / (task["id"] + ".py")
        oracle.parent.mkdir(exist_ok=True)
        oracle.write_text("import os,pathlib,click\nassert pathlib.Path(click.__file__).resolve().is_relative_to(pathlib.Path(os.environ['STUDY_WORKSPACE']).resolve()/'src')\n" + task["oracle"], encoding="utf-8")
        task["baseline_target"] = study.command(policy["checks"][0]["argv"], workspace, study.python_env(workspace))
        task["baseline_oracle"] = study.command([study.PYTHON, oracle], workspace, study.python_env(workspace))
        golden = study.command([study.PYTHON, oracle], study.UPSTREAM, study.python_env(study.UPSTREAM))
        if task["baseline_target"]["exit_code"] == 0 or task["baseline_oracle"]["exit_code"] == 0 or golden["exit_code"] != 0:
            raise ValueError("task or oracle preflight failed: " + task["id"] + str(golden))
    manifest = {**{key: original[key] for key in ("model", "reasoning_effort", "dsh_version", "max_seconds", "max_total_tokens", "max_output_tokens",
                                                "subagent_max_seconds", "subagent_max_total_tokens", "repository")},
                "runtime_fingerprint": IMPLEMENTATION_FINGERPRINT, "repeats": 2, "tasks": [old_task, new_task],
                "design": "native main versus main required to use specialized Investigator; identical original harness prompts, same model/effort; no generic subagent",
                "development_task": old_task["id"], "heldout_task": new_task["id"],
                "acceptance": "original AST statements and config preserved; original test suite plus hidden oracle; audited before success aggregation",
                "task_origin": "controlled regression mutations in the same real Click checkout; not a cross-repository or historical-issue sample",
                "driver_hashes": {file: hashlib.sha256((PROJECT / "benchmarks" / file).read_bytes()).hexdigest()
                                  for file in ("dsh_study.py", "dsh_mcp.py", "dsh_acceptance.py", "dsh_investigator_v3.py")},
                "schedule": [{"task": task["id"], "repeat": repeat, "arm": arm}
                             for number, task in enumerate((old_task, new_task)) for repeat in range(2)
                             for arm in (("direct", "offload") if (number + repeat) % 2 == 0 else ("offload", "direct"))]}
    study.write_json(path, manifest)
    return manifest


def run(manifest):
    for file, expected in manifest["driver_hashes"].items():
        if hashlib.sha256((PROJECT / "benchmarks" / file).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen study driver changed: " + file)
    for entry in manifest["schedule"]:
        check_runtime()
        result = study.run_trial(manifest, entry)
        root = BASE / "runs" / result["run_id"]
        task = next(task for task in manifest["tasks"] if task["id"] == entry["task"])
        audit = acceptance.audit(root, task)
        diff = subprocess.run(["git", "diff", "--no-ext-diff", "--binary", "HEAD"], cwd=root / "workspace", capture_output=True, check=True).stdout
        (root / "verified.patch").write_bytes(diff)
        applied = study.command(["git", "apply", "--check", str(root / "verified.patch")], BASE / "cases" / task["id"])
        study.write_json(root / "patch-check.json", applied)
        print(json.dumps({"run": result["run_id"], "success": audit["success"], "total_tokens": result["total_tokens"],
                          "agent_wall_seconds": result["agent_wall_seconds"], "offload_calls": result["offload_calls"],
                          "usage_complete": result["usage_complete"], "patch_applies": applied["exit_code"] == 0}), flush=True)


def report(manifest):
    rows, grouped = [], {}
    for entry in manifest["schedule"]:
        root = BASE / "runs" / f"{entry['task']}-r{entry['repeat']}-{entry['arm']}"
        if not (root / "external-audit.json").exists():
            continue
        row = json.loads((root / "result.json").read_text())
        row.update(json.loads((root / "external-audit.json").read_text()))
        rows.append(row)
    for task in ["all", *[task["id"] for task in manifest["tasks"]]]:
        grouped[task] = {}
        for arm in ("direct", "offload"):
            subset = [row for row in rows if row["arm"] == arm and (task == "all" or row["task"] == task)]
            if subset:
                grouped[task][arm] = {"n": len(subset), "successes": sum(row["success"] for row in subset),
                    **{key: statistics.mean(row[key] for row in subset) for key in ("total_tokens", "agent_wall_seconds", "end_to_end_seconds", "offload_calls")},
                    "main_tokens": statistics.mean(row["metrics"]["main"]["total_tokens"] for row in subset),
                    "child_tokens": statistics.mean(row["metrics"]["subagent"]["total_tokens"] for row in subset)}
    output = {"runtime_fingerprint": manifest["runtime_fingerprint"], "completed": len(rows), "planned": len(manifest["schedule"]),
              "usage_complete": all(row["usage_complete"] for row in rows),
              "same_model_and_effort": all(row["request_models_valid"] for row in rows),
              "ledger_matches_dsh": all(row["dsh_reported_total_tokens"] == row["total_tokens"] for row in rows),
              "groups": grouped, "rows": rows}
    study.write_json(BASE / "summary.json", output)
    print(json.dumps({key: value for key, value in output.items() if key != "rows"}, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "report"))
    args = parser.parse_args()
    manifest = prepare()
    if args.action == "run": run(manifest)
    if args.action == "report": report(manifest)
    if args.action == "prepare": print(json.dumps({"tasks": [task["id"] for task in manifest["tasks"]], "runs": len(manifest["schedule"]), "runtime": IMPLEMENTATION_FINGERPRINT}), flush=True)
