"""Greenfield Benchmark: Arcade Tetris Game under Goal-Driven Mode (/goal).
Full official-guideline-compliant Tetris Game:
  - 10x20 Matrix Playfield
  - 7 Standard Tetrominoes (I, J, L, O, S, T, Z) with 4 rotation states
  - 7-Bag Randomizer
  - SRS (Super Rotation System) Wall Kicks
  - Ghost Piece Projection Shadow
  - Hold Piece swapping mechanism
  - Hard Drop & Soft Drop with Lock Delay
  - Line clear detection, row shift collapse, and scoring
  - Level progression & gravity speed acceleration
  - Next queue preview & high score localStorage persistence
Evaluates Native Control Arm vs Outboard Offload Arm under Goal-driven thorough execution (Single A/B run, N=1).
"""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
NATIVE_WORKSPACE = ROOT / ".trajectory/tetris-native"
OFFLOAD_WORKSPACE = ROOT / ".trajectory/tetris-offload"
ORACLE_FILE = ROOT / "benchmarks/tetris_oracle.py"
RESULT_FILE = ROOT / ".trajectory/tetris_ab_result.json"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

GOAL_DIRECTIVE = """[EXECUTION MODE: /goal (GOAL-DRIVEN THOROUGH EXECUTION)]
You are operating under the /goal directive. Your mission is absolute, end-to-end delivery of a complete, polished, official-guideline-compliant arcade Tetris game.
Rules:
- Do NOT stop until all modules, mechanics, and integration flows are 100% implemented, wired, and verified.
- Implement every component robustly with full functionality (no placeholders, no mocked returns, no '// TODO').
- Test exhaustively. Call finish_task ONLY when all acceptance criteria are fully met.
"""

TETRIS_SPECIFICATION = """
# 项目任务：从零构建一个符合国际官方指南（Tetris Guideline）的高品质街机版俄罗斯方块

你是一名拥有丰富经验的资深游戏开发工程师和前端架构师。
你的任务是：独立设计并从零开发一个可以实际运行和游玩的高品质街机风格俄罗斯方块（Tetris Arcade）。
这不是一个粗制滥造的简陋 Demo。我要求你交付一个具有丝滑操作手感、完整官方规则、高质感 UI 和音效/粒子反馈的可玩产品。

## 一、核心游戏机制（严格遵循官方 Tetris Guideline 规范）
1. 标准游戏棋盘：
   - 10 列 x 20 行的标准游戏区域（Matrix Playfield）。
2. 标准 7 种 Tetromino 方块与 4 态旋转：
   - 包含 I, J, L, O, S, T, Z 七种经典方块，各自具备独立的 4 种旋转状态（0°, 90°, 180°, 270°）矩阵。
3. 官方 7-Bag 随机袋算法：
   - 严格采用 7 个方块打乱随机派发的 7-Bag 发生器，杜绝极端连出或卡方块。
4. SRS（Super Rotation System）超级旋转与踢墙：
   - 实现贴近左右边界或障碍物时的踢墙偏移（Wall Kicks）检测，旋转不卡死。
5. 幽灵投影块（Ghost Piece）：
   - 在方块垂直落点实时绘制半透明或虚线轮廓的落点虚影。
6. 暂存机制（Hold Piece）：
   - 支持按 C 或 Shift 随时将当前方块暂存并调出备用块（每回合限换 1 次）。
7. 操作手感与控制：
   - 空格键：硬降（Hard Drop）瞬间锁定并获得额外加分；
   - 下方向键：软降（Soft Drop）加速下沉；
   - 触底锁定延迟（Lock Delay，~500ms），给玩家提供滑动微调窗口。
8. 消除、计分与等级系统：
   - 满行检测、行消除与上方方块整体下落位移；
   - 单消、双消、三消与 TETRIS（四消）阶梯式计分与消除反馈；
   - 随消除行数提升游戏等级（Level），按级别递增重力下落速度。
9. 界面与辅助功能：
   - Next 队列预览（展示后续至少 3 个方块）；
   - 当前得分、当前等级、消除行数、最高分（LocalStorage 持久化）；
   - 开始界面、游戏暂停与 Game Over 判定。
"""

TASK_SPECIFICATION = GOAL_DIRECTIVE + TETRIS_SPECIFICATION

def run_oracle(workspace: Path) -> dict:
    """Run external independent 20-test Oracle suite against workspace."""
    ws_abs = workspace.resolve()
    env = os.environ.copy()
    env["TETRIS_WORKSPACE"] = str(ws_abs)
    t0 = time.monotonic()
    cmd = [
        sys.executable,
        "-m", "pytest",
        "-q",
        "--color=no",
        str(ORACLE_FILE),
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=30)
        dur = round(time.monotonic() - t0, 3)
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except Exception as e:
        return {"passed": 0, "failed": 20, "total": 20, "pass_rate": "0.0%", "duration": 30.0, "error": str(e)}

    passed = 0
    failed = 0
    for line in stdout.splitlines():
        if "passed" in line or "failed" in line:
            m_pass = re.search(r"(\d+) passed", line)
            m_fail = re.search(r"(\d+) failed", line)
            if m_pass:
                passed = int(m_pass.group(1))
            if m_fail:
                failed = int(m_fail.group(1))

    total = 20
    pass_rate = f"{(passed / total) * 100:.1f}%" if total > 0 else "0.0%"
    return {
        "exit_code": exit_code,
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate": pass_rate,
        "duration": dur,
        "stdout": stdout[-500:],
    }

def calculate_costs(prompt_tokens: int, completion_tokens: int, cache_hit_rate: float = 0.90) -> dict:
    hit_tokens = prompt_tokens * cache_hit_rate
    miss_tokens = prompt_tokens * (1.0 - cache_hit_rate)
    peak_cost_cny = (hit_tokens * 0.043 + miss_tokens * 2.16 + completion_tokens * 8.64) / 1_000_000
    offpeak_cost_cny = peak_cost_cny * 0.5
    return {
        "peak_cost_cny": round(peak_cost_cny, 4),
        "offpeak_cost_cny": round(offpeak_cost_cny, 4),
        "peak_cost_usd": round(peak_cost_cny / 7.2, 4),
    }

# =========================================================================
# ARM 1: Native Control Arm
# =========================================================================
def execute_native_arm() -> dict:
    print(f"\n{'='*70}")
    print(f">>> Starting Arm 1: Native Control Arm on Tetris Arcade (/goal) ...")
    print(f"{'='*70}", flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    NATIVE_WORKSPACE.mkdir(parents=True, exist_ok=True)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior game engineer building an official-guideline-compliant arcade Tetris game from scratch under /goal directive. Write all HTML, CSS, and JavaScript cleanly."},
        {"role": "user", "content": TASK_SPECIFICATION}
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Run shell command in workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create or overwrite a file in workspace.",
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
                "description": "Read file contents.",
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
                "name": "list_directory",
                "description": "List files in directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "finish_task",
                "description": "Finish task when all Tetris requirements are completely fulfilled.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"]
                }
            }
        }
    ]

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0
    finished = False
    max_turns = 20

    while turns < max_turns and not finished:
        turns += 1
        print(f"\n[Native Arm] Turn {turns} sending request...", flush=True)

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            print(f"[Native Arm] API error: {e}", flush=True)
            time.sleep(2)
            continue

        usage = resp.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens or 0
            total_completion_tokens += usage.completion_tokens or 0

        choice = resp.choices[0]
        msg = choice.message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"[Native Arm] Text reply: {msg.content[:150] if msg.content else 'None'}...", flush=True)
            oracle_res = run_oracle(NATIVE_WORKSPACE)
            print(f"[Native Arm] Oracle check: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
            if oracle_res["passed"] == 20:
                print(f"[Native Arm] Goal achieved! 20/20 Oracle tests passed.", flush=True)
                finished = True
                break
            messages.append({"role": "user", "content": "Please continue implementing the Tetris game mechanics: board, tetrominoes, 7-bag, SRS kicks, ghost piece, hold, scoring, and UI."})
            continue

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            raw_args = tc.function.arguments
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}

            print(f"[Native Arm] Tool call: {fn_name}({str(args)[:100]}...)", flush=True)
            res_content = ""

            if fn_name == "write_file":
                p = NATIVE_WORKSPACE / args.get("path", "")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args.get("content", ""), encoding="utf-8")
                res_content = f"Successfully wrote {len(args.get('content', ''))} characters to {args.get('path')}."
            elif fn_name == "read_file":
                p = NATIVE_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_file():
                    res_content = p.read_text(encoding="utf-8", errors="replace")[:4000]
                elif p.exists() and p.is_dir():
                    res_content = "Directory listing:\n" + "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = f"Error: File not found: {args.get('path')}"
            elif fn_name == "list_directory":
                p = NATIVE_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_dir():
                    res_content = "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = "Directory not found or empty."
            elif fn_name == "execute_command":
                cmd = args.get("command", "")
                try:
                    proc = subprocess.run(cmd, shell=True, cwd=str(NATIVE_WORKSPACE), capture_output=True, text=True, timeout=30)
                    res_content = f"Exit code {proc.returncode}\nStdout: {proc.stdout[:1500]}\nStderr: {proc.stderr[:1000]}"
                except Exception as e:
                    res_content = f"Execution error: {e}"
            elif fn_name == "finish_task":
                finished = True
                res_content = "Task completion acknowledged."

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": res_content})

        oracle_res = run_oracle(NATIVE_WORKSPACE)
        print(f"[Native Arm] Oracle check: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
        if oracle_res["passed"] == 20:
            print(f"[Native Arm] Goal achieved! 20/20 Oracle tests passed.", flush=True)
            finished = True

    wall_clock = round(time.monotonic() - t0, 3)
    oracle_res = run_oracle(NATIVE_WORKSPACE)
    costs = calculate_costs(total_prompt_tokens, total_completion_tokens)

    return {
        "arm": "Native Control Arm",
        "wall_clock_seconds": wall_clock,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "turns": turns,
        "oracle": oracle_res,
        "costs": costs
    }

# =========================================================================
# ARM 2: Outboard Offload Arm
# =========================================================================
def execute_offload_arm() -> dict:
    print(f"\n{'='*70}")
    print(f">>> Starting Arm 2: Outboard Offload Arm on Tetris Arcade (/goal) ...")
    print(f"{'='*70}", flush=True)

    if OFFLOAD_WORKSPACE.exists():
        shutil.rmtree(OFFLOAD_WORKSPACE, ignore_errors=True)
    OFFLOAD_WORKSPACE.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0
    offload_count = 0

    # Stage 1: Zero-Token Architecture Scaffolding (< 5ms, 0 Token)
    print(f"[Offload Arm] Stage 1: Zero-Token Architecture Scaffolding...", flush=True)

    (OFFLOAD_WORKSPACE / "index.html").write_text("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>Tetris Arcade</title>
  <style>
    body { background: #0a0c14; color: #fff; font-family: monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
    #game { display: flex; gap: 20px; }
    .panel { background: #121624; border: 1px solid #222940; border-radius: 8px; padding: 12px; }
    canvas { background: #000; border: 2px solid #334; border-radius: 4px; display: block; }
  </style>
</head>
<body>
  <div id="game">
    <div class="panel">
      <h3>HOLD</h3>
      <canvas id="hold-canvas" width="90" height="70"></canvas>
      <div style="margin-top: 10px;">
        <div>SCORE: <span id="score">0</span></div>
        <div>LEVEL: <span id="level">1</span></div>
        <div>LINES: <span id="lines">0</span></div>
      </div>
    </div>
    <div class="panel">
      <canvas id="board-canvas" width="300" height="600"></canvas>
    </div>
    <div class="panel">
      <h3>NEXT</h3>
      <canvas id="next-canvas" width="90" height="200"></canvas>
      <div style="margin-top: 10px;">
        <div>TOP: <span id="top-score">0</span></div>
      </div>
    </div>
  </div>
  <script src="game.js"></script>
</body>
</html>""", encoding="utf-8")

    (OFFLOAD_WORKSPACE / "game.js").write_text("""// Tetris Core Architecture Skeleton
const COLS = 10;
const ROWS = 20;
const BLOCK_SIZE = 30;

// 7 Standard Shapes
const SHAPES = {
  I: { color: '#00f0f0', matrices: [[[0,0,0,0],[1,1,1,1],[0,0,0,0],[0,0,0,0]]] },
  J: { color: '#0055ff', matrices: [[[1,0,0],[1,1,1],[0,0,0]]] },
  L: { color: '#ff8800', matrices: [[[0,0,1],[1,1,1],[0,0,0]]] },
  O: { color: '#f0e000', matrices: [[[1,1],[1,1]]] },
  S: { color: '#00e040', matrices: [[[0,1,1],[1,1,0],[0,0,0]]] },
  T: { color: '#aa00ff', matrices: [[[0,1,0],[1,1,1],[0,0,0]]] },
  Z: { color: '#ff2244', matrices: [[[1,1,0],[0,1,1],[0,0,0]]] },
};
""", encoding="utf-8")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior game engineer operating under Outboard Offload Engine & /goal directive. Zero-token scaffolding has established the layout, constants, and HTML canvas. Complete all Tetris mechanics: 7-bag, SRS wall-kick, ghost piece, hold piece, hard/soft drop, line clears, scoring, and UI controls."},
        {"role": "user", "content": TASK_SPECIFICATION}
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Create or overwrite a file in workspace.",
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
                "name": "edit_file",
                "description": "Perform precise diff replacements in files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "target": {"type": "string"},
                        "replacement": {"type": "string"}
                    },
                    "required": ["path", "target", "replacement"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read file contents.",
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
                "name": "list_directory",
                "description": "List files in directory.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "verify_oracle",
                "description": "Run the independent 20-assertion Tetris Oracle suite.",
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
                "name": "finish_task",
                "description": "Finish task when all 20 Oracle tests pass.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"]
                }
            }
        }
    ]

    max_turns = 20
    finished = False

    while turns < max_turns and not finished:
        turns += 1
        print(f"\n[Offload Arm] Turn {turns} sending request...", flush=True)

        oracle_res = run_oracle(OFFLOAD_WORKSPACE)
        print(f"[Offload Arm] Oracle status: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
        if oracle_res["passed"] == 20:
            print(f"[Offload Arm] Fast path goal convergence! 20/20 Oracle tests passed.", flush=True)
            finished = True
            break

        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as e:
            print(f"[Offload Arm] API error: {e}", flush=True)
            time.sleep(2)
            continue

        usage = resp.usage
        if usage:
            total_prompt_tokens += usage.prompt_tokens or 0
            total_completion_tokens += usage.completion_tokens or 0

        choice = resp.choices[0]
        msg = choice.message
        messages.append(msg)

        if not msg.tool_calls:
            print(f"[Offload Arm] Text reply: {msg.content[:150] if msg.content else 'None'}...", flush=True)
            oracle_res = run_oracle(OFFLOAD_WORKSPACE)
            if oracle_res["passed"] == 20:
                finished = True
                break
            messages.append({"role": "user", "content": "Please complete the Tetris implementation to achieve 20/20 Oracle pass."})
            continue

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            raw_args = tc.function.arguments
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}

            print(f"[Offload Arm] Tool call: {fn_name}({str(args)[:100]}...)", flush=True)
            res_content = ""

            if fn_name == "write_file":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(args.get("content", ""), encoding="utf-8")
                res_content = f"Successfully wrote {len(args.get('content', ''))} characters to {args.get('path')}."
            elif fn_name == "edit_file":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_file():
                    c = p.read_text(encoding="utf-8")
                    target = args.get("target", "")
                    replacement = args.get("replacement", "")
                    if target in c:
                        c = c.replace(target, replacement)
                        p.write_text(c, encoding="utf-8")
                        res_content = f"Successfully edited {args.get('path')}."
                    else:
                        res_content = f"Error: target string not found in {args.get('path')}."
                else:
                    res_content = f"Error: file not found: {args.get('path')}"
            elif fn_name == "read_file":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_file():
                    res_content = p.read_text(encoding="utf-8", errors="replace")[:4000]
                elif p.exists() and p.is_dir():
                    res_content = "Directory listing:\n" + "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = f"Error: file not found {args.get('path')}"
            elif fn_name == "list_directory":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_dir():
                    res_content = "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = "Directory not found or empty."
            elif fn_name == "verify_oracle":
                ores = run_oracle(OFFLOAD_WORKSPACE)
                res_content = f"Oracle passed: {ores['passed']}/{ores['total']} ({ores['pass_rate']})\nDetails: {ores['stdout']}"
            elif fn_name == "finish_task":
                finished = True
                res_content = "Task finished."

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": res_content})

        oracle_res = run_oracle(OFFLOAD_WORKSPACE)
        if oracle_res["passed"] == 20:
            print(f"[Offload Arm] Goal achieved! 20/20 Oracle tests passed.", flush=True)
            finished = True

    wall_clock = round(time.monotonic() - t0, 3)
    oracle_res = run_oracle(OFFLOAD_WORKSPACE)
    costs = calculate_costs(total_prompt_tokens, total_completion_tokens)

    return {
        "arm": "Outboard Offload Arm",
        "wall_clock_seconds": wall_clock,
        "prompt_tokens": total_prompt_tokens,
        "completion_tokens": total_completion_tokens,
        "total_tokens": total_prompt_tokens + total_completion_tokens,
        "turns": turns,
        "offload_count": offload_count,
        "oracle": oracle_res,
        "costs": costs
    }

def main():
    print(f"\n{'#'*75}")
    print(f"### Greenfield Benchmark: Tetris Arcade Game (/goal Directive) ###")
    print(f"{'#'*75}")

    # Run Native Arm
    native_res = execute_native_arm()

    # Run Offload Arm
    offload_res = execute_offload_arm()

    # Calculate Comparison Delta
    time_saved = round(native_res["wall_clock_seconds"] - offload_res["wall_clock_seconds"], 2)
    time_pct = round((time_saved / native_res["wall_clock_seconds"]) * 100, 1) if native_res["wall_clock_seconds"] > 0 else 0.0
    tokens_saved = native_res["total_tokens"] - offload_res["total_tokens"]
    tokens_pct = round((tokens_saved / native_res["total_tokens"]) * 100, 1) if native_res["total_tokens"] > 0 else 0.0
    cost_saved = round(native_res["costs"]["peak_cost_cny"] - offload_res["costs"]["peak_cost_cny"], 4)
    cost_pct = round((cost_saved / native_res["costs"]["peak_cost_cny"]) * 100, 1) if native_res["costs"]["peak_cost_cny"] > 0 else 0.0

    delta = {
        "time_speedup_seconds": time_saved,
        "time_speedup_percent": f"{time_pct}% faster",
        "tokens_saved_count": tokens_saved,
        "tokens_saved_percent": f"{tokens_pct}%",
        "cost_saved_cny_peak": cost_saved,
        "cost_saved_percent": f"{cost_pct}%",
        "turns_reduced": native_res["turns"] - offload_res["turns"],
    }

    final_result = {
        "task": "Arcade Tetris Game (/goal directive)",
        "native": native_res,
        "offload": offload_res,
        "delta": delta
    }

    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(final_result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*75}")
    print(f"### FINAL BENCHMARK RESULTS PERSISTED TO: {RESULT_FILE} ###")
    print(f"{'='*75}")
    print(f"Native Arm:  Time={native_res['wall_clock_seconds']}s | Tokens={native_res['total_tokens']:,} | Pass={native_res['oracle']['pass_rate']} | Cost=CNY {native_res['costs']['peak_cost_cny']}")
    print(f"Offload Arm: Time={offload_res['wall_clock_seconds']}s | Tokens={offload_res['total_tokens']:,} | Pass={offload_res['oracle']['pass_rate']} | Cost=CNY {offload_res['costs']['peak_cost_cny']}")
    print(f"Delta:       Time -{time_pct}% | Tokens -{tokens_pct}% | Cost -{cost_pct}% | Turns -{delta['turns_reduced']}")

if __name__ == "__main__":
    main()
