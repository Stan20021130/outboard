"""Native control trial for coverage-gap augmentation without specialized offloading."""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from openai import OpenAI
from trajectory_offload.contracts import Settings
from trajectory_offload.repository import Snapshot

ROOT = Path(__file__).resolve().parent.parent
BASE_REPO = ROOT / ".trajectory/v14-opt-test-03/cases/regression"
WORK_DIR = ROOT / ".trajectory/coverage-gap-native-workspace"

def setup_workspace():
    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    shutil.copytree(BASE_REPO, WORK_DIR, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))

def execute_pytest(test_file):
    cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", test_file]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(WORK_DIR), capture_output=True, text=True, timeout=60)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "duration": time.monotonic() - t0
    }

def main():
    print("Setting up clean native workspace ...", flush=True)
    setup_workspace()
    client = OpenAI(base_url="http://127.0.0.1:8788/v1", api_key="local-dev")
    model = "deepseek-v4.1-flash"

    tools = [
        {
            "type": "function",
            "name": "read_source_range",
            "description": "Read specific line range from a file in the workspace.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"}
                },
                "required": ["path", "start_line", "end_line"],
                "additionalProperties": False
            }
        },
        {
            "type": "function",
            "name": "search_code",
            "description": "Search literal string in workspace files.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"],
                "additionalProperties": False
            }
        },
        {
            "type": "function",
            "name": "write_file",
            "description": "Write or overwrite content of a file in the workspace.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["path", "content"],
                "additionalProperties": False
            }
        },
        {
            "type": "function",
            "name": "run_pytest",
            "description": "Run pytest on a specified test file.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "test_file": {"type": "string"}
                },
                "required": ["test_file"],
                "additionalProperties": False
            }
        }
    ]

    instructions = (
        "You are an autonomous coding agent. Work only in the current repository. "
        "Task: In src/click/types.py, add edge-case coverage tests for click.IntRange when clamp=False "
        "and values fall outside the boundary (raising click.BadParameter), as well as open boundary limits. "
        "Use your native tools to inspect code, write tests to tests/test_coverage_edge.py, and verify with run_pytest. "
        "When finished and pytest passes, summarize your changes and state that you are done."
    )

    history = [
        {"role": "user", "content": "Begin the task. Inspect src/click/types.py, implement tests/test_coverage_edge.py, and verify with pytest."}
    ]

    metrics = {
        "model_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "process_seconds": 0.0
    }

    print("\nStarting Native Control Agent loop (deepseek-v4.1-flash) ...", flush=True)
    t_start = time.monotonic()
    max_turns = 12

    for turn in range(max_turns):
        metrics["model_calls"] += 1
        resp = client.responses.create(
            model=model,
            reasoning={"effort": "low"},
            instructions=instructions,
            input=history,
            tools=tools,
            store=False,
            max_output_tokens=3000
        )
        usage = resp.usage
        if usage:
            metrics["input_tokens"] += usage.input_tokens
            metrics["output_tokens"] += usage.output_tokens
            metrics["total_tokens"] += usage.total_tokens

        history.extend(item.model_dump(exclude_none=True) for item in resp.output)
        calls = [item for item in resp.output if item.type == "function_call"]
        if not calls:
            print(f"Agent finished on turn {turn+1}. Final text length: {len(resp.output_text)}", flush=True)
            break

        for call in calls:
            metrics["tool_calls"] += 1
            args = json.loads(call.arguments)
            name = call.name
            print(f"  [Turn {turn+1}] Tool call: {name}({list(args.keys())})", flush=True)
            result = {}
            if name == "read_source_range":
                rel = args["path"].replace("\\", "/")
                target = WORK_DIR / rel
                if target.is_file():
                    lines = target.read_text(encoding="utf-8").splitlines()
                    s, e = max(1, args["start_line"]), min(len(lines), args["end_line"])
                    result = {"content": "\n".join(f"{i+1}: {lines[i]}" for i in range(s-1, e))}
                else:
                    result = {"error": f"File not found: {rel}"}
            elif name == "search_code":
                q = args["query"]
                matches = []
                for p in (WORK_DIR / "src").rglob("*.py"):
                    try:
                        content = p.read_text(encoding="utf-8")
                        for idx, line in enumerate(content.splitlines()):
                            if q in line:
                                matches.append({"file": p.relative_to(WORK_DIR).as_posix(), "line": idx+1, "text": line.strip()})
                                if len(matches) >= 10:
                                    break
                    except Exception:
                        pass
                result = {"matches": matches[:10]}
            elif name == "write_file":
                rel = args["path"].replace("\\", "/")
                target = WORK_DIR / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(args["content"], encoding="utf-8")
                result = {"status": "written", "bytes": len(args["content"])}
            elif name == "run_pytest":
                res = execute_pytest(args["test_file"])
                metrics["process_seconds"] += res["duration"]
                result = {"exit_code": res["exit_code"], "output": (res["stdout"] + res["stderr"])[:1000]}

            history.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, ensure_ascii=False)})

    wall_seconds = time.monotonic() - t_start
    print(f"\nNative Agent completed in {wall_seconds:.2f}s!", flush=True)
    print(f"Native Metrics: {json.dumps(metrics, indent=2)}")

    test_file = WORK_DIR / "tests/test_coverage_edge.py"
    test_exists = test_file.is_file()
    print(f"Generated test file exists: {test_exists}")
    if test_exists:
        content = test_file.read_text(encoding="utf-8")
        print(f"Test lines: {len(content.splitlines())}")
        py_res = execute_pytest("tests/test_coverage_edge.py")
        print(f"Final pytest exit_code: {py_res['exit_code']}")
        print(f"Final pytest output: {py_res['stdout'][:300]}")

    # Output comparison record
    out_record = {
        "native": {
            "wall_seconds": wall_seconds,
            "metrics": metrics,
            "test_exists": test_exists,
            "pytest_passed": test_exists and (execute_pytest("tests/test_coverage_edge.py")["exit_code"] == 0)
        }
    }
    (ROOT / ".trajectory/coverage-gap-native-summary.json").write_text(json.dumps(out_record, indent=2), encoding="utf-8")
    print(f"\nSummary saved to .trajectory/coverage-gap-native-summary.json")

if __name__ == "__main__":
    main()
