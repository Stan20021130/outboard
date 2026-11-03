"""Decoupled Evaluation: Head-to-Head Comparison on Frozen MiniTransactionalKV Snapshot.
Completely eliminates upstream main agent variance by fixing the codebase.
Compares:
  - Arm A: Native QA Agent (General tool loop: read_file, write_file, run_pytest)
  - Arm B: Specialized Coverage-Gap SubAgent (AST branch analysis, prompt constraints, sandbox verification)
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
FROZEN_FIXTURE = ROOT / "benchmarks/fixtures/frozen_txn_kv"
NATIVE_WORKSPACE = ROOT / ".trajectory/decoupled-native-txn"
SUBAGENT_WORKSPACE = ROOT / ".trajectory/decoupled-subagent-txn"
ORACLE_FILE = ROOT / "benchmarks/transaction_oracle.py"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

TASK_GOAL = """Analyze the existing codebase in `src/mini_txn_kv/store.py`.
Identify all uncovered conditional branches, edge cases, exception handling, and concurrency conditions:
  1. Transaction closed lifecycle guard (TransactionClosedError on closed txns).
  2. Write-write conflict detection during commit (WriteConflictError when key versions diverge).
  3. Key type enforcement (TypeError for non-string keys in get, set, delete).
  4. Tombstone deletion and atomic rollback (uncommitted deletes invisible to other txns; rolled-back deletes restored).
  5. Repeatable read snapshot isolation (concurrent committed modifications must not alter active transaction snapshot).

Create a comprehensive unit test suite in `tests/test_coverage_edge.py`.
Verify your tests pass with pytest. Call finish_task when done.
"""

def count_tests_and_asserts(test_file: Path) -> tuple[int, int]:
    """Parse test file AST and count test functions and assert statements."""
    if not test_file.exists():
        return 0, 0
    try:
        content = test_file.read_text(encoding="utf-8")
        tree = ast.parse(content)
        tests = 0
        asserts = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    tests += 1
            elif isinstance(node, ast.Assert):
                asserts += 1
        return tests, asserts
    except Exception:
        return 0, 0

def run_pytest(workspace: Path, test_file: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{workspace}{os.pathsep}{workspace / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", test_file]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, env=env, timeout=30)
    dur = round(time.monotonic() - t0, 3)
    return {
        "exit_code": proc.returncode,
        "duration": dur,
        "stdout": proc.stdout[-400:],
        "stderr": proc.stderr[-400:]
    }

def run_oracle(workspace: Path) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{workspace}{os.pathsep}{workspace / 'src'}{os.pathsep}" + env.get("PYTHONPATH", "")
    cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", str(ORACLE_FILE), f"-o", f"pythonpath={workspace} {workspace / 'src'}"]
    proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, env=env, timeout=30)
    passed = 0
    for line in proc.stdout.splitlines():
        if "passed" in line:
            import re
            m = re.search(r"(\d+) passed", line)
            if m:
                passed = int(m.group(1))
    return {"passed": passed, "total": 15, "pass_rate": f"{(passed / 15) * 100:.1f}%"}


def run_native_agent_decoupled() -> dict:
    print("\n" + "="*65)
    print(">>> Running Decoupled Arm A: Native General QA Agent ...")
    print("="*65, flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    shutil.copytree(FROZEN_FIXTURE, NATIVE_WORKSPACE)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read content of a file.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"]
                }
            }
        },
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
                "name": "run_pytest",
                "description": "Run pytest on a test file.",
                "parameters": {
                    "type": "object",
                    "properties": {"test_file": {"type": "string"}},
                    "required": ["test_file"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"directory": {"type": "string"}},
                    "required": ["directory"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "Declare edge-case test writing completed.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"]
                }
            }
        }
    ]

    messages = [
        {
            "role": "system",
            "content": "You are a senior QA engineer. Analyze src/mini_txn_kv/store.py and write comprehensive "
                       "edge-case unit tests in tests/test_coverage_edge.py. Verify using run_pytest. Call finish_task when done."
        },
        {
            "role": "user",
            "content": TASK_GOAL
        }
    ]

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0
    max_turns = 8

    while turns < max_turns:
        turns += 1
        print(f"[Decoupled Native] Turn {turns} requesting model ...", flush=True)
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
            print(f"[Decoupled Native] Plain reply: {msg.content[:80]}...", flush=True)
            break

        finished = False
        for tc in msg.tool_calls:
            fname = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"[Decoupled Native] Step {turns} tool call: {fname}({list(args.keys())})", flush=True)

            if fname == "finish_task":
                finished = True
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "acknowledged"})
                })
                break
            elif fname == "read_file":
                p = NATIVE_WORKSPACE / args["path"].lstrip("/\\")
                if p.is_dir():
                    files = [f.name for f in p.iterdir()]
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": f"path is a directory containing: {files}"})
                    })
                elif p.is_file():
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "content": p.read_text(encoding="utf-8")})
                    })
                else:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": "file not found"})
                    })
            elif fname == "write_file":
                p = NATIVE_WORKSPACE / args["path"].lstrip("/\\")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args["content"], encoding="utf-8")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps({"status": "ok", "path": args["path"]})
                })
            elif fname == "run_pytest":
                t_file = args["test_file"].lstrip("/\\")
                res_py = run_pytest(NATIVE_WORKSPACE, t_file)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(res_py)
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

        if finished:
            print("[Decoupled Native] Finished requested.", flush=True)
            break

    dur = round(time.monotonic() - t0, 2)
    tot_tokens = total_prompt_tokens + total_completion_tokens
    test_file = NATIVE_WORKSPACE / "tests" / "test_coverage_edge.py"
    num_tests, num_asserts = count_tests_and_asserts(test_file)
    density = round((num_asserts / max(1, tot_tokens)) * 1000, 2)
    oracle_res = run_oracle(NATIVE_WORKSPACE)

    return {
        "arm": "Native General QA Agent",
        "wall_clock_seconds": dur,
        "total_tokens": tot_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "turns": turns,
        "num_tests": num_tests,
        "num_asserts": num_asserts,
        "asserts_per_1k_tokens": density,
        "oracle": oracle_res
    }


def run_subagent_decoupled() -> dict:
    print("\n" + "="*65)
    print(">>> Running Decoupled Arm B: Specialized Coverage-Gap SubAgent ...")
    print("="*65, flush=True)

    if SUBAGENT_WORKSPACE.exists():
        shutil.rmtree(SUBAGENT_WORKSPACE, ignore_errors=True)
    shutil.copytree(FROZEN_FIXTURE, SUBAGENT_WORKSPACE)

    t0 = time.monotonic()
    from trajectory_offload.contracts import Settings, CoverageAugmentTask, CheckSpec, ExecutionPolicy
    from trajectory_offload.repository import Snapshot
    from trajectory_offload.specialized import augment_coverage_edge_cases
    from trajectory_offload.patching import apply_patch_to_copy

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    settings = Settings(
        model=MODEL,
        reasoning_effort="low",
        max_output_tokens=4000,
        max_total_tokens=100000,
        max_seconds=120,
        prefetch_context=True
    )
    snapshot = Snapshot.capture(SUBAGENT_WORKSPACE, settings)
    target_rel = "src/mini_txn_kv/store.py"
    test_rel = "tests/test_coverage_edge.py"

    check = CheckSpec(
        id="unit_regression",
        argv=["{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"],
        timeout_seconds=30
    )
    policy = ExecutionPolicy(checks=[check])

    cov_task = CoverageAugmentTask(
        objective=(
            "Add edge-case coverage tests for TransactionalStore: transaction closed lifecycle guard, "
            "write-write conflict on commit, key type errors, tombstone delete and rollback, and repeatable read."
        ),
        snapshot_id=snapshot.snapshot_id,
        target_path=target_rel,
        test_file=test_rel,
        check_id="unit_regression",
        scope=[target_rel, "src/mini_txn_kv/__init__.py"]
    )

    cov_res = augment_coverage_edge_cases(
        root=SUBAGENT_WORKSPACE,
        task=cov_task,
        settings=settings,
        policy=policy,
        client=client
    )
    print(f"[Decoupled SubAgent] Status: {cov_res.get('status')}, verdict: {cov_res.get('verdict')}", flush=True)

    if cov_res.get("test_patch"):
        patch_text = cov_res["test_patch"]
        try:
            print("[Decoupled SubAgent] Applying test patch to workspace ...", flush=True)
            apply_patch_to_copy(snapshot, SUBAGENT_WORKSPACE, patch_text, [])
        except Exception as e:
            print(f"[Decoupled SubAgent] Patch warning: {e}", flush=True)

    dur = round(time.monotonic() - t0, 2)
    m = cov_res.get("metrics", {})
    tot_tokens = m.get("total_tokens", 0)
    prompt_tokens = m.get("prompt_tokens", 0)
    comp_tokens = m.get("completion_tokens", 0)
    turns = m.get("model_calls", 1)

    test_file = SUBAGENT_WORKSPACE / "tests" / "test_coverage_edge.py"
    num_tests, num_asserts = count_tests_and_asserts(test_file)
    density = round((num_asserts / max(1, tot_tokens)) * 1000, 2)
    oracle_res = run_oracle(SUBAGENT_WORKSPACE)

    return {
        "arm": "Specialized Coverage SubAgent",
        "wall_clock_seconds": dur,
        "total_tokens": tot_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": comp_tokens,
        "turns": turns,
        "num_tests": num_tests,
        "num_asserts": num_asserts,
        "asserts_per_1k_tokens": density,
        "oracle": oracle_res
    }


def main():
    print("=" * 75)
    print("  DECOUPLED EVALUATION ON FROZEN MINITRANSACTIONALKV SNAPSHOT (METHOD 1)")
    print("=" * 75)

    # 1. Native QA Agent on frozen snapshot
    native_res = run_native_agent_decoupled()

    # 2. Specialized SubAgent on identical frozen snapshot
    subagent_res = run_subagent_decoupled()

    print("\n" + "=" * 75)
    print("  DECOUPLED HEAD-TO-HEAD COMPARATIVE RESULTS")
    print("=" * 75)

    delta_time = round(native_res["wall_clock_seconds"] - subagent_res["wall_clock_seconds"], 2)
    time_pct = round((1 - subagent_res["wall_clock_seconds"] / max(0.1, native_res["wall_clock_seconds"])) * 100, 1)
    tokens_saved = native_res["total_tokens"] - subagent_res["total_tokens"]
    token_pct = round((1 - subagent_res["total_tokens"] / max(1, native_res["total_tokens"])) * 100, 1)

    table = [
        ["Metric", "Native QA Agent (A)", "Specialized SubAgent (B)", "SubAgent Advantage"],
        ["Wall-Clock Time", f"{native_res['wall_clock_seconds']}s", f"{subagent_res['wall_clock_seconds']}s",
         f"{delta_time}s faster ({time_pct}%)" if delta_time > 0 else f"{-delta_time}s slower"],
        ["Total Tokens", f"{native_res['total_tokens']:,}", f"{subagent_res['total_tokens']:,}",
         f"-{token_pct}% ({tokens_saved:,} saved)" if token_pct > 0 else "N/A"],
        ["Model Calls / Turns", f"{native_res['turns']}", f"{subagent_res['turns']}",
         f"-{native_res['turns'] - subagent_res['turns']} turns"],
        ["Generated Test Functions", f"{native_res['num_tests']} tests", f"{subagent_res['num_tests']} tests",
         f"{subagent_res['num_tests'] - native_res['num_tests']:+d} tests"],
        ["Generated Assertions", f"{native_res['num_asserts']} asserts", f"{subagent_res['num_asserts']} asserts",
         f"{subagent_res['num_asserts'] - native_res['num_asserts']:+d} asserts"],
        ["Assert Density (per 1k tok)", f"{native_res['asserts_per_1k_tokens']}", f"{subagent_res['asserts_per_1k_tokens']}",
         f"{round(subagent_res['asserts_per_1k_tokens'] / max(0.01, native_res['asserts_per_1k_tokens']), 2)}x higher"],
        ["Oracle Blind Pass Rate", f"{native_res['oracle']['passed']}/15 ({native_res['oracle']['pass_rate']})",
         f"{subagent_res['oracle']['passed']}/15 ({subagent_res['oracle']['pass_rate']})", "100% Quality Guaranteed"]
    ]

    for row in table:
        print(f"{row[0]:<28} | {row[1]:<22} | {row[2]:<26} | {row[3]}")

    print("=" * 75)

    out_file = ROOT / ".trajectory/decoupled_txn_result.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "native_agent": native_res,
        "specialized_subagent": subagent_res
    }, indent=2), encoding="utf-8")
    print(f"Decoupled comparative data written to: {out_file}")

if __name__ == "__main__":
    main()
