"""Greenfield Benchmark: TaskPipeline evaluation (DAG execution, cycle detection, retry).
Compares Native Control Arm vs Orchestrated Offload Arm.
Evaluates:
  1. Wall-clock time (seconds)
  2. Token consumption (prompt, completion, total)
  3. Interaction turns / tool calls
  4. Independent Acceptance Oracle Pass Rate (15 strict tests)
"""
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
NATIVE_WORKSPACE = ROOT / ".trajectory/pipeline-native"
OFFLOAD_WORKSPACE = ROOT / ".trajectory/pipeline-offload"
ORACLE_FILE = ROOT / "benchmarks/pipeline_oracle.py"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

TASK_SPECIFICATION = """You are tasked with building a lightweight task pipeline engine called `MiniPipeline` from scratch.

Module Location:
- Source code must reside in `src/mini_pipeline/pipeline.py`.
- Package init must reside in `src/mini_pipeline/__init__.py` exporting `Pipeline`, `PipelineResult`, `CyclicDependencyError`.

Specification:

1. Exception Class:
   `class CyclicDependencyError(ValueError): pass`

2. Result Class:
   `class PipelineResult:`
   Attributes:
   - `success: bool` (True if all attempted tasks completed successfully; False if any failed)
   - `results: dict[str, Any]` (Map of task_name -> return value for successful tasks)
   - `failed_tasks: list[str]` (List of task names that failed)
   - `errors: dict[str, str]` (Map of failed task_name -> error message string)
   - `execution_order: list[str]` (Order of executed tasks)

3. Pipeline Class:
   `class Pipeline:`
   Methods:
   - `__init__()`: initialize an empty pipeline.
   - `add_task(name: str, fn: Callable[[dict], Any], depends_on: list[str] | None = None, max_retries: int = 0) -> None`:
     - If not isinstance(name, str): raise TypeError.
     - If not callable(fn): raise TypeError.
     - If max_retries < 0: raise ValueError.
     - If task `name` already exists: raise ValueError.
     - `depends_on`: list of prerequisite task names. Defaults to empty list.
   - `has_task(name: str) -> bool`: returns True if task exists, False otherwise.
   - `clear() -> None`: reset pipeline, removing all tasks.
   - `run(initial_context: dict[str, Any] | None = None) -> PipelineResult`:
     - Check missing dependencies: if any task depends on a name not added to pipeline, raise ValueError.
     - Check cyclic dependencies: perform topological sort / cycle detection. If a cycle is detected (e.g. A->B->A or self-dependency A->A), raise CyclicDependencyError.
     - Execution:
       - Context starts as a copy of `initial_context` (or empty dict).
       - Tasks must execute in topological order (all dependencies must succeed before a task runs).
       - When executing a task, call `fn(context)`. If successful:
         - store return value in `results[name]` and in `context[name] = return_value`.
         - add `name` to `execution_order`.
       - If `fn(context)` raises an exception:
         - Retry up to `max_retries` times.
         - If all retries fail: record task in `failed_tasks`, record str(exc) in `errors[name]`.
         - Downstream tasks that depend on the failed task must be SKIPPED (not executed).
         - Independent tasks (tasks that do not depend on any failed task) should still execute.
     - Return `PipelineResult`.

Requirements:
- Clean, robust implementation with type hints.
- Unit tests in `tests/test_pipeline.py`.
- Run pytest to verify all tests pass before calling finish_task.
"""

def run_oracle(workspace: Path) -> dict:
    """Run the external independent 15-test Oracle suite against the workspace."""
    cmd = [
        sys.executable,
        "-m", "pytest",
        "-q",
        "--color=no",
        str(ORACLE_FILE),
        f"-o", f"pythonpath={workspace} {workspace / 'src'}"
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{workspace}{os.pathsep}{workspace / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, env=env, timeout=30)
    dur = time.monotonic() - t0
    
    passed = 0
    failed = 0
    for line in proc.stdout.splitlines():
        if "passed" in line or "failed" in line:
            import re
            m_pass = re.search(r"(\d+) passed", line)
            m_fail = re.search(r"(\d+) failed", line)
            if m_pass:
                passed = int(m_pass.group(1))
            if m_fail:
                failed = int(m_fail.group(1))
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "failed": failed,
        "total": 15,
        "pass_rate": f"{(passed / 15) * 100:.1f}%",
        "duration": round(dur, 3),
        "stdout": proc.stdout[-500:] if len(proc.stdout) > 500 else proc.stdout,
        "stderr": proc.stderr[-500:] if len(proc.stderr) > 500 else proc.stderr
    }


def execute_native_arm() -> dict:
    print("\n" + "="*60)
    print(">>> Starting Native Control Arm Trial (MiniPipeline) ...")
    print("="*60, flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    NATIVE_WORKSPACE.mkdir(parents=True, exist_ok=True)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    
    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write or overwrite content to a file in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read content of a file in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "run_pytest",
                "description": "Run pytest on a test file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "test_file": {"type": "string"}
                    },
                    "required": ["test_file"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string"}
                    },
                    "required": ["directory"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "Declare project completed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"}
                    },
                    "required": ["summary"]
                }
            }
        }
    ]

    messages = [
        {
            "role": "system",
            "content": "You are a senior Python software engineer. Build the requested project from scratch. "
                       "Create files using write_file. Verify using run_pytest. "
                       "When all tests pass, call finish_task."
        },
        {
            "role": "user",
            "content": TASK_SPECIFICATION
        }
    ]

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0
    max_turns = 12

    while turns < max_turns:
        turns += 1
        print(f"[Native Arm] Turn {turns} requesting model ...", flush=True)
        res = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            temperature=0.0
        )
        choice = res.choices[0]
        usage = res.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens

        msg = choice.message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"[Native Arm] Model plain message: {msg.content[:100]}...", flush=True)
            break

        finished = False
        for tc in msg.tool_calls:
            fname = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"[Native Arm] Step {turns} tool call: {fname}({list(args.keys())})", flush=True)

            if fname == "finish_task":
                finished = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "acknowledged", "summary": args.get("summary")})
                })
                break
            elif fname == "write_file":
                rel_path = args["path"].lstrip("/\\")
                target = NATIVE_WORKSPACE / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(args["content"], encoding="utf-8")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "ok", "path": rel_path, "bytes_written": len(args["content"])})
                })
            elif fname == "read_file":
                rel_path = args["path"].lstrip("/\\")
                target = NATIVE_WORKSPACE / rel_path
                if target.exists():
                    content = target.read_text(encoding="utf-8")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "content": content})
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": f"File {rel_path} not found"})
                    })
            elif fname == "list_files":
                d = args.get("directory", ".")
                target_dir = NATIVE_WORKSPACE / d.lstrip("/\\")
                if target_dir.exists():
                    files = [str(p.relative_to(NATIVE_WORKSPACE)) for p in target_dir.rglob("*") if p.is_file()]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "files": files[:30]})
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "files": []})
                    })
            elif fname == "run_pytest":
                test_file = args["test_file"].lstrip("/\\")
                env = os.environ.copy()
                env["PYTHONPATH"] = f"{NATIVE_WORKSPACE}{os.pathsep}{NATIVE_WORKSPACE / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
                cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", str(test_file)]
                proc = subprocess.run(cmd, cwd=str(NATIVE_WORKSPACE), capture_output=True, text=True, env=env, timeout=30)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({
                        "exit_code": proc.returncode,
                        "stdout": proc.stdout[-500:],
                        "stderr": proc.stderr[-500:]
                    })
                })

        if finished:
            print("[Native Arm] Finished task requested by model.", flush=True)
            break

    dur = time.monotonic() - t0
    oracle_res = run_oracle(NATIVE_WORKSPACE)
    return {
        "arm": "Native Control Arm",
        "wall_clock_seconds": round(dur, 2),
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "turns": turns,
        "oracle": oracle_res
    }


def execute_offload_arm() -> dict:
    print("\n" + "="*60)
    print(">>> Starting Orchestrated Offload Arm Trial (MiniPipeline) ...")
    print("="*60, flush=True)

    if OFFLOAD_WORKSPACE.exists():
        shutil.rmtree(OFFLOAD_WORKSPACE, ignore_errors=True)
    OFFLOAD_WORKSPACE.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0

    # 1. Zero-Token Deterministic Scaffolding (< 5ms, 0 Token)
    print("[Offload Arm] Stage 1: Zero-Token Scaffolding initializing...", flush=True)
    src_dir = OFFLOAD_WORKSPACE / "src" / "mini_pipeline"
    test_dir = OFFLOAD_WORKSPACE / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "__init__.py").write_text(
        "from .pipeline import Pipeline, PipelineResult, CyclicDependencyError\n\n"
        "__all__ = ['Pipeline', 'PipelineResult', 'CyclicDependencyError']\n",
        encoding="utf-8"
    )
    scaffold_code = (
        "from typing import Any, Callable\n\n"
        "class CyclicDependencyError(ValueError):\n"
        "    pass\n\n"
        "class PipelineResult:\n"
        "    def __init__(self, success: bool, results: dict[str, Any], failed_tasks: list[str], errors: dict[str, str], execution_order: list[str]):\n"
        "        self.success = success\n"
        "        self.results = results\n"
        "        self.failed_tasks = failed_tasks\n"
        "        self.errors = errors\n"
        "        self.execution_order = execution_order\n\n"
        "class Pipeline:\n"
        "    def __init__(self):\n"
        "        self._tasks = {}\n\n"
        "    def add_task(self, name: str, fn: Callable[[dict], Any], depends_on: list[str] | None = None, max_retries: int = 0) -> None:\n"
        "        pass\n\n"
        "    def has_task(self, name: str) -> bool:\n"
        "        pass\n\n"
        "    def clear(self) -> None:\n"
        "        pass\n\n"
        "    def run(self, initial_context: dict[str, Any] | None = None) -> PipelineResult:\n"
        "        pass\n"
    )
    (src_dir / "pipeline.py").write_text(scaffold_code, encoding="utf-8")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    # 2. Primary Agent: Implements core logic and base unit tests
    print("[Offload Arm] Stage 2: Main Agent implementing core logic...", flush=True)
    offload_prompt = (
        "You are a senior Python software engineer. The project scaffold has already been initialized.\n"
        "Source file is at `src/mini_pipeline/pipeline.py`.\n"
        "Your task is to implement `Pipeline`, `PipelineResult`, `CyclicDependencyError` according to the specification.\n"
        "Then write initial unit tests in `tests/test_pipeline.py`.\n"
        "Use write_file to save the files, and call verify_patch to test."
    )

    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write or overwrite content of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["path", "content"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "verify_patch",
                "description": "Run fast differential verification on tests/test_pipeline.py.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "hand_off_to_coverage_agent",
                "description": "Hand off to specialized coverage-gap agent to augment edge-case tests.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ready": {"type": "boolean"}
                    },
                    "required": ["ready"]
                }
            }
        }
    ]

    messages = [
        {"role": "system", "content": "You are a precise, efficient Python core engineer. Zero redundancy."},
        {"role": "user", "content": f"{TASK_SPECIFICATION}\n\n{offload_prompt}"}
    ]

    max_turns = 8
    handed_off = False

    while turns < max_turns:
        turns += 1
        print(f"[Offload Arm] Main Agent Turn {turns} ...", flush=True)
        res = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            temperature=0.0
        )
        choice = res.choices[0]
        usage = res.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens

        msg = choice.message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"[Offload Arm] Main Agent plain response: {msg.content[:100]}...", flush=True)
            break

        done_turn = False
        for tc in msg.tool_calls:
            fname = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"[Offload Arm] Step {turns} tool call: {fname}({list(args.keys())})", flush=True)

            if fname == "hand_off_to_coverage_agent":
                handed_off = True
                done_turn = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "handed_off", "message": "Coverage augmentation initiated."})
                })
                break
            elif fname == "write_file":
                rel_path = args["path"].lstrip("/\\")
                target = OFFLOAD_WORKSPACE / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(args["content"], encoding="utf-8")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "ok", "path": rel_path})
                })
            elif fname == "verify_patch":
                env = os.environ.copy()
                env["PYTHONPATH"] = f"{OFFLOAD_WORKSPACE}{os.pathsep}{OFFLOAD_WORKSPACE / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
                test_file = OFFLOAD_WORKSPACE / "tests" / "test_pipeline.py"
                if not test_file.exists():
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": "tests/test_pipeline.py does not exist yet."})
                    })
                else:
                    cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", str(test_file)]
                    t_v0 = time.monotonic()
                    proc = subprocess.run(cmd, cwd=str(OFFLOAD_WORKSPACE), capture_output=True, text=True, env=env, timeout=15)
                    v_dur = round(time.monotonic() - t_v0, 3)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({
                            "status": "passed" if proc.returncode == 0 else "failed",
                            "duration_sec": v_dur,
                            "stdout": proc.stdout[-300:],
                            "stderr": proc.stderr[-300:]
                        })
                    })

        if done_turn or handed_off:
            break

    # 3. Stage 3: Specialized Coverage Gap Agent (Edge Case Generation)
    print("[Offload Arm] Stage 3: Specialized Edge-Case Coverage Augmenter running ...", flush=True)
    from trajectory_offload.contracts import Settings, CoverageAugmentTask, CheckSpec, ExecutionPolicy
    from trajectory_offload.repository import Snapshot
    from trajectory_offload.specialized import augment_coverage_edge_cases
    from trajectory_offload.patching import apply_patch_to_copy

    settings = Settings(
        model=MODEL,
        reasoning_effort="low",
        max_output_tokens=4000,
        max_total_tokens=100000,
        max_seconds=120,
        prefetch_context=True
    )
    snapshot = Snapshot.capture(OFFLOAD_WORKSPACE, settings)
    target_rel = "src/mini_pipeline/pipeline.py"
    test_rel = "tests/test_coverage_edge.py"

    check = CheckSpec(
        id="unit_regression",
        argv=["{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"],
        timeout_seconds=30
    )
    policy = ExecutionPolicy(checks=[check])

    cov_task = CoverageAugmentTask(
        objective="Add edge-case coverage tests for MiniPipeline (cyclic dependency, missing dependency, retry exhaustion, empty pipeline).",
        snapshot_id=snapshot.snapshot_id,
        target_path=target_rel,
        test_file=test_rel,
        check_id="unit_regression",
        scope=[target_rel, "src/mini_pipeline/__init__.py"]
    )

    cov_res = augment_coverage_edge_cases(
        root=OFFLOAD_WORKSPACE,
        task=cov_task,
        settings=settings,
        policy=policy,
        client=client
    )
    print(f"[Offload Arm] Coverage Augmentation status: {cov_res.get('status')}, verdict: {cov_res.get('verdict')}", flush=True)

    if cov_res.get("test_patch"):
        patch_text = cov_res["test_patch"]
        try:
            print("[Offload Arm] Applying augmented edge cases to workspace ...", flush=True)
            apply_patch_to_copy(snapshot, OFFLOAD_WORKSPACE, patch_text, [])
        except Exception as e:
            print(f"[Offload Arm] Patch apply warning: {e}", flush=True)

    dur = time.monotonic() - t0
    subagent_tokens = cov_res.get("metrics", {}).get("total_tokens", 0)
    total_prompt_tokens += cov_res.get("metrics", {}).get("prompt_tokens", 0)
    total_completion_tokens += cov_res.get("metrics", {}).get("completion_tokens", 0)

    oracle_res = run_oracle(OFFLOAD_WORKSPACE)
    return {
        "arm": "Orchestrated Offload Arm",
        "wall_clock_seconds": round(dur, 2),
        "total_tokens": total_prompt_tokens + total_completion_tokens + subagent_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "turns": turns + 1,
        "oracle": oracle_res
    }


def main():
    print("=" * 70)
    print("  GREENFIELD EVALUATION 2: MINI-PIPELINE FROM SCRATCH (A/B COMPARISON)")
    print("=" * 70)

    # 1. Run Native Arm
    native_result = execute_native_arm()

    # 2. Run Offload Arm
    offload_result = execute_offload_arm()

    print("\n" + "=" * 70)
    print("  FINAL MINI-PIPELINE COMPARATIVE RESULTS")
    print("=" * 70)

    res_table = [
        ["Metric", "Native Control Arm", "Orchestrated Offload Arm", "Advantage / Delta"],
        ["Wall-Clock Time", f"{native_result['wall_clock_seconds']}s", f"{offload_result['wall_clock_seconds']}s", 
         f"{round(native_result['wall_clock_seconds'] - offload_result['wall_clock_seconds'], 2)}s faster" if offload_result['wall_clock_seconds'] < native_result['wall_clock_seconds'] else f"{round(offload_result['wall_clock_seconds'] - native_result['wall_clock_seconds'], 2)}s slower"],
        ["Total Tokens", f"{native_result['total_tokens']:,}", f"{offload_result['total_tokens']:,}",
         f"-{round((1 - offload_result['total_tokens'] / max(1, native_result['total_tokens'])) * 100, 1)}% tokens" if offload_result['total_tokens'] < native_result['total_tokens'] else "N/A"],
        ["Interaction Turns", f"{native_result['turns']}", f"{offload_result['turns']}",
         f"-{native_result['turns'] - offload_result['turns']} turns" if offload_result['turns'] < native_result['turns'] else "N/A"],
        ["Oracle Passed", f"{native_result['oracle']['passed']} / {native_result['oracle']['total']}",
         f"{offload_result['oracle']['passed']} / {offload_result['oracle']['total']}",
         f"{native_result['oracle']['pass_rate']} vs {offload_result['oracle']['pass_rate']}"]
    ]

    for row in res_table:
        print(f"{row[0]:<20} | {row[1]:<22} | {row[2]:<25} | {row[3]}")

    print("=" * 70)

    out_file = ROOT / ".trajectory/pipeline_ab_result.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "native": native_result,
        "offload": offload_result
    }, indent=2), encoding="utf-8")
    print(f"Results written to: {out_file}")

if __name__ == "__main__":
    main()
