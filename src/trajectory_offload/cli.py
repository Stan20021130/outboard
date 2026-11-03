"""CLI, stdio server entry point and benchmark commands."""

import argparse
import json
import os
from pathlib import Path
import sys

from .contracts import (DiagnosisTask, FailureGroupingTask, InvestigationTask, RegressionTask,
                        Settings, VerificationTask)


def add_runtime_options(parser):
    parser.add_argument("--model", default=os.getenv("TRAJECTORY_MODEL", ""), help="Must match the baseline/main-agent model")
    parser.add_argument("--reasoning", default=os.getenv("TRAJECTORY_REASONING_EFFORT", "low"))
    parser.add_argument("--max-model-calls", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=24)
    parser.add_argument("--max-total-tokens", type=int, default=80000)
    parser.add_argument("--max-output-tokens", type=int, default=5000)
    parser.add_argument("--max-seconds", type=float, default=240)
    parser.add_argument("--max-files", type=int, default=20000)
    parser.add_argument("--max-file-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-snapshot-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--max-process-calls", type=int, default=12)
    parser.add_argument("--max-process-seconds", type=float, default=60)
    parser.add_argument("--max-process-output-bytes", type=int, default=128 * 1024)
    parser.add_argument("--log-dir", type=Path, default=Settings.__dataclass_fields__["log_dir"].default)
    review = parser.add_mutually_exclusive_group()
    review.add_argument("--semantic-verification", "--semantic-review", dest="semantic_verification", action="store_true",
                        help="Require an additional independent model review of investigation claims")
    review.add_argument("--no-semantic-verification", dest="semantic_verification", action="store_false",
                        help="Skip independent model review (default); retain programmatic evidence checks")
    parser.set_defaults(semantic_verification=False)
    parser.add_argument("--no-prefetch", action="store_true", help="Ablate the specialized deterministic context-construction stage")


def settings_from_args(args):
    return Settings(model=args.model, reasoning_effort=args.reasoning,
                    max_model_calls=args.max_model_calls, max_tool_calls=args.max_tool_calls,
                    max_total_tokens=args.max_total_tokens, max_output_tokens=args.max_output_tokens,
                    max_seconds=args.max_seconds, log_dir=args.log_dir,
                    max_files=args.max_files, max_file_bytes=args.max_file_bytes,
                    max_snapshot_bytes=args.max_snapshot_bytes,
                    max_process_calls=args.max_process_calls, max_process_seconds=args.max_process_seconds,
                    max_process_output_bytes=args.max_process_output_bytes,
                    semantic_verification=args.semantic_verification, prefetch_context=not args.no_prefetch)


def write_json(value, output: Path | None = None):
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trajectory-offload")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("serve", "Expose task-level tools over local stdio MCP"),
                            ("investigate", "Run one read-only investigation"),
                            ("verify", "Verify a patch in a temporary source copy"),
                            ("diagnose", "Reproduce a failure and investigate its source"),
                            ("construct-regression", "Construct and differentially check a regression test"),
                            ("cluster-failures", "Group multiple test failures"),
                            ("snapshot", "Get the current supported-source snapshot without a model call")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("--repo", type=Path, required=True)
        sub.add_argument("--mode", choices=("generic", "specialized"), default="specialized")
        add_runtime_options(sub)
        if name in {"serve", "verify", "diagnose", "construct-regression"}:
            sub.add_argument("--checks", type=Path, required=name in {"verify", "diagnose", "construct-regression"},
                             help="Trusted startup JSON with predeclared check argument arrays")
        if name in {"investigate", "verify", "diagnose", "construct-regression", "cluster-failures"}:
            sub.add_argument("--task", type=Path, required=True, help="Task JSON, matching InvestigationTask")
            sub.add_argument("--output", type=Path)
    evaluate = commands.add_parser("evaluate", help="Run paired native / generic / specialized subtask or repair evaluation")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--output-dir", type=Path, required=True)
    evaluate.add_argument("--modes", nargs="+", choices=("native", "generic", "specialized"),
                          default=["native", "generic", "specialized"])
    evaluate.add_argument("--level", choices=("subtask", "repair"), default="subtask")
    evaluate.add_argument("--repeats", type=int, default=1)
    evaluate.add_argument("--pricing", type=Path, help="JSON with per-million-token input, cached_input and output rates")
    evaluate.add_argument("--codex-executable", default=os.getenv("CODEX_APP_RUNTIME_CLI_PATH", "codex"))
    add_runtime_options(evaluate)
    evaluate.set_defaults(max_total_tokens=500000)
    profile = commands.add_parser("profile", help="Summarize run costs and recurring successful tool sequences")
    profile.add_argument("--log-dir", type=Path, default=Settings.__dataclass_fields__["log_dir"].default)
    profile.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "profile":
            from .evaluation import profile_runs
            write_json(profile_runs(args.log_dir), args.output)
            return 0
        settings = settings_from_args(args)
        if args.command == "snapshot":
            from .repository import Snapshot
            snapshot = Snapshot.capture(args.repo, settings)
            write_json({"snapshot_id": snapshot.snapshot_id, "source_file_count": len(snapshot.files)})
            return 0
        if args.command != "cluster-failures" and not os.getenv("OPENAI_API_KEY"):
            raise ValueError("set OPENAI_API_KEY in the environment (the local development gateway accepts a placeholder)")
        if args.command == "serve":
            from .server import create_server
            from .execution import load_policy
            policy = load_policy(args.checks) if args.checks else None
            create_server(args.repo, settings, args.mode, policy=policy).run(transport="stdio")
            return 0
        if args.command in {"investigate", "verify", "diagnose", "construct-regression", "cluster-failures"}:
            from .runtime import investigate
            if args.output and args.output.resolve().is_relative_to(args.repo.resolve()):
                raise ValueError("output must be outside the read-only investigated repository")
            contract = {"investigate": InvestigationTask, "verify": VerificationTask, "diagnose": DiagnosisTask,
                        "construct-regression": RegressionTask, "cluster-failures": FailureGroupingTask}[args.command]
            task = contract.model_validate_json(args.task.read_text(encoding="utf-8-sig"))
            if args.command == "investigate":
                result = investigate(args.repo, task, settings, args.mode)
            elif args.command == "cluster-failures":
                from .specialized import cluster_test_failures
                result = cluster_test_failures(args.repo, task, settings)
            elif args.command == "construct-regression":
                from .execution import load_policy
                from .specialized import construct_regression_test
                result = construct_regression_test(args.repo, task, settings, load_policy(args.checks))
            else:
                from .execution import execute_task, load_policy
                result = execute_task(args.repo, task, settings, load_policy(args.checks), args.mode)
            write_json(result, args.output)
            return 0 if result["status"] == "completed" and result.get("verdict", "passed") not in {"failed", "inconclusive"} else 2
        from .evaluation import evaluate_dataset
        summary = evaluate_dataset(args.dataset, args.output_dir, settings, args.modes, args.level,
                                   args.repeats, args.pricing, args.codex_executable)
        write_json(summary)
        return 0 if summary["execution_errors"] == 0 else 2
    except (ValueError, OSError) as exc:
        print(f"trajectory-offload: {exc}", file=sys.stderr)
        return 1

