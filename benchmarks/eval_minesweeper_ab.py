"""Greenfield Benchmark: Minesweeper Game under Goal-Driven Mode (/goal).
Full standard-compliant Minesweeper:
  - 3 Difficulty Presets (Beginner 9x9/10, Intermediate 16x16/40, Expert 30x16/99)
  - First-click safety guarantee (never hit mine on click 1)
  - 8-neighbor adjacent mine calculation
  - Zero-cascade flood fill expansion (BFS/DFS)
  - Flagging & Question mark cycle (? toggle)
  - Chording (double click / dual button quick reveal)
  - LCD Mine counter & Timer (0-999)
  - Win & Loss condition detection & input freeze
  - Smiley reset button (states: smile, surprise, dead, cool)
  - Best time persistence per difficulty in localStorage
Evaluates Native Control Arm vs Outboard Offload Arm under Goal-driven mode without artificial turn limits (N=1).
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
NATIVE_WORKSPACE = ROOT / ".trajectory/minesweeper-native"
OFFLOAD_WORKSPACE = ROOT / ".trajectory/minesweeper-offload"
ORACLE_FILE = ROOT / "benchmarks/minesweeper_oracle.py"
RESULT_FILE = ROOT / ".trajectory/minesweeper_ab_result.json"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

GOAL_DIRECTIVE = """[EXECUTION MODE: /goal (GOAL-DRIVEN THOROUGH EXECUTION)]
You are operating under the /goal directive with NO tight turn limits. Your mission is absolute, end-to-end delivery of a complete, polished, standard-compliant Minesweeper game.
Rules:
- Do NOT stop until all modules, mechanics, UI, and integration flows are 100% implemented, wired, and verified.
- Implement every component robustly with full functionality (no placeholders, no mocked returns, no '// TODO').
- Test exhaustively. Call finish_task ONLY when all acceptance criteria are fully met.
"""

MINESWEEPER_SPECIFICATION = """
# 项目任务：从零构建一个经典高品质网页版扫雷（Minesweeper Retro / Modern）

你是一名拥有丰富经验的资深游戏开发工程师和前端全栈架构师。
你的任务是：独立设计并从零开发一个可以实际在浏览器中流畅游玩的高品质经典扫雷游戏（Minesweeper）。
要求交付一个具有完整经典官方规则、丝滑操作手感、精致 UI、数字色彩高亮、计时计雷、连开和本地存档的高完成度作品。

## 一、核心游戏机制（严格遵循经典扫雷规范）
1. 三大标准难度预设（必须支持切换）：
   - 初级（Beginner）：9 列 x 9 行，10 颗雷
   - 中级（Intermediate）：16 列 x 16 行，40 颗雷
   - 高级（Expert）：30 列 x 16 行（或 16 列 x 30 行），99 颗雷
2. 绝对首击安全保障（First Click Safety Guarantee）：
   - 玩家的第一次左键点击绝对不能踩雷。必须在玩家首次点击后才生成地雷，或者如果首次点击命中地雷，立即将地雷重新随机移动到空位并更新所有周围数字。
3. 八方向相邻雷数精确计算：
   - 每个格子计算周围 8 个相邻格子（上下左右及 4 个对角线）中的地雷总数（0 ~ 8），不同数字使用经典区分色彩（1蓝、2绿、3红、4深蓝、5深红、6青、7黑、8灰）。
4. 空格级联空白自动展开（Flood Fill / Zero-Cascade）：
   - 当翻开一个数字为 0（周围没有任何雷）的格子时，自动通过广度优先（BFS）或深度优先（DFS）递归级联翻开所有相邻的非雷格子。
5. 插旗与标问号防护机制（Flagging & Question Mark）：
   - 右键点击可在“未标记 -> 插旗 🚩 -> 问号 ❓ -> 取消”状态间循环切换。
   - 被插旗保护的格子，左键点击无法意外触发翻开，防止误触。
6. 双击 / 双键双连翻开（Chording Mechanism）：
   - 当一个已经被翻开的数字格，其周围被插旗的格子数量等于该格显示的数字时，双击或双键点击该格，自动将其周围所有未插旗的格子翻开（若插错旗则会引爆炸弹 Game Over）。
7. 剩余雷数计数与计时器（Mine Counter & Timer）：
   - 实时显示剩余雷数（总地雷数 - 已插旗数）。
   - 计时器：从玩家第一次点击棋盘开始按秒自增（0~999），游戏获胜或失败时立刻停止计时。
8. 胜负判定与全盘结算：
   - 踩雷失败：游戏立刻结束，棋盘翻开所有地雷，将触发爆炸的地雷标红高亮，将插错旗的位置打红叉标出。
   - 翻全胜利：当所有非雷格子全部被翻开时判定获胜，自动将剩余地雷全部标记上旗帜。
   - 游戏结束（无论输赢）后冻结棋盘输入，点击不再翻开新格子。
9. 经典复古 UI 与交互细节：
   - 经典复位笑脸按钮（普通微笑 🙂、点击时张嘴 😮、踩雷哭脸/骷髅 😵、获胜戴墨镜 😎）。点击笑脸重开本难度新局。
   - 拦截右键系统默认菜单（`preventDefault()` on contextmenu）。
   - 本地持久化存储（localStorage）：分别记录初级、中级、高级各自的历史最佳通关时间（Best Time）。
"""

TASK_SPECIFICATION = f"{GOAL_DIRECTIVE}\n\n{MINESWEEPER_SPECIFICATION}"

def run_oracle(workspace: Path) -> dict[str, Any]:
    """Runs the independent Minesweeper acceptance oracle in the given workspace."""
    env = os.environ.copy()
    env["MINESWEEPER_WORKSPACE"] = str(workspace.resolve())
    env["PYTHONPATH"] = str(ROOT)

    t0 = time.monotonic()
    cmd = [sys.executable, "-m", "pytest", str(ORACLE_FILE), "-v", "--tb=short"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30
        )
        stdout = proc.stdout
        exit_code = proc.returncode
    except Exception as e:
        stdout = str(e)
        exit_code = 1

    dur = round(time.monotonic() - t0, 3)

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
# ARM 1: Native Control Arm (No artificial tight turn limit, max 60)
# =========================================================================
def execute_native_arm() -> dict:
    print(f"\n{'='*70}")
    print(f">>> Starting Arm 1: Native Control Arm on Minesweeper (/goal, unconstrained turns) ...")
    print(f"{'='*70}", flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    NATIVE_WORKSPACE.mkdir(parents=True, exist_ok=True)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior game engineer building an official-standard-compliant Minesweeper game from scratch under /goal directive with no tight turn limit. Write all HTML, CSS, and JavaScript cleanly."},
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
                "description": "Finish task when all Minesweeper requirements are completely fulfilled.",
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
    max_turns = 60  # Unconstrained by standard limits

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
            messages.append({"role": "user", "content": "Please continue implementing the Minesweeper game mechanics: difficulty presets, first-click safety, 8-neighbor scan, cascade flood fill, flags, chording, LCD counters, timer, win/loss, smiley reset, and localStorage."})
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
# ARM 2: Outboard Offload Arm (Zero-Token Scaffolding + Diff Hunk + Oracle Gate)
# =========================================================================
def execute_offload_arm() -> dict:
    print(f"\n{'='*70}")
    print(f">>> Starting Arm 2: Outboard Offload Arm on Minesweeper (/goal, unconstrained turns) ...")
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
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Minesweeper Retro · 扫雷经典版</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <div class="window">
    <div class="title-bar">
      <span class="title">💣 扫雷 (Minesweeper)</span>
      <div class="controls">
        <button id="btn-minimize">_</button>
        <button id="btn-close">X</button>
      </div>
    </div>
    <div class="menu-bar">
      <div class="difficulty-group">
        <button class="diff-btn active" data-diff="beginner">初级 (9x9)</button>
        <button class="diff-btn" data-diff="intermediate">中级 (16x16)</button>
        <button class="diff-btn" data-diff="expert">高级 (30x16)</button>
      </div>
    </div>
    <div class="game-container">
      <div class="status-bar">
        <div class="digital-lcd" id="mine-counter">010</div>
        <button id="smiley-btn" class="smiley">🙂</button>
        <div class="digital-lcd" id="timer">000</div>
      </div>
      <div id="board-container" class="board-frame">
        <div id="minefield" class="minefield"></div>
      </div>
      <div class="footer-bar">
        <span>BEST: <span id="best-time">--</span>s</span>
      </div>
    </div>
  </div>
  <script src="minesweeper.js"></script>
</body>
</html>
""", encoding="utf-8")

    (OFFLOAD_WORKSPACE / "style.css").write_text("""* { box-sizing: border-box; user-select: none; }
body {
  margin: 0;
  padding: 20px;
  background: #008080;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
}
.window {
  background: #c0c0c0;
  border-top: 3px solid #ffffff;
  border-left: 3px solid #ffffff;
  border-right: 3px solid #7b7b7b;
  border-bottom: 3px solid #7b7b7b;
  box-shadow: 2px 2px 10px rgba(0,0,0,0.5);
  padding: 3px;
  display: inline-block;
}
.title-bar {
  background: linear-gradient(90deg, #000080, #1084d0);
  color: white;
  padding: 3px 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: bold;
  font-size: 13px;
}
.menu-bar {
  display: flex;
  background: #c0c0c0;
  padding: 4px;
  gap: 4px;
  border-bottom: 2px solid #808080;
}
.diff-btn {
  background: #c0c0c0;
  border: 2px outset #ffffff;
  padding: 2px 8px;
  font-size: 12px;
  cursor: pointer;
}
.diff-btn.active {
  border: 2px inset #ffffff;
  background: #d4d0c8;
}
.game-container {
  padding: 8px;
}
.status-bar {
  background: #c0c0c0;
  border: 3px inset #ffffff;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  margin-bottom: 8px;
}
.digital-lcd {
  background: #000;
  color: #ff0000;
  font-family: 'Courier New', Courier, monospace;
  font-size: 24px;
  font-weight: bold;
  padding: 2px 6px;
  border: 2px inset #808080;
  letter-spacing: 2px;
}
.smiley {
  width: 36px;
  height: 36px;
  font-size: 20px;
  background: #c0c0c0;
  border: 3px outset #ffffff;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.smiley:active {
  border: 3px inset #ffffff;
}
.board-frame {
  border: 3px inset #ffffff;
  display: inline-block;
  background: #808080;
}
.minefield {
  display: grid;
  background: #c0c0c0;
}
.cell {
  width: 24px;
  height: 24px;
  border: 2px outset #ffffff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: bold;
  cursor: pointer;
}
.cell.revealed {
  border: 1px solid #7b7b7b;
  background: #c0c0c0;
}
.cell.mine-exploded { background: #ff0000; }
.cell.c-1 { color: #0000ff; }
.cell.c-2 { color: #008000; }
.cell.c-3 { color: #ff0000; }
.cell.c-4 { color: #000080; }
.cell.c-5 { color: #800000; }
.cell.c-6 { color: #008080; }
.cell.c-7 { color: #000000; }
.cell.c-8 { color: #808080; }
.footer-bar {
  margin-top: 6px;
  font-size: 12px;
  color: #333;
  text-align: right;
}
""", encoding="utf-8")

    (OFFLOAD_WORKSPACE / "minesweeper.js").write_text("""/**
 * Minesweeper Retro Engine & Controller
 */
const PRESETS = {
  beginner: { rows: 9, cols: 9, mines: 10 },
  intermediate: { rows: 16, cols: 16, mines: 40 },
  expert: { rows: 16, cols: 30, mines: 99 }
};
""", encoding="utf-8")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior game engineer operating under Outboard Offload Engine & /goal directive. Zero-token scaffolding has established the classic retro layout, CSS styling, and difficulty constants. Complete all Minesweeper mechanics in minesweeper.js: presets, first-click safety, 8-neighbor scan, cascade flood fill, flags, chording, LCD counters, timer, win/loss, smiley states, and localStorage."},
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
                "description": "Run the independent 20-assertion Minesweeper Oracle suite.",
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
                "description": "Declare completion when all Minesweeper requirements are completely met.",
                "parameters": {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}},
                    "required": ["summary"]
                }
            }
        }
    ]

    finished = False
    max_turns = 60  # Unconstrained by standard limits

    while turns < max_turns and not finished:
        turns += 1
        print(f"\n[Offload Arm] Turn {turns} sending request...", flush=True)

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
            print(f"[Offload Arm] Oracle check: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
            if oracle_res["passed"] == 20:
                print(f"[Offload Arm] Goal achieved! 20/20 Oracle tests passed.", flush=True)
                finished = True
                break
            messages.append({"role": "user", "content": "Please continue implementing all remaining Minesweeper mechanics in minesweeper.js."})
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
                    content = p.read_text(encoding="utf-8")
                    target = args.get("target", "")
                    replacement = args.get("replacement", "")
                    if target in content:
                        new_content = content.replace(target, replacement, 1)
                        p.write_text(new_content, encoding="utf-8")
                        res_content = f"Successfully replaced target in {args.get('path')}."
                    else:
                        res_content = f"Error: Target string not found in {args.get('path')}."
                else:
                    res_content = f"Error: File not found: {args.get('path')}"
            elif fn_name == "read_file":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_file():
                    res_content = p.read_text(encoding="utf-8", errors="replace")[:4000]
                elif p.exists() and p.is_dir():
                    res_content = "Directory listing:\n" + "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = f"Error: File not found: {args.get('path')}"
            elif fn_name == "list_directory":
                p = OFFLOAD_WORKSPACE / args.get("path", "")
                if p.exists() and p.is_dir():
                    res_content = "\n".join([f.name for f in p.iterdir()])
                else:
                    res_content = "Directory not found or empty."
            elif fn_name == "verify_oracle":
                offload_count += 1
                oracle_check = run_oracle(OFFLOAD_WORKSPACE)
                res_content = f"Oracle Check: {oracle_check['passed']}/{oracle_check['total']} passed ({oracle_check['pass_rate']}). Output snippet:\n{oracle_check['stdout']}"
            elif fn_name == "finish_task":
                finished = True
                res_content = "Task finished successfully."

            messages.append({"role": "tool", "tool_call_id": tc.id, "content": res_content})

        oracle_res = run_oracle(OFFLOAD_WORKSPACE)
        print(f"[Offload Arm] Oracle check: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
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
    print(f"### Greenfield Benchmark: Minesweeper Game (/goal Directive, Unconstrained Turns) ###")
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
        "task": "Minesweeper Game (/goal directive, unconstrained turns)",
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
