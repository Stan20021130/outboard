"""Medium-Scale System Greenfield Benchmark: MiniDocDB under Goal-Driven Mode (/goal).
Full multi-module embedded document database system:
  - storage.py (Disk persistence & record encoding)
  - index.py (Unique/non-unique indexing & fast lookup)
  - query.py (MongoDB-style query operator parser & evaluator: $gt, $in, $exists, $or, etc.)
  - collection.py (CRUD, atomic update operators $set/$inc/$unset, sort/skip/limit)
  - database.py (Multi-collection management, drop, flush, context manager)
Evaluates Native Control Arm vs Orchestrated Offload Arm under Goal-driven thorough execution.
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
NATIVE_WORKSPACE = ROOT / ".trajectory/docdb-native"
OFFLOAD_WORKSPACE = ROOT / ".trajectory/docdb-offload"
ORACLE_FILE = ROOT / "benchmarks/docdb_oracle.py"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

GOAL_DIRECTIVE = """[EXECUTION MODE: /goal (GOAL-DRIVEN THOROUGH EXECUTION)]
You are operating under the /goal directive. Your mission is absolute, end-to-end delivery of a complete, production-grade micro-system.
Rules:
- Do NOT stop until all modules and integration flows are 100% implemented and tested.
- Implement every component robustly with full functionality (no placeholders, no mocked returns).
- Test exhaustively using pytest. Fix every failure. Call finish_task ONLY when all acceptance criteria are fully met.
"""

TASK_SPECIFICATION = GOAL_DIRECTIVE + """
Objective: Build a comprehensive embedded document database called `MiniDocDB` from scratch.

Module Layout:
- `src/mini_docdb/__init__.py`: exports `Database`, `Collection`, `DuplicateKeyError`, `DocumentNotFoundError`.
- `src/mini_docdb/storage.py`: Handles serialization, JSON/JSONL file persistence, atomic flush, and directory management.
- `src/mini_docdb/index.py`: Manages field indexes (unique and non-unique), lookup acceleration, and duplicate key violation checks.
- `src/mini_docdb/query.py`: Evaluates MongoDB-style query filters on document dictionaries (supports nested dot notation e.g. 'user.profile.city').
- `src/mini_docdb/collection.py`: Provides collection-level CRUD, update operators, indexing, and sorting/pagination.
- `src/mini_docdb/database.py`: Top-level database container managing collections, persistence directory, drop, and context management.

Detailed Specification:

1. Exceptions:
   - `class DuplicateKeyError(ValueError): pass`
   - `class DocumentNotFoundError(KeyError): pass`

2. Query Operators in `query.py`:
   - Comparison: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`
   - Element: `$exists` (boolean True/False)
   - Logical: `$and` (list of sub-filters), `$or` (list of sub-filters)
   - Plain key match: `{"age": 25}` is equivalent to `{"age": {"$eq": 25}}`
   - Dot notation support for nested dictionary lookup: e.g. `"user.profile.city"`

3. Update Operators in `collection.py`:
   - `$set`: updates or adds specified fields. (Note: cannot change `_id`).
   - `$unset`: removes specified fields from the document.
   - `$inc`: increments numeric fields by given number (initializes to value if field absent).

4. Collection in `collection.py`:
   - `insert_one(doc: dict) -> str`: assigns unique string `_id` (e.g. hex uuid) if absent, enforces unique indexes, returns `_id`.
   - `insert_many(docs: list[dict]) -> list[str]`: inserts multiple documents atomically.
   - `find_one(filter: dict = None) -> dict | None`: returns copy of first matching document, or None.
   - `find(filter: dict = None, sort: tuple[str, bool] = None, skip: int = None, limit: int = None) -> list[dict]`:
     returns list of copies matching filter. `sort=("field", ascending_bool)`.
   - `update_one(filter: dict, update: dict) -> bool`: applies update operators to first matching document, updates indexes. Returns True if modified.
   - `update_many(filter: dict, update: dict) -> int`: applies update operators to all matching documents. Returns modified count.
   - `delete_one(filter: dict) -> bool`: deletes first matching document and updates indexes. Returns True if deleted.
   - `delete_many(filter: dict) -> int`: deletes all matching documents. Returns deleted count.
   - `count(filter: dict = None) -> int`: returns count of matching documents.
   - `create_index(field: str, unique: bool = False) -> None`: builds index on field. If unique=True, verifies existing data and raises DuplicateKeyError on conflict.

5. Database in `database.py`:
   - `Database(path: str | Path = None)`: if path is provided, enables disk persistence. If path is None, operates entirely in memory.
   - `get_collection(name: str) -> Collection` or `db[name]`
   - `list_collections() -> list[str]`
   - `drop_collection(name: str) -> bool`
   - `flush() -> None`: writes dirty collections and index metadata to disk (if path configured).
   - `close() -> None`: flushes and closes database.
   - Context manager: `with Database(path) as db:` automatically calls flush() / close() on exit.
   - Persistence reload: when a new Database(path) points to an existing directory, it reloads all collections, documents, and index configurations.

Requirements:
- Robust multi-file architecture with clear type hints.
- Write extensive integration tests in `tests/test_docdb.py`.
- Run pytest to verify all tests pass before calling finish_task.
"""

def run_oracle(workspace: Path) -> dict:
    """Run the external independent 20-test Oracle suite against the workspace."""
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
    proc = subprocess.run(cmd, cwd=str(workspace), capture_output=True, text=True, env=env, timeout=45)
    dur = round(time.monotonic() - t0, 3)
    
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
        "total": 20,
        "pass_rate": f"{(passed / 20) * 100:.1f}%",
        "duration": dur,
        "stdout": proc.stdout[-500:] if len(proc.stdout) > 500 else proc.stdout,
        "stderr": proc.stderr[-500:] if len(proc.stderr) > 500 else proc.stderr
    }


def execute_native_arm() -> dict:
    print("\n" + "="*70)
    print(">>> Starting Native Control Arm under /goal directive (MiniDocDB) ...")
    print("="*70, flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    NATIVE_WORKSPACE.mkdir(parents=True, exist_ok=True)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    
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
                "description": "Declare system completed under /goal directive.",
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
            "content": "You are a distinguished system architect operating under the /goal directive. "
                       "Build the full MiniDocDB system across all requested modules. "
                       "Verify thoroughly using run_pytest. Do not stop until all components are production-ready."
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
    max_turns = 16

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
            print(f"[Native Arm] Model plain message: {msg.content[:80]}...", flush=True)
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
                    "content": json.dumps({"status": "ok", "path": rel_path})
                })
            elif fname == "read_file":
                rel_path = args["path"].lstrip("/\\")
                target = NATIVE_WORKSPACE / rel_path
                if target.is_dir():
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": "path is a directory"})
                    })
                elif target.is_file():
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "ok", "content": target.read_text(encoding="utf-8")})
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
                        "content": json.dumps({"status": "ok", "files": files[:40]})
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
                proc = subprocess.run(cmd, cwd=str(NATIVE_WORKSPACE), capture_output=True, text=True, env=env, timeout=40)
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
        "arm": "Native Control Arm (/goal)",
        "wall_clock_seconds": round(dur, 2),
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "turns": turns,
        "oracle": oracle_res
    }


def execute_offload_arm() -> dict:
    print("\n" + "="*70)
    print(">>> Starting Orchestrated Offload Arm under /goal directive (MiniDocDB) ...")
    print("="*70, flush=True)

    if OFFLOAD_WORKSPACE.exists():
        shutil.rmtree(OFFLOAD_WORKSPACE, ignore_errors=True)
    OFFLOAD_WORKSPACE.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0

    # 1. Zero-Token Deterministic Scaffolding for multi-module system (< 10ms, 0 Token)
    print("[Offload Arm] Stage 1: Zero-Token Multi-Module Scaffolding initializing...", flush=True)
    src_dir = OFFLOAD_WORKSPACE / "src" / "mini_docdb"
    test_dir = OFFLOAD_WORKSPACE / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    (src_dir / "__init__.py").write_text(
        "from .database import Database\n"
        "from .collection import Collection\n"
        "from .storage import DuplicateKeyError, DocumentNotFoundError\n\n"
        "__all__ = ['Database', 'Collection', 'DuplicateKeyError', 'DocumentNotFoundError']\n",
        encoding="utf-8"
    )
    # Scaffold skeleton stubs
    (src_dir / "storage.py").write_text(
        "class DuplicateKeyError(ValueError): pass\n"
        "class DocumentNotFoundError(KeyError): pass\n\n"
        "class StorageEngine:\n"
        "    def __init__(self, path=None): self.path = path\n"
        "    def save(self, collection_name, data, indexes): pass\n"
        "    def load(self, collection_name): return {}, {}\n"
        "    def list_collections(self): return []\n"
        "    def drop_collection(self, name): return False\n",
        encoding="utf-8"
    )
    (src_dir / "index.py").write_text(
        "class IndexManager:\n"
        "    def __init__(self): self.indexes = {}\n"
        "    def create_index(self, field, unique=False): pass\n"
        "    def add_doc(self, doc_id, doc): pass\n"
        "    def remove_doc(self, doc_id, doc): pass\n"
        "    def check_unique(self, doc, exclude_id=None): pass\n"
        "    def lookup(self, field, val): return None\n",
        encoding="utf-8"
    )
    (src_dir / "query.py").write_text(
        "class QueryMatcher:\n"
        "    @staticmethod\n"
        "    def match(doc: dict, filter_expr: dict) -> bool:\n"
        "        return True\n",
        encoding="utf-8"
    )
    (src_dir / "collection.py").write_text(
        "class Collection:\n"
        "    def __init__(self, name, storage=None):\n"
        "        self.name = name; self.storage = storage; self._docs = {}\n"
        "    def insert_one(self, doc): pass\n"
        "    def insert_many(self, docs): pass\n"
        "    def find(self, filter=None, sort=None, skip=None, limit=None): return []\n"
        "    def find_one(self, filter=None): return None\n"
        "    def update_one(self, filter, update): return False\n"
        "    def update_many(self, filter, update): return 0\n"
        "    def delete_one(self, filter): return False\n"
        "    def delete_many(self, filter): return 0\n"
        "    def count(self, filter=None): return 0\n"
        "    def create_index(self, field, unique=False): pass\n",
        encoding="utf-8"
    )
    (src_dir / "database.py").write_text(
        "from .collection import Collection\n"
        "class Database:\n"
        "    def __init__(self, path=None): self.path = path; self._collections = {}\n"
        "    def get_collection(self, name): return self[name]\n"
        "    def __getitem__(self, name): pass\n"
        "    def list_collections(self): return []\n"
        "    def drop_collection(self, name): return False\n"
        "    def flush(self): pass\n"
        "    def close(self): pass\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, exc_type, exc, tb): self.close()\n",
        encoding="utf-8"
    )

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

    # 2. Primary Agent: Implements core modular architecture
    print("[Offload Arm] Stage 2: Main Agent implementing multi-module system under /goal...", flush=True)
    offload_prompt = (
        "The multi-module project scaffold has already been initialized with skeleton files:\n"
        "- `src/mini_docdb/storage.py`\n"
        "- `src/mini_docdb/index.py`\n"
        "- `src/mini_docdb/query.py`\n"
        "- `src/mini_docdb/collection.py`\n"
        "- `src/mini_docdb/database.py`\n\n"
        "Your task: implement all modules fully according to the /goal directive.\n"
        "Write initial integration tests in `tests/test_docdb.py`.\n"
        "Use write_file to save the modules, and call verify_patch to test."
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
                "description": "Run fast differential verification on tests/test_docdb.py.",
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
        {"role": "system", "content": "You are a master systems engineer operating under the /goal directive. Implement clean, modular code with zero redundancy."},
        {"role": "user", "content": f"{TASK_SPECIFICATION}\n\n{offload_prompt}"}
    ]

    max_turns = 16
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
            print(f"[Offload Arm] Main Agent plain response: {msg.content[:80]}...", flush=True)
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
                test_file = OFFLOAD_WORKSPACE / "tests" / "test_docdb.py"
                if not test_file.exists():
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps({"status": "error", "message": "tests/test_docdb.py does not exist yet."})
                    })
                else:
                    cmd = [sys.executable, "-m", "pytest", "-q", "--color=no", str(test_file)]
                    t_v0 = time.monotonic()
                    proc = subprocess.run(cmd, cwd=str(OFFLOAD_WORKSPACE), capture_output=True, text=True, env=env, timeout=20)
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

    # 3. Stage 3: Specialized Coverage Gap Agent (Edge Case Generation on query/collection)
    print("[Offload Arm] Stage 3: Specialized Edge-Case Coverage Augmenter running ...", flush=True)
    from trajectory_offload.contracts import Settings, CoverageAugmentTask, CheckSpec, ExecutionPolicy
    from trajectory_offload.repository import Snapshot
    from trajectory_offload.specialized import augment_coverage_edge_cases
    from trajectory_offload.patching import apply_patch_to_copy

    settings = Settings(
        model=MODEL,
        reasoning_effort="low",
        max_output_tokens=4000,
        max_total_tokens=120000,
        max_seconds=120,
        prefetch_context=True
    )
    snapshot = Snapshot.capture(OFFLOAD_WORKSPACE, settings)
    target_rel = "src/mini_docdb/collection.py"
    test_rel = "tests/test_coverage_edge.py"

    check = CheckSpec(
        id="unit_regression",
        argv=["{python}", "-m", "pytest", "-q", "{test_file}", "{junit_option}"],
        timeout_seconds=30
    )
    policy = ExecutionPolicy(checks=[check])

    cov_task = CoverageAugmentTask(
        objective=(
            "Add edge-case coverage tests for Collection: duplicate key error on unique index, "
            "empty queries, update operators ($set, $inc, $unset), sort/skip/limit boundary conditions."
        ),
        snapshot_id=snapshot.snapshot_id,
        target_path=target_rel,
        test_file=test_rel,
        check_id="unit_regression",
        scope=[target_rel, "src/mini_docdb/query.py", "src/mini_docdb/database.py", "src/mini_docdb/__init__.py"]
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
        "arm": "Orchestrated Offload Arm (/goal)",
        "wall_clock_seconds": round(dur, 2),
        "total_tokens": total_prompt_tokens + total_completion_tokens + subagent_tokens,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "turns": turns + 1,
        "oracle": oracle_res
    }


def main():
    print("=" * 80)
    print("  MEDIUM-SCALE SYSTEM GREENFIELD: MINI-DOC-DB (/goal DIRECTIVE)")
    print("=" * 80)

    # 1. Run Native Arm under /goal
    native_result = execute_native_arm()

    # 2. Run Offload Arm under /goal
    offload_result = execute_offload_arm()

    print("\n" + "=" * 80)
    print("  FINAL MEDIUM-SCALE SYSTEM RESULTS (/goal)")
    print("=" * 80)

    res_table = [
        ["Metric", "Native Control Arm (/goal)", "Orchestrated Offload Arm (/goal)", "Advantage / Delta"],
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
        print(f"{row[0]:<24} | {row[1]:<28} | {row[2]:<32} | {row[3]}")

    print("=" * 80)

    out_file = ROOT / ".trajectory/docdb_goal_ab_result.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "native": native_result,
        "offload": offload_result
    }, indent=2), encoding="utf-8")
    print(f"Results written to: {out_file}")

if __name__ == "__main__":
    main()
