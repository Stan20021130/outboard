"""Real Codex baselines, paired evaluations, and offline trajectory cost profiles."""

from collections import Counter, defaultdict
from dataclasses import replace
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib

from pydantic import TypeAdapter

from .contracts import BenchmarkCase, InvestigationReport, InvestigationTask, Settings
from .repository import RepositoryTools, Snapshot
from .runtime import COMMON_POLICY, IMPLEMENTATION_FINGERPRINT, Run, _empty_report, investigate, report_errors, verify_support
from .server import TOOL_DESCRIPTION
from .workspace import copy_sources as _copy_sources, isolated_workspace


def _run_process(command: list[str], root: Path, prompt: str | None, timeout: float) -> subprocess.CompletedProcess:
    # A standalone CLI trial must not inherit the desktop app's tool pipe or thread identity.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    environment.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(root)})
    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                               errors="replace", start_new_session=os.name != "nt")
    try:
        out, err = process.communicate(prompt, timeout=max(0.1, timeout))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        out, err = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(command, process.returncode, out, err)


def _codex_command(root: Path, settings: Settings, executable: str, output: Path,
                   schema: Path | None, sandbox: str, offload_mode: str | None = None) -> list[str]:
    resolved = shutil.which(executable) or executable
    if os.name == "nt" and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
        raise ValueError("use --codex-executable with the actual codex.exe path, not a Windows batch wrapper")
    command = [resolved, "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
               "--skip-git-repo-check", "--json", "--color", "never", "-s", sandbox,
               "-C", str(root), "-m", settings.model, "-o", str(output)]
    if schema:
        command += ["--output-schema", str(schema)]
    overrides = {
        "approval_policy": "never", "model_reasoning_effort": settings.reasoning_effort,
        "model_provider": "trajectory_eval", "features.multi_agent": False,
        "features.apps": False, "features.shell_tool": True,
        "shell_environment_policy.set.PYTHONDONTWRITEBYTECODE": "1",
        "model_providers.trajectory_eval.name": "Trajectory evaluation",
        "model_providers.trajectory_eval.wire_api": "responses",
        "model_providers.trajectory_eval.base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "model_providers.trajectory_eval.env_key": "OPENAI_API_KEY",
    }
    if os.name == "nt":
        config = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
        if config.exists():
            windows = tomllib.loads(config.read_text(encoding="utf-8")).get("windows", {})
            if windows.get("sandbox"):
                # Preserve the host's configured sandbox implementation; ignoring user config
                # must not accidentally disable the native Windows command executor.
                overrides["windows.sandbox"] = windows["sandbox"]
    # Unknown model names otherwise inherit this CLI's code-mode-only fallback, which a
    # function-calling gateway cannot execute. Use its bundled direct-tool metadata as
    # a capability adapter, while keeping the requested inference model unchanged.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CODEX_")}
    catalog_result = subprocess.run([resolved, "debug", "models", "--bundled"], capture_output=True,
                                    text=True, encoding="utf-8", env=environment, timeout=15, check=True)
    catalog = json.loads(catalog_result.stdout)
    if settings.model not in {m["slug"] for m in catalog["models"]}:
        direct = next((m for m in catalog["models"] if not m.get("tool_mode")), None)
        if direct is None:
            raise ValueError("Codex has no bundled direct-tool metadata for this gateway model")
        direct = {**direct, "slug": settings.model, "display_name": settings.model,
                  "upgrade": None, "default_reasoning_level": settings.reasoning_effort,
                  "experimental_supported_tools": [], "supports_search_tool": False}
        catalog_path = output.parent / "gateway-models.json"
        catalog_path.write_text(json.dumps({"models": [direct]}, ensure_ascii=False), encoding="utf-8")
        overrides["model_catalog_json"] = str(catalog_path)
    if offload_mode:
        name = "mcp_servers.trajectory-investigator."
        overrides.update({
            name + "command": sys.executable,
            name + "args": ["-m", "trajectory_offload", "serve", "--repo", str(root), "--mode", offload_mode,
                            "--model", settings.model, "--reasoning", settings.reasoning_effort,
                            "--max-model-calls", str(settings.max_model_calls),
                            "--max-tool-calls", str(settings.max_tool_calls),
                            "--max-total-tokens", str(settings.max_total_tokens),
                            "--max-output-tokens", str(settings.max_output_tokens),
                            "--max-seconds", str(settings.max_seconds), "--log-dir", str(settings.log_dir)],
            name + "cwd": str(root), name + "env_vars": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
            name + "tool_timeout_sec": settings.max_seconds + 30,
            name + "startup_timeout_sec": 20, name + "required": True,
            name + "enabled_tools": ["investigate_repository"],
        })
        overrides[name + "args"] += ["--max-files", str(settings.max_files), "--max-file-bytes", str(settings.max_file_bytes),
                                      "--max-snapshot-bytes", str(settings.max_snapshot_bytes)]
        if not settings.prefetch_context:
            overrides[name + "args"].append("--no-prefetch")
        overrides[name + "args"].append("--semantic-verification" if settings.semantic_verification else "--no-semantic-verification")
    for key, value in overrides.items():
        command += ["-c", f"{key}={json.dumps(value)}"]
    return command + ["-"]


def _native_events(completed: subprocess.CompletedProcess, run: Run) -> list[dict]:
    events = []
    usage_seen = False
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        events.append(event)
        item_type = event.get("item", {}).get("type", "")
        if "reasoning" not in item_type:
            run.event("native_trace", data=event)
        if event.get("type") == "turn.completed" and event.get("usage"):
            usage = event["usage"]
            usage_seen = True
            input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            run.metrics["input_tokens"] += input_tokens
            run.metrics["output_tokens"] += output_tokens
            run.metrics["total_tokens"] += input_tokens + output_tokens
            run.metrics["cached_input_tokens"] += usage.get("cached_input_tokens", 0)
            run.metrics["reasoning_tokens"] += usage.get("reasoning_output_tokens", 0)
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") in {"command_execution", "mcp_tool_call", "web_search"}:
            run.metrics["tool_calls"] += 1
            run.event("native_tool", item=item)
        if event.get("type") in {"turn.completed", "turn.failed", "error"}:
            run.event("native_event", data=event)
    run.metrics["model_calls_complete"] = False
    run.metrics["native_model_calls"] = None  # Codex JSON does not expose each inference request.
    if not usage_seen:
        run.metrics["usage_complete"] = False
    if completed.stderr.strip():
        run.event("native_stderr", text=completed.stderr[-6000:])
    if "blocked by policy" in completed.stderr:
        run.metrics["executor_policy_blocked"] = True
    return events


def native_investigate(root: Path, task: InvestigationTask, settings: Settings, executable: str, client=None) -> dict:
    run = Run(settings, root, "native", task, client)
    snapshot = Snapshot.capture(root, settings)
    backend = RepositoryTools(snapshot, task.scope)
    errors, diagnostics = [], []
    report = _empty_report("native investigation not finished", failed=True)
    checked = passed = False
    structure_checked = False
    run.event("capsule", mode="native", capsule={"task": task.model_dump(), "snapshot_id": snapshot.snapshot_id,
                                                "model": settings.model, "reasoning_effort": settings.reasoning_effort})
    try:
        with isolated_workspace(run, "trajectory-native-") as parent:
            source = parent / "source"
            _copy_sources(snapshot, source, task.scope)
            schema, output = parent / "schema.json", parent / "report.json"
            schema.write_text(json.dumps(InvestigationReport.model_json_schema()), encoding="utf-8")
            native_policy = COMMON_POLICY.replace("using only the read-only tools", "using read-only operations through native tools")
            prompt = (native_policy + "\nPerform this investigation directly with your native tools. "
                      "Use exec_command to read local source with rg, Get-Content, or equivalent read-only shell commands. "
                      "Reading files through the terminal is allowed; executing the repository's programs is not. Do not delegate or use subagents. "
                      "Do not edit files or execute repository tests. Return ONLY the JSON report matching the output schema.\n"
                      + json.dumps(task.model_dump(), ensure_ascii=False)
                      + "\nFinal JSON schema: " + json.dumps(InvestigationReport.model_json_schema(), separators=(",", ":")))
            command = _codex_command(source, settings, executable, output, schema, "read-only")
            started = time.monotonic()
            completed = _run_process(command, source, prompt, settings.max_seconds - (time.monotonic() - run.started))
            run.metrics["phase_seconds"]["native_investigation"] = time.monotonic() - started
            _native_events(completed, run)
            if completed.returncode:
                run.event("native_error", returncode=completed.returncode, stderr=completed.stderr[-5000:])
                raise RuntimeError(f"native Codex exited {completed.returncode}; see run log")
            native_text = output.read_text(encoding="utf-8")
            run.event("model_final", text=native_text)
            report = InvestigationReport.model_validate_json(native_text)
            errors = report_errors(report, task, backend, require_observed=False)
            structure_checked = True
            if settings.semantic_verification and not errors and report.findings:
                review = verify_support(run, report, backend)
                checked = True
                passed = (review.supported and not review.unsupported_claims
                          and set(review.criteria_supported) == set(range(len(task.acceptance_criteria))))
                run.event("support_review", review=review.model_dump())
                if not passed:
                    diagnostics += review.unsupported_claims or ["semantic review did not support all criteria"]
    except Exception as exc:
        if isinstance(exc, subprocess.TimeoutExpired):
            _native_events(subprocess.CompletedProcess([], -1, exc.output or "", exc.stderr or ""), run)
        diagnostics.append(f"native execution error: {type(exc).__name__}")
        if isinstance(exc, subprocess.TimeoutExpired) or "native_model_calls" not in run.metrics:
            run.metrics["usage_complete"] = False
        run.metrics["model_calls_complete"] = False
        run.event("error", error_type=type(exc).__name__)
    unchanged = Snapshot.capture(root, settings).snapshot_id == snapshot.snapshot_id
    if run.metrics.get("executor_policy_blocked"):
        diagnostics.append("native executor was blocked by host policy; this is not a valid task-quality baseline")
    if report.status == "completed" and (errors or not unchanged or (settings.semantic_verification and not (checked and passed))):
        report.status = "partial"
    if report.status != "completed" and not report.unresolved_questions:
        report.unresolved_questions = errors + diagnostics or ["native investigation not verified"]
    run.metrics["elapsed_seconds"] = round(time.monotonic() - run.started, 4)
    result = {**report.model_dump(), "snapshot_id": snapshot.snapshot_id, "run_id": run.id, "mode": "native",
              "verification": {"structure_and_citations_valid": structure_checked and not errors,
                               "semantic_review_status": ("not_requested" if not settings.semantic_verification else
                                                          "passed" if checked and passed else "rejected" if checked else "incomplete"),
                               "semantic_support_checked": checked, "semantic_support_passed": passed,
                               "snapshot_unchanged": unchanged, "errors": errors},
              "metrics": run.metrics, "diagnostics": diagnostics, "log_path": str(run.log_path)}
    result["baseline_valid"] = not run.metrics.get("executor_policy_blocked", False)
    run.event("result", result=result)
    return result


def _test(command: list[str], root: Path, timeout: float) -> dict:
    command = [sys.executable if part == "{python}" else part for part in command]
    started = time.monotonic()
    try:
        result = _run_process(command, root, None, timeout)
        return {"passed": result.returncode == 0, "exit_code": result.returncode,
                "seconds": time.monotonic() - started, "output": (result.stdout + result.stderr)[-10000:]}
    except subprocess.TimeoutExpired:
        return {"passed": False, "exit_code": None, "seconds": time.monotonic() - started, "output": "test timeout"}


def repair_trial(root: Path, item: dict, settings: Settings, mode: str, executable: str) -> dict:
    task = InvestigationTask.model_validate(item["task"])
    run = Run(settings, root, mode, task)
    snapshot = Snapshot.capture(root, settings)
    diagnostics = []
    with isolated_workspace(run, "trajectory-repair-") as parent:
        workspace = parent / "source"
        _copy_sources(snapshot, workspace)
        workspace_root = workspace.resolve()
        precheck = _test(item["test_command"], workspace, min(30, settings.max_seconds))
        if precheck["passed"]:
            raise ValueError("repair fixture is already passing; cannot measure successful repair")
        child_dir = settings.log_dir / (run.id + "-children")
        child_settings = replace(settings, log_dir=child_dir)
        prompt = ("Complete this code repair using the repository and your tools. Keep the public API and tests intact. "
                  "Do not delegate to additional agents. Run the relevant tests and report the result.\n"
                  + item["repair_objective"])
        if mode != "native":
            prompt += ("\nYou may actively call investigate_repository for bounded code investigation. " + TOOL_DESCRIPTION
                       + " Supply acceptance questions and known leads; use evidence and unresolved gaps, then retain required repair tests.")
        output = parent / "repair-summary.txt"
        command = _codex_command(workspace, child_settings, executable, output, None, "workspace-write",
                                  offload_mode=None if mode == "native" else mode)
        returncode = None
        try:
            completed = _run_process(command, workspace, prompt,
                                     settings.max_seconds - (time.monotonic() - run.started))
            returncode = completed.returncode
            _native_events(completed, run)
            if returncode:
                run.event("native_error", returncode=returncode, stderr=completed.stderr[-5000:])
        except (subprocess.TimeoutExpired, OSError) as exc:
            diagnostics.append(type(exc).__name__)
            run.metrics["usage_complete"] = False
        child_results = []
        for log in sorted(child_dir.glob("*.jsonl")):
            for line in log.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event["event"] == "result":
                    child_results.append(event["result"])
        usage_keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens")
        run.metrics["main_agent_usage"] = {key: run.metrics[key] for key in usage_keys}
        run.metrics["subagent_usage"] = {key: sum(r["metrics"][key] for r in child_results) for key in usage_keys}
        for result in child_results:
            metrics = result["metrics"]
            for key in (*usage_keys, "model_calls", "tool_calls", "verification_model_calls"):
                run.metrics[key] += metrics[key]
            run.metrics["usage_complete"] &= metrics["usage_complete"]
        if workspace.resolve() != workspace_root:
            raise ValueError("isolated workspace root was replaced")
        after = Snapshot.capture(workspace, settings)
        changed = sorted(p for p in set(snapshot.files) | set(after.files) if after.files.get(p) != snapshot.files.get(p))
        protected = [p for p in set(snapshot.files) | set(after.files)
                     if p.startswith("tests/") or Path(p).name.startswith("test_") or Path(p).name == "conftest.py"]
        tests_unchanged = all(after.raw_files.get(p) == snapshot.raw_files.get(p) for p in protected)
        patch = "".join("".join(difflib.unified_diff(snapshot.files.get(p, "").splitlines(keepends=True),
                       after.files.get(p, "").splitlines(keepends=True), fromfile="a/" + p, tofile="b/" + p)) for p in changed)
        patch_path = settings.log_dir / f"{run.id}.patch"
        patch_path.write_text(patch, encoding="utf-8")
        # Restore all original tests before the external regression check; no credit for changing tests.
        for path in protected:
            if not (workspace / path).resolve().is_relative_to(workspace.resolve()):
                raise ValueError("test restoration escaped isolated workspace")
            if path not in snapshot.files:
                candidate = (workspace / path).resolve()
                if not candidate.is_relative_to(workspace.resolve()):
                    raise ValueError("generated test escaped isolated workspace")
                candidate.unlink(missing_ok=True)
                continue
            (workspace / path).parent.mkdir(parents=True, exist_ok=True)
            (workspace / path).write_bytes(snapshot.raw_files[path])
        postcheck = _test(item["test_command"], workspace, min(30, settings.max_seconds))
        original_unchanged = Snapshot.capture(root, settings).snapshot_id == snapshot.snapshot_id
        adoption_satisfied = not item.get("require_offload", False) or len(child_results) > 0
        if not adoption_satisfied:
            diagnostics.append("required investigator call did not occur; conformance is not established")
        success = returncode == 0 and postcheck["passed"] and tests_unchanged and original_unchanged and adoption_satisfied
        run.metrics["elapsed_seconds"] = round(time.monotonic() - run.started, 4)
        result = {"status": "completed" if success else "failed", "mode": mode, "run_id": run.id,
                  "snapshot_id": snapshot.snapshot_id, "task_success": success, "metrics": run.metrics,
                  "precheck": precheck, "postcheck": postcheck, "tests_unchanged": tests_unchanged,
                  "original_repository_unchanged": original_unchanged, "offload_calls": len(child_results),
                  "child_run_ids": [r["run_id"] for r in child_results], "diagnostics": diagnostics,
                  "changed_source_files": [p for p in changed if p not in protected],
                  "changed_test_files": [p for p in changed if p in protected], "patch_path": str(patch_path),
                  "log_path": str(run.log_path)}
        run.event("result", result=result)
        return result


def _price(metrics: dict, rates: dict | None):
    if rates is None or not metrics.get("usage_complete"):
        return None
    return ((metrics["input_tokens"] - metrics["cached_input_tokens"]) * rates["input"]
            + metrics["cached_input_tokens"] * rates["cached_input"]
            + metrics["output_tokens"] * rates["output"]) / 1_000_000


def evaluate_dataset(dataset: Path, output_dir: Path, settings: Settings, modes: list[str], level="subtask",
                     repeats=1, pricing: Path | None = None, executable="codex") -> dict:
    if repeats < 1 or len(set(modes)) != len(modes):
        raise ValueError("repeats must be positive and modes must be unique")
    cases = TypeAdapter(list[BenchmarkCase]).validate_json(dataset.read_text(encoding="utf-8-sig"))
    items = [case.model_dump() for case in cases]
    if not items or len({x["id"] for x in items}) != len(items):
        raise ValueError("dataset must contain unique task ids")
    rates = json.loads(pricing.read_text(encoding="utf-8")) if pricing else None
    if rates is not None and (not isinstance(rates, dict) or set(rates) != {"input", "cached_input", "output"}
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in rates.values())):
        raise ValueError("pricing must contain nonnegative input/cached_input/output rates per million tokens")
    if level == "repair" and any(not item["repair_objective"] or not item["test_command"]
            or any(not part for part in item["test_command"]) for item in items):
        raise ValueError("repair cases require repair_objective and a nonempty test_command argument array")
    output_dir = output_dir.resolve()
    roots = [(dataset.parent / item["repo"]).resolve(strict=True) for item in items]
    if any(output_dir.is_relative_to(root) for root in roots):
        raise ValueError("evaluation output must be outside every investigated repository")
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / "results.jsonl").exists():
        raise ValueError("results.jsonl already exists; choose a new output directory to keep trial sets reproducible")
    settings = replace(settings, log_dir=output_dir / "runs")
    trials = []
    for repeat in range(repeats):
        for index, (item, root) in enumerate(zip(items, roots)):
            offset = (index + repeat) % len(modes)
            ordered = modes[offset:] + modes[:offset]
            for mode in ordered:
                print(f"[{level}] {item['id']} repeat={repeat} mode={mode}", file=sys.stderr, flush=True)
                try:
                    task = InvestigationTask.model_validate(item["task"])
                    if level == "repair":
                        result = repair_trial(root, item, settings, mode, executable)
                    else:
                        result = (native_investigate(root, task, settings, executable) if mode == "native"
                                  else investigate(root, task, settings, mode))
                        cited = {e["path"] for key in ("findings", "relations", "related_tests")
                                 for entry in result[key] for e in entry["evidence"]}
                        expected = set(item.get("expected_files", []))
                        recall = len(cited & expected) / len(expected) if expected else None
                        result["reference_file_recall"] = recall
                        result["task_success"] = result["status"] == "completed" and (recall is None or recall == 1)
                    result["metrics"]["estimated_cost_usd"] = _price(result["metrics"], rates)
                except Exception as exc:
                    result = {"status": "failed", "mode": mode, "task_success": False,
                              "execution_error": type(exc).__name__, "diagnostics": [str(exc)]}
                row = {"task_id": item["id"], "repeat": repeat, "level": level, **result}
                trials.append(row)
                with (output_dir / "results.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    summaries = {}
    for mode in modes:
        rows = [r for r in trials if r["mode"] == mode]
        valid = [r for r in rows if "metrics" in r]
        success = sum(r["task_success"] for r in rows)
        costs = [r["metrics"].get("estimated_cost_usd") for r in valid]
        cost = sum(costs) if len(valid) == len(rows) and all(v is not None for v in costs) else None
        summaries[mode] = {"trials": len(rows), "successes": success, "success_rate": success / len(rows),
                           "total_tokens": sum(r["metrics"]["total_tokens"] for r in valid),
                           "mean_latency_seconds": sum(r["metrics"]["elapsed_seconds"] for r in valid) / len(valid) if valid else None,
                           "total_estimated_cost_usd": cost,
                           "estimated_cost_per_success_usd": cost / success if cost is not None and success else None,
                           "usage_complete": len(valid) == len(rows) and all(r["metrics"]["usage_complete"] for r in valid),
                           "offload_calls": sum(r.get("offload_calls", 0) for r in rows)}
    paired = {}
    for reference in ("native", "generic"):
        if reference not in modes or "specialized" not in modes:
            continue
        by_key = {(r["task_id"], r["repeat"], r["mode"]): r for r in trials}
        pairs = []
        for item in items:
            for repeat in range(repeats):
                a = by_key[(item["id"], repeat, reference)]
                c = by_key[(item["id"], repeat, "specialized")]
                if ("metrics" not in a or "metrics" not in c or not a.get("baseline_valid", True)
                        or a.get("snapshot_id") != c.get("snapshot_id")
                        or not a["metrics"]["usage_complete"] or not c["metrics"]["usage_complete"]):
                    continue
                pairs.append({"task_id": item["id"], "repeat": repeat,
                              "reference_success": a["task_success"], "specialized_success": c["task_success"],
                              "token_delta": a["metrics"]["total_tokens"] - c["metrics"]["total_tokens"],
                              "latency_delta_seconds": a["metrics"]["elapsed_seconds"] - c["metrics"]["elapsed_seconds"]})
        paired["specialized_vs_" + reference] = {"pair_count": len(pairs), "pairs": pairs,
            "mean_token_delta": sum(p["token_delta"] for p in pairs) / len(pairs) if pairs else None,
            "mean_latency_delta_seconds": sum(p["latency_delta_seconds"] for p in pairs) / len(pairs) if pairs else None,
            "quality_noninferiority_established": False}
    summary = {"level": level, "model": settings.model, "reasoning_effort": settings.reasoning_effort,
               "implementation_fingerprint": IMPLEMENTATION_FINGERPRINT,
               "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
               "limits": {"max_model_calls": settings.max_model_calls, "max_tool_calls": settings.max_tool_calls,
                          "max_total_tokens": settings.max_total_tokens, "max_seconds": settings.max_seconds},
               "api_base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
               "trial_count": len(trials), "modes": summaries,
               "execution_errors": sum("execution_error" in r or r["status"] == "failed" for r in trials),
               "pricing_configured": rates is not None,
               "paired_comparisons": paired,
               "interpretation": "Fixture smoke evaluation. Subtask recall is a proxy; this is not statistical evidence of quality equivalence or general cost reduction."}
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Evaluation report", "", f"Model: `{settings.model}`; reasoning: `{settings.reasoning_effort}`; level: `{level}`.", "",
             "| Mode | Success | Total tokens | Mean latency (s) | Cost / success (USD) | Offload calls |",
             "| --- | --- | --- | --- | --- | --- |"]
    for mode, data in summaries.items():
        latency = f"{data['mean_latency_seconds']:.2f}" if data["mean_latency_seconds"] is not None else "unknown"
        cost = f"{data['estimated_cost_per_success_usd']:.6f}" if data["estimated_cost_per_success_usd"] is not None else "unpriced"
        lines.append(f"| {mode} | {data['successes']}/{data['trials']} | {data['total_tokens']} | {latency} | {cost} | {data['offload_calls']} |")
    lines += ["", summary["interpretation"], "", "Raw trials: `results.jsonl`. Run-level tool and usage events: `runs/`.",
              "Native Codex inference-call counts and reasoning-token breakdown are not fully observable; those counters are lower bounds."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def profile_runs(log_dir: Path) -> dict:
    runs, patterns = [], Counter()
    for path in sorted(log_dir.rglob("*.jsonl")):
        if path.name == "results.jsonl":
            continue
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        finals = [event["result"] for event in events if event.get("event") == "result"]
        if not finals:
            continue
        result = finals[-1]
        tools = []
        for event in events:
            if event.get("event") == "tool":
                tools.append(event["name"])
            elif event.get("event") == "native_tool":
                item = event["item"]
                tools.append("native:" + item.get("tool", item["type"]))
            elif event.get("event") == "check_run":
                tools.append("check:" + event["check_id"])
        runs.append({"run_id": result["run_id"], "mode": result["mode"], "status": result["status"],
                     "total_tokens": result["metrics"]["total_tokens"],
                     "elapsed_seconds": result["metrics"]["elapsed_seconds"], "tool_sequence": tools})
        if result["status"] == "completed":
            for size in (2, 3):
                patterns.update(tuple(tools[i:i + size]) for i in range(len(tools) - size + 1))
    return {"run_count": len(runs), "highest_token_runs": sorted(runs, key=lambda r: -r["total_tokens"])[:20],
            "successful_recurring_tool_sequences": [{"sequence": list(p), "count": n} for p, n in patterns.most_common(20)],
            "note": "Repeated sequences are candidates for investigation, not automatically proven useful policies."}
