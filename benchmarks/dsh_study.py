"""Paired, real-DSH main-agent versus specialized-offload experiments.

The HTTP ledger forwards requests without rewriting their bodies. It records
provider usage for both parent and child, independently of harness accounting.
"""

import argparse
from datetime import datetime, timezone
import difflib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT


PROJECT = Path(__file__).resolve().parent.parent
BASE = PROJECT / ".trajectory/dsh-study"
PYTHON = BASE / "python-env/Scripts/python.exe"
DSH = BASE / "tooling/node_modules/@deepseek-ai/dsh/lib/bin.js"
UPSTREAM = BASE / "repos/click-upstream"
MODEL = "deepseek-v4.1-flash"
DISABLED = ("tool-subagent", "tool-subagent-fork", "tool-subagent-control", "tool-subagent-list-agents",
            "tool-workflow", "session-title-llm", "session-log-deepseek", "plugin-package-inventory-deepseek",
            "session-telemetry-otel", "tool-plugin-manager", "plugin-manager", "tool-web", "llm-deepseek",
            "llm-deepseek-account", "skill-filesystem", "tool-skill")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def command(argv, cwd, env=None, timeout=120):
    completed = subprocess.run([str(value) for value in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return {"exit_code": completed.returncode, "stdout": completed.stdout.decode("utf-8", "replace"),
            "stderr": completed.stderr.decode("utf-8", "replace")}


def python_env(workspace):
    env = {key: value for key, value in os.environ.items() if not key.startswith("CODEX_")}
    env.update(PYTHONPATH=str(workspace / "src"), PYTHONDONTWRITEBYTECODE="1", PY_COLORS="0",
               STUDY_WORKSPACE=str(workspace))
    return env


def digest_files(workspace, paths):
    return {path: hashlib.sha256((workspace / path).read_bytes()).hexdigest() for path in paths}


def prepare():
    """Freeze tasks and their hidden acceptance checks before formal A/B runs."""
    manifest_path = BASE / "manifest.json"
    if manifest_path.exists():
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    code = (UPSTREAM / "src/click/types.py").read_text(encoding="utf-8")
    core = (UPSTREAM / "src/click/core.py").read_text(encoding="utf-8")
    tasks = [
        {"id": "env-prefix-impact", "scenario": "impact", "path": "src/click/core.py",
         "old": "auto_envvar_prefix = auto_envvar_prefix.replace(\"-\", \"_\")", "new": "auto_envvar_prefix = auto_envvar_prefix.replace(\"-\", \"-\")",
         "target": ["tests/test_reported_regression.py", "tests/test_options.py", "tests/test_arguments.py", "tests/test_context.py"],
         "brief": "Fix the regression where hyphenated auto_envvar_prefix values no longer pick up underscore-normalized environment variables. "
                  "Consider the static impact on nested commands, command-line overrides and explicit envvar behavior. "
                  "Preserve public APIs and existing tests. Deliver the working source change, run the reported regression and normal non-stress suite, "
                  "and summarize affected behavior and validation.",
         "repro": "import click\nfrom click.testing import CliRunner\ndef test_reported_env_prefix():\n"
                  "    @click.command()\n    @click.option('--name')\n    def cli(name): click.echo(name)\n"
                  "    result = CliRunner().invoke(cli, [], auto_envvar_prefix='my-app', env={'MY_APP_NAME':'Ada'})\n"
                  "    assert result.exit_code == 0\n    assert result.output == 'Ada\\n'\n",
         "oracle": "from click.testing import CliRunner\n"
                   "for prefix in ('app','APP','my-app'):\n"
                   "    @click.group()\n    def cli(): pass\n"
                   "    @cli.command('sub-task')\n    @click.option('--number', type=int, default=3)\n"
                   "    def sub_task(number): click.echo(number)\n"
                   "    key=prefix.upper().replace('-','_')+'_SUB_TASK_NUMBER'\n"
                   "    runner=CliRunner()\n    result=runner.invoke(cli,['sub-task'],auto_envvar_prefix=prefix,env={key:'7'})\n"
                   "    assert result.exit_code==0, result.output\n    assert result.output=='7\\n', result.output\n"
                   "    result=runner.invoke(cli,['sub-task','--number','9'],auto_envvar_prefix=prefix,env={key:'7'})\n"
                   "    assert result.output=='9\\n', result.output\n"},
        {"id": "open-range-diagnosis", "scenario": "diagnosis", "path": "src/click/types.py",
         "old": "return bound + dir", "new": "return bound - dir",
         "target": ["tests/test_reported_regression.py", "tests/test_types/test_IntRange.py", "tests/test_types/test_FloatRange.py"],
         "brief": "Diagnose and fix the regression in integer range clamping with open endpoints. The reported regression and integer range tests fail. "
                  "Keep closed bounds, non-clamping validation errors and FloatRange restrictions unchanged. "
                  "Preserve public APIs and existing tests. Deliver a source fix, run the reported regression and normal non-stress suite, "
                  "and distinguish observed failures from diagnostic assumptions.",
         "repro": "import click\ndef test_reported_open_range():\n"
                  "    assert click.IntRange(0,5,min_open=True,clamp=True).convert('0',None,None)==1\n",
         "oracle": "for value in (-100,-2,-1,0,1,2,3,100):\n"
                   "    actual=click.IntRange(-2,3,min_open=True,max_open=True,clamp=True).convert(str(value),None,None)\n"
                   "    assert actual==max(-1,min(value,2)), (value,actual)\n"
                   "assert click.IntRange(0,5,clamp=True).convert('-1',None,None)==0\n"
                   "try: click.IntRange(0,5,min_open=True).convert('0',None,None)\n"
                   "except click.BadParameter: pass\nelse: raise AssertionError('open bound accepted without clamp')\n"
                   "try: click.FloatRange(0,5,min_open=True,clamp=True)\n"
                   "except TypeError: pass\nelse: raise AssertionError('FloatRange restriction removed')\n"},
        {"id": "unicode-choice-verification", "scenario": "verification", "path": "src/click/types.py",
         "old": "normed_value = normed_value.casefold()", "new": "normed_value = normed_value.lower()",
         "target": ["tests/test_reported_regression.py", "tests/test_types/test_Choice.py", "tests/test_basic.py", "tests/test_normalization.py"],
         "brief": "Review and validate the supplied candidate.patch for the Unicode case-insensitive Choice regression. "
                  "Apply it if correct; otherwise correct the source. Preserve original choice objects, enum behavior, token normalization and case-sensitive rejection. "
                  "Preserve public APIs and existing tests. Deliver the working source change, run the reported regression and normal non-stress suite, "
                  "and report whether the candidate was accepted and why.",
         "repro": "import click\ndef test_reported_unicode_choice():\n"
                  "    assert click.Choice(['Straße'],case_sensitive=False).convert('STRASSE',None,None)=='Straße'\n",
         "oracle": "import enum\n"
                   "for option, token in [('Straße','STRASSE'),('Maße','MASSE'),('Σ','ς')]:\n"
                   "    assert click.Choice([option],case_sensitive=False).convert(token,None,None)==option\n"
                   "class Kind(enum.Enum):\n    STRASSE='road'\n"
                   "assert click.Choice(Kind,case_sensitive=False).convert('Straße',None,None) is Kind.STRASSE\n"
                   "ctx=click.Context(click.Command('cli'),token_normalize_func=str.strip)\n"
                   "assert click.Choice(['Straße'],case_sensitive=False).convert(' STRASSE ',None,ctx)=='Straße'\n"
                   "try: click.Choice(['Straße'],case_sensitive=True).convert('STRASSE',None,None)\n"
                   "except click.BadParameter: pass\nelse: raise AssertionError('case sensitive match widened')\n"},
    ]
    for task in tasks:
        original = core if task["path"].endswith("core.py") else code
        if original.count(task["old"]) != 1:
            raise ValueError("mutation anchor is not unique")
        workspace = BASE / "cases" / task["id"]
        shutil.copytree(UPSTREAM, workspace, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        (workspace / task["path"]).write_text(original.replace(task["old"], task["new"]), encoding="utf-8", newline="\n")
        (workspace / "tests/test_reported_regression.py").write_text(task["repro"], encoding="utf-8", newline="\n")
        if task["scenario"] == "verification":
            patch = "".join(difflib.unified_diff(original.replace(task["old"], task["new"]).splitlines(True),
                                              original.splitlines(True), fromfile="a/" + task["path"], tofile="b/" + task["path"]))
            (workspace / "candidate.patch").write_text(patch, encoding="utf-8", newline="\n")
        oracle = BASE / "oracles" / (task["id"] + ".py")
        oracle.parent.mkdir(exist_ok=True)
        oracle.write_text("import os,pathlib,click\nassert pathlib.Path(click.__file__).resolve().is_relative_to(pathlib.Path(os.environ['STUDY_WORKSPACE']).resolve()/'src')\n" + task["oracle"], encoding="utf-8")
        policy = {"checks": [{"id": "target", "argv": [str(PYTHON), "-m", "pytest", "-q", "--color=no", *task["target"]], "timeout_seconds": 60},
                              {"id": "regression", "argv": [str(PYTHON), "-m", "pytest", "-q", "--color=no", "tests"], "timeout_seconds": 75}],
                  "protected_paths": ["tests", "pyproject.toml"]}
        write_json(BASE / "policies" / (task["id"] + ".json"), policy)
        for args in (["git", "init", "-q"], ["git", "-c", "core.autocrlf=false", "add", "."],
                     ["git", "-c", "user.name=TrajectoryStudy", "-c", "user.email=study@localhost", "commit", "-q", "-m", "Evaluation starting state"]):
            result = command(args, workspace)
            if result["exit_code"]: raise RuntimeError(result["stderr"])
        task["baseline_commit"] = command(["git", "rev-parse", "HEAD"], workspace)["stdout"].strip()
        task["baseline_target"] = command(policy["checks"][0]["argv"], workspace, python_env(workspace))
        task["baseline_oracle"] = command([PYTHON, oracle], workspace, python_env(workspace))
        golden_oracle = command([PYTHON, oracle], UPSTREAM, python_env(UPSTREAM))
        if task["baseline_target"]["exit_code"] == 0 or task["baseline_oracle"]["exit_code"] == 0 or golden_oracle["exit_code"] != 0:
            raise ValueError("task precondition or hidden oracle failed validation: " + task["id"] + str(golden_oracle))
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "repository": json.loads((BASE / "upstream.json").read_text()),
                "dsh_version": "0.2.1-rc.5", "model": MODEL, "reasoning_effort": "low", "repeats": 2,
                "max_seconds": 300, "max_total_tokens": 500000, "max_output_tokens": 8000,
                "subagent_max_seconds": 150, "subagent_max_total_tokens": 200000,
                "design": "controlled A/B: native main versus main required to call the selected specialized executor; no generic subagent",
                "task_origin": "real upstream repository with disclosed controlled regression mutations, not sampled historical issues",
                "tasks": tasks, "runtime_fingerprint": IMPLEMENTATION_FINGERPRINT,
                "schedule": [{"task": task["id"], "repeat": repeat, "arm": arm}
                             for number, task in enumerate(tasks) for repeat in range(2)
                             for arm in (("direct", "offload") if (number + repeat) % 2 == 0 else ("offload", "direct"))]}
    write_json(manifest_path, manifest)
    return manifest


class Ledger:
    def __init__(self, path, max_tokens):
        self.path, self.max_tokens = path, max_tokens
        self.rows, self.lock = [], threading.Lock()
        ledger = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args): pass

            def do_GET(self): self.forward()

            def do_POST(self): self.forward()

            def forward(self):
                role, suffix = self.path.lstrip("/").split("/", 1)
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(body) if body else {}
                inference = self.command == "POST" and suffix.endswith("responses")
                row = {"role": role, "inference": inference, "path": suffix, "model": payload.get("model"),
                       "reasoning": payload.get("reasoning"), "max_output_tokens": payload.get("max_output_tokens"),
                       "request_bytes": len(body), "tool_names": [tool.get("name") for tool in payload.get("tools", [])],
                       "started": time.time(), "usage": None}
                with ledger.lock:
                    spent = sum((item.get("usage") or {}).get("total_tokens", 0) for item in ledger.rows)
                if inference and spent >= ledger.max_tokens:
                    self.send_error(429, "study token budget exhausted")
                    return
                headers = {"Authorization": "Bearer local-dev", "Content-Type": "application/json"}
                request = urllib.request.Request("http://127.0.0.1:8788/" + suffix, data=body or None, headers=headers, method=self.command)
                started, disconnected = time.monotonic(), False
                try:
                    try: response = urllib.request.urlopen(request, timeout=180)
                    except urllib.error.HTTPError as error: response = error
                    with response:
                        row["http_status"] = response.status
                        content_type = response.headers.get("Content-Type", "application/json")
                        self.send_response(response.status)
                        self.send_header("Content-Type", content_type)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        def send(chunk):
                            nonlocal disconnected
                            if not disconnected:
                                try: self.wfile.write(chunk); self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError): disconnected = True
                        if "text/event-stream" in content_type:
                            for line in response:
                                send(line)
                                if line.startswith(b"data: "):
                                    try:
                                        event = json.loads(line[6:])
                                        value = event.get("response", {})
                                        if value.get("usage"): row["usage"] = value["usage"]
                                        if value.get("model"): row["response_model"] = value["model"]
                                    except (ValueError, TypeError): pass
                        else:
                            data = response.read()
                            send(data)
                            try:
                                value = json.loads(data)
                                row["usage"] = value.get("usage")
                                row["response_model"] = value.get("model")
                            except ValueError: pass
                except Exception as error:
                    row["error"] = type(error).__name__
                    disconnected = True
                finally:
                    row["elapsed_seconds"] = time.monotonic() - started
                    row["client_disconnected"] = disconnected
                    with ledger.lock:
                        ledger.rows.append(row)
                        with ledger.path.open("a", encoding="utf-8") as stream: stream.write(json.dumps(row) + "\n")
                    self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, role): return f"http://127.0.0.1:{self.server.server_port}/{role}/v1"

    def close(self): self.server.shutdown(); self.server.server_close()


def profile(home, workspace, ledger, task, arm, run_dir):
    rows = [{"id": key, "disabled": True} for key in DISABLED]
    rows += [{"id": "agent-default-model", "config": {"provider": "study", "model": MODEL, "reasoningEffort": "low"}},
             {"id": "llm-pi-ai", "config": {"providers": {"study": {"apiKeyEnv": "OPENAI_API_KEY", "api": "openai-responses",
                 "baseURL": ledger.url("main"), "models": [{"id": MODEL, "contextWindow": 131072, "maxTokens": 8000,
                 "input": ["text"], "reasoningEfforts": {"low": "low"}}], "retryPolicy": {"mode": "normal", "maxRetries": 0}}}}},
             {"id": "approval", "config": {"policy": "never"}},
             {"id": "sandbox-policy", "config": {"mode": "danger-full-access", "workspaceRoot": str(workspace)}}]
    common = ("You are a coding agent powered by the {{model}} model. Complete the user's task in the current checkout. "
              f"The prepared Python interpreter is {PYTHON.as_posix()}; use it for pytest, with --color=no. "
              "PYTHONPATH points to this checkout's src. Preserve all existing tests and project configuration; new regression tests are allowed. "
              "Do not inspect other checkouts, hidden grading artifacts, or the evaluation harness. Do not use network access or install packages. "
              "Do not create commits, invoke external LLMs or delegate to any other agent. Do not ask for clarification: finish the well-specified task. ")
    if arm == "direct":
        common += "Perform the task directly with your native file/search/PowerShell tools; no offloading is available."
    else:
        names = {"impact": "investigate_repository", "diagnosis": "diagnose_failure", "verification": "verify_patch"}
        target = names[task["scenario"]]
        common += (f"The only permitted offload is the task-level MCP executor mcp__trajectory__{target}. "
                   "This controlled evaluation requires using it at least once, then continuing the complete task yourself. "
                   "Get the current snapshot and available check IDs with mcp__trajectory__get_repository_state. "
                   "Use check ID target for focused diagnosis and target plus regression for patch verification. "
                   "Treat partial findings as incomplete and continue with native tools when necessary. ")
        if task["scenario"] == "impact":
            common += ("Before editing, call investigate_repository with task_type=change_impact. Supply public API symbols "
                       "or candidate changed paths; a short native localization step is allowed. Ask for source-supported dependencies, "
                       "candidate tests and scope limits. Do not request a repair or runtime execution from this read-only executor.")
        elif task["scenario"] == "diagnosis":
            common += ("Before editing, call diagnose_failure with check_ids=['target'], literal failure_signature='test_reported', "
                       "and repetitions=2. Ask for observed failures and relevant source with causal uncertainty preserved.")
        else:
            common += ("Before editing or applying candidate.patch, call verify_patch using its exact text and the current snapshot, "
                       "with check_ids=['target','regression']. Its verdict concerns a temporary copy, not this checkout: "
                       "apply the accepted patch yourself, or correct the source if necessary, and complete validation.")
        rows.append({"insert": [{"id": "trajectory-mcp", "name": "@deepseek-ai/dsh-mcp-client", "required": True,
            "config": {"serverName": "trajectory", "transport": "stdio", "command": sys.executable,
                       "args": [str(PROJECT / "benchmarks/dsh_mcp.py"), "--repo", str(workspace), "--checks",
                                str(BASE / "policies" / (task["id"] + ".json")), "--logs", str(run_dir / "subagent"),
                                "--scenario", task["scenario"]], "cwd": str(PROJECT),
                       "env": {"OPENAI_API_KEY": "local-dev", "OPENAI_BASE_URL": ledger.url("subagent")},
                       "toolCallTimeoutMs": 185000, "failOnStartupError": True}}]})
    rows.append({"id": "system-prompt", "config": {"personaPrefix": common}})
    home.mkdir(parents=True, exist_ok=True)
    write_json(home / "cordis.patch.yml", rows)


def run_trial(manifest, entry):
    task = next(item for item in manifest["tasks"] if item["id"] == entry["task"])
    name = f"{task['id']}-r{entry['repeat']}-{entry['arm']}"
    run_dir = BASE / "runs" / name
    if (run_dir / "result.json").exists(): return json.loads((run_dir / "result.json").read_text())
    if run_dir.exists(): raise ValueError("incomplete run exists; preserve it and use an explicit new study directory")
    run_dir.mkdir(parents=True)
    started = time.monotonic()
    workspace = run_dir / "workspace"
    shutil.copytree(BASE / "cases" / task["id"], workspace, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    protected = [str(path.relative_to(workspace)).replace("\\", "/") for path in workspace.rglob("*")
                 if path.is_file() and (path.is_relative_to(workspace / "tests") or path.name == "pyproject.toml")]
    protected_before = digest_files(workspace, protected)
    ledger = Ledger(run_dir / "api-ledger.jsonl", manifest["max_total_tokens"])
    home = run_dir / "home"
    profile(home, workspace, ledger, task, entry["arm"], run_dir)
    env = python_env(workspace)
    env.update(DSH_HOME=str(home), DSH_TELEMETRY_DISABLED="1", DSH_TOOLS_MODE="native",
               DSH_PERMISSION_MODE="danger-full-access", OPENAI_API_KEY="local-dev")
    events, parse_errors = [], []
    agent_started = time.monotonic()
    with (run_dir / "events.jsonl").open("w", encoding="utf-8") as out, (run_dir / "stderr.txt").open("wb") as err:
        process = subprocess.Popen(["node", str(DSH), "--profile", "headless", "--json", "-"], cwd=workspace, env=env,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                                   start_new_session=os.name != "nt")
        def consume():
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if event.get("type") != "thinking":
                        events.append(event)
                        out.write(json.dumps(event, ensure_ascii=False) + "\n"); out.flush()
                except ValueError: parse_errors.append(line[:100].decode("utf-8", "replace"))
        reader = threading.Thread(target=consume, daemon=True); reader.start()
        process.stdin.write(task["brief"].encode()); process.stdin.close()
        timed_out = False
        try: process.wait(timeout=manifest["max_seconds"])
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt": command(["taskkill", "/PID", str(process.pid), "/T", "/F"], PROJECT, timeout=15)
            else: os.killpg(process.pid, 9)
            process.wait(timeout=15)
        reader.join(timeout=10)
    agent_elapsed = time.monotonic() - agent_started
    ledger.close()
    calls = [event for event in events if event.get("type") == "tool_call"]
    offloads = [event for event in calls if event.get("tool", "").startswith("mcp__trajectory__")
                and not event["tool"].endswith("get_repository_state")]
    original_tests_unchanged = all((workspace / path).is_file() and hashlib.sha256((workspace / path).read_bytes()).hexdigest() == value
                                   for path, value in protected_before.items())
    grading_started = time.monotonic()
    regression = command([PYTHON, "-m", "pytest", "-q", "--color=no", "tests"], workspace, python_env(workspace)) if original_tests_unchanged else {"exit_code": None, "stderr": "protected inputs changed"}
    oracle = command([PYTHON, BASE / "oracles" / (task["id"] + ".py")], workspace, python_env(workspace)) if original_tests_unchanged else {"exit_code": None}
    write_json(run_dir / "regression.json", regression); write_json(run_dir / "oracle.json", oracle)
    patch = command(["git", "diff", "--no-ext-diff", "--binary"], workspace)["stdout"]
    (run_dir / "result.patch").write_text(patch, encoding="utf-8")
    inference = [row for row in ledger.rows if row["inference"]]
    metrics = {}
    for role in ("main", "subagent"):
        rows = [row for row in inference if row["role"] == role]
        metrics[role] = {"model_calls": len(rows), "input_tokens": sum((row["usage"] or {}).get("input_tokens", 0) for row in rows),
            "output_tokens": sum((row["usage"] or {}).get("output_tokens", 0) for row in rows),
            "cached_input_tokens": sum((row["usage"] or {}).get("input_tokens_details", {}).get("cached_tokens", 0) or 0 for row in rows),
            "total_tokens": sum((row["usage"] or {}).get("total_tokens", 0) for row in rows),
            "usage_complete": all(row["usage"] is not None for row in rows)}
    terminal = [event for event in events if event.get("type") == "final"]
    quality = original_tests_unchanged and regression["exit_code"] == 0 and oracle["exit_code"] == 0
    conforms = bool(offloads) if entry["arm"] == "offload" else not offloads
    result = {**entry, "run_id": name, "dsh_exit_code": process.returncode, "timed_out": timed_out,
              "main_task_success": quality, "protocol_conformant": conforms,
              "success": quality and conforms and not timed_out and bool(terminal),
              "original_tests_unchanged": original_tests_unchanged, "offload_calls": len(offloads),
              "offload_tools": [event["tool"] for event in offloads], "tool_calls": len(calls),
              "agent_wall_seconds": round(agent_elapsed, 4), "end_to_end_seconds": round(time.monotonic() - started, 4),
              "grading_seconds": round(time.monotonic() - grading_started, 4), "metrics": metrics,
              "total_tokens": sum(item["total_tokens"] for item in metrics.values()),
              "usage_complete": all(item["usage_complete"] for item in metrics.values()),
              "request_models_valid": all(row["model"] == MODEL and (row["reasoning"] or {}).get("effort") == "low" for row in inference),
              "ledger_model_calls": len(inference), "stderr_path": str(run_dir / "stderr.txt"),
              "runtime_fingerprint": IMPLEMENTATION_FINGERPRINT, "parse_errors": parse_errors,
              "final": terminal[-1].get("text") if terminal else None,
              "dsh_reported_total_tokens": sum(event.get("usage", {}).get("totalTokens", 0) for event in events
                                               if event.get("type") == "status" and event.get("phase") == "step_end")}
    write_json(run_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "one"))
    parser.add_argument("--task")
    parser.add_argument("--arm", choices=("direct", "offload"))
    parser.add_argument("--repeat", type=int, default=0)
    args = parser.parse_args()
    manifest = prepare()
    print(json.dumps({"prepared_tasks": [item["id"] for item in manifest["tasks"]], "formal_trials": len(manifest["schedule"])}), flush=True)
    if args.action == "prepare": return
    schedule = manifest["schedule"] if args.action == "run" else [{"task": args.task, "arm": args.arm, "repeat": args.repeat}]
    for entry in schedule:
        print(json.dumps({"starting": entry}), flush=True)
        result = run_trial(manifest, entry)
        print(json.dumps({key: result[key] for key in ("run_id", "success", "main_task_success", "offload_calls", "total_tokens", "agent_wall_seconds", "usage_complete")}), flush=True)


if __name__ == "__main__": main()
