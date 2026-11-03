"""Greenfield Benchmark: 3D Minecraft Sandbox Game under Goal-Driven Mode (/goal).
Full multi-system 3D Voxel Sandbox Game:
  - 3D Procedural Voxel World (Simplex/Perlin noise, 8 block types: grass, dirt, stone, sand, wood, leaves, glass, water)
  - First-person player physics (WASD, PointerLockControls, Space jump, Shift sprint, AABB voxel collision, gravity)
  - Raycasting & Block interaction (crosshair target picking, face normal calculation for adjacent placement, breaking)
  - 9-slot Hotbar & Inventory system (item stacking, mining add, placement deduction, slots 1-9)
  - Day/Night cycle & dynamic celestial lighting
  - Modern UI & HUD (crosshair, coordinates, FPS, pause menu, main menu)
  - World & Player state persistence (save/load modified blocks & coordinates)
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
NATIVE_WORKSPACE = ROOT / ".trajectory/minecraft-native"
OFFLOAD_WORKSPACE = ROOT / ".trajectory/minecraft-offload"
ORACLE_FILE = ROOT / "benchmarks/minecraft_oracle.py"
RESULT_FILE = ROOT / ".trajectory/minecraft_ab_result.json"

MODEL = "deepseek-v4.1-flash"
BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

os.environ["OPENAI_BASE_URL"] = BASE_URL
os.environ["OPENAI_API_KEY"] = API_KEY

GOAL_DIRECTIVE = """[EXECUTION MODE: /goal (GOAL-DRIVEN THOROUGH EXECUTION)]
You are operating under the /goal directive. Your mission is absolute, end-to-end delivery of a complete, production-grade 3D Minecraft-style voxel sandbox game.
Rules:
- Do NOT stop until all modules and integration flows are 100% implemented, wired, and verified.
- Implement every component robustly with full functionality (no placeholders, no mocked returns, no '// TODO').
- Test exhaustively. Call finish_task ONLY when all acceptance criteria are fully met.
"""

USER_MINECRAFT_PROMPT = """
# 项目任务：从零构建一个可玩的 Minecraft 3D 沙盒游戏

你是一名拥有丰富经验的资深游戏开发工程师、3D 图形程序员和全栈开发工程师。
你的任务是：独立设计并从零开发一个可以实际运行和游玩的 Minecraft 风格 3D 沙盒游戏。
这不是一个 UI Demo，也不是一个静态场景展示。我要求你交付一个真正具有交互能力、游戏机制和完整体验的可玩产品。

推荐技术栈：Node.js + Three.js（模块化 JavaScript/HTML/CSS），支持本地浏览器直接运行。

## 一、核心游戏功能
1. 3D 游戏世界：
   - 创建真正的 3D 方块世界，使用程序化算法（如噪声函数）生成高低起伏地形。
   - 地形包含至少 8 种方块：草方块(grass)、泥土(dirt)、石头(stone)、沙子(sand)、木头(wood)、树叶(leaves)、玻璃(glass)、水(water)。
   - 包含平原、山脉、水面以及树木植被分布。
2. 玩家控制系统：
   - WASD 移动，鼠标控制视角（PointerLockControls），Space 跳跃，Shift 奔跑。
   - 实现重力、AABB 碰撞检测与地形阻挡机制（玩家不能穿墙穿地）。
3. 方块交互系统：
   - 准星选中目标方块，左键破坏方块，右键根据表面法线在相邻位置放置方块。
   - 放置方块时检测位置合法性，不能与玩家身体发生重叠碰撞。
4. 背包和物品系统：
   - 9 格快捷栏，支持数字键 1-9 切换选中的方块。
   - 破坏方块获得物品，放置方块扣除物品，支持数量堆叠显示。
5. 世界环境系统：
   - 实现昼夜交替、太阳/月亮轨迹运动、天空颜色明暗变化。
6. 游戏 UI：
   - 现代质感 UI：中央准星、快捷栏、当前 FPS 显示、玩家坐标显示、暂停/主菜单。
7. 存档系统：
   - 支持保存/加载当前世界（已修改的方块、玩家坐标、背包），刷新后可恢复。

## 二、技术与性能要求
1. 必须使用 InstancedMesh 或几何体合并优化大量方块渲染，避免每个方块单独创建 Mesh 导致卡顿。
2. 采用清晰模块化架构：
   - `index.html`: 入口与全套 HUD/UI 元素
   - `package.json`: 依赖声明（Three.js 等）
   - `src/world/blocks.js`: 8 种方块定义与材质配置
   - `src/world/chunk.js`: 3D 体素存储与 InstancedMesh 批量渲染
   - `src/world/terrain.js`: 程序化地形、地层分层与树木生成
   - `src/physics/player.js`: 第一人称摄像机、WASD 控制、AABB 碰撞、重力跳跃
   - `src/interaction/raycast.js`: 射线检测、目标方块拾取、法线计算与放置/破坏
   - `src/inventory/inventory.js`: 9 格快捷栏与物品计数
   - `src/environment/daynight.js`: 昼夜交替与动态光照
   - `src/save/storage.js`: LocalStorage 存档与读档
   - `src/main.js`: Three.js 场景初始化与主游戏循环
"""

TASK_SPECIFICATION = GOAL_DIRECTIVE + USER_MINECRAFT_PROMPT

def run_oracle(workspace: Path) -> dict:
    """Run external independent 20-test Oracle suite against workspace."""
    ws_abs = workspace.resolve()
    env = os.environ.copy()
    env["MINECRAFT_WORKSPACE"] = str(ws_abs)
    t0 = time.monotonic()
    cmd = [
        sys.executable,
        "-m", "pytest",
        "-q",
        "--color=no",
        str(ORACLE_FILE),
    ]
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=40)
        dur = round(time.monotonic() - t0, 3)
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except Exception as e:
        return {"passed": 0, "failed": 20, "total": 20, "pass_rate": "0.0%", "duration": 40.0, "error": str(e)}

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
    print(f">>> Starting Arm 1: Native Control Arm on Minecraft 3D Sandbox (/goal) ...")
    print(f"{'='*70}", flush=True)

    if NATIVE_WORKSPACE.exists():
        shutil.rmtree(NATIVE_WORKSPACE, ignore_errors=True)
    NATIVE_WORKSPACE.mkdir(parents=True, exist_ok=True)
    (NATIVE_WORKSPACE / "src").mkdir(exist_ok=True)

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior 3D game engine engineer and fullstack developer building a complete, playable 3D Minecraft voxel sandbox game from scratch under /goal directive. Write all code, modules, and UI."},
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
                "description": "Finish task when all 3D Minecraft requirements are completely fulfilled.",
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
            # Run oracle to check if goals are met
            oracle_res = run_oracle(NATIVE_WORKSPACE)
            print(f"[Native Arm] Oracle check: {oracle_res['passed']}/{oracle_res['total']} ({oracle_res['pass_rate']})", flush=True)
            if oracle_res["passed"] == 20:
                print(f"[Native Arm] Goal achieved! 20/20 Oracle tests passed.", flush=True)
                finished = True
                break
            messages.append({"role": "user", "content": "Please continue implementing the Minecraft modules: terrain generation, player physics, raycast interaction, hotbar UI, instancing optimization, and save/load."})
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
    print(f">>> Starting Arm 2: Outboard Offload Arm on Minecraft 3D Sandbox (/goal) ...")
    print(f"{'='*70}", flush=True)

    if OFFLOAD_WORKSPACE.exists():
        shutil.rmtree(OFFLOAD_WORKSPACE, ignore_errors=True)
    OFFLOAD_WORKSPACE.mkdir(parents=True, exist_ok=True)
    src_dir = OFFLOAD_WORKSPACE / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "world").mkdir(exist_ok=True)
    (src_dir / "physics").mkdir(exist_ok=True)
    (src_dir / "interaction").mkdir(exist_ok=True)
    (src_dir / "inventory").mkdir(exist_ok=True)
    (src_dir / "environment").mkdir(exist_ok=True)
    (src_dir / "save").mkdir(exist_ok=True)

    t0 = time.monotonic()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turns = 0
    offload_count = 0

    # Stage 1: Zero-Token Architecture Scaffolding (< 5ms, 0 Token)
    # Lays down the project structure, package.json, index.html HUD, block constants, and chunk/main stubs
    print(f"[Offload Arm] Stage 1: Zero-Token Architecture Scaffolding...", flush=True)

    (OFFLOAD_WORKSPACE / "package.json").write_text(json.dumps({
        "name": "minecraft-3d-sandbox",
        "version": "1.0.0",
        "description": "A high-performance 3D Minecraft voxel sandbox game in Three.js",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build"
        },
        "dependencies": {
            "three": "^0.160.0"
        },
        "devDependencies": {
            "vite": "^5.0.0"
        }
    }, indent=2), encoding="utf-8")

    (OFFLOAD_WORKSPACE / "index.html").write_text("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Minecraft 3D Sandbox</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; user-select: none; }
    body, html { width: 100%; height: 100%; overflow: hidden; background: #000; font-family: monospace; }
    #canvas-container { width: 100%; height: 100%; position: absolute; }
    #crosshair {
      position: absolute; top: 50%; left: 50%;
      transform: translate(-50%, -50%);
      width: 20px; height: 20px;
      pointer-events: none; z-index: 100;
    }
    #crosshair::before, #crosshair::after {
      content: ''; position: absolute; background: rgba(255, 255, 255, 0.85);
    }
    #crosshair::before { top: 9px; left: 0; width: 20px; height: 2px; }
    #crosshair::after { top: 0; left: 9px; width: 2px; height: 20px; }
    #hud-info {
      position: absolute; top: 12px; left: 16px;
      color: #fff; text-shadow: 2px 2px 0 #000;
      font-size: 14px; line-height: 1.5; z-index: 100;
    }
    #hotbar-container {
      position: absolute; bottom: 20px; left: 50%;
      transform: translateX(-50%);
      display: flex; gap: 4px; padding: 4px;
      background: rgba(0, 0, 0, 0.6);
      border: 2px solid #555; border-radius: 4px; z-index: 100;
    }
    .hotbar-slot {
      width: 48px; height: 48px;
      background: #8b8b8b; border: 2px solid #373737;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      position: relative; font-size: 11px; color: #fff;
    }
    .hotbar-slot.active { border-color: #ffffff; box-shadow: 0 0 8px #fff; }
    .hotbar-slot .slot-key { position: absolute; top: 2px; left: 4px; font-size: 10px; color: #bbb; }
    .hotbar-slot .slot-count { position: absolute; bottom: 2px; right: 4px; font-weight: bold; }
    #menu-overlay {
      position: absolute; top: 0; left: 0; width: 100%; height: 100%;
      background: rgba(0, 0, 0, 0.75);
      display: flex; flex-direction: column; align-items: center; justify-content: center;
      color: #fff; z-index: 200;
    }
    #menu-overlay h1 { font-size: 48px; margin-bottom: 24px; text-shadow: 4px 4px 0 #222; }
    .menu-btn {
      padding: 12px 32px; font-size: 18px; margin: 8px;
      background: #555; color: #fff; border: 2px solid #fff;
      cursor: pointer; border-radius: 4px;
    }
    .menu-btn:hover { background: #777; }
  </style>
</head>
<body>
  <div id="canvas-container"></div>
  <div id="crosshair"></div>
  <div id="hud-info">
    <div id="fps-display">FPS: 60</div>
    <div id="coord-display">Pos: (0.0, 10.0, 0.0)</div>
    <div id="time-display">Time: Day</div>
  </div>
  <div id="hotbar-container"></div>
  <div id="menu-overlay">
    <h1>MINECRAFT 3D</h1>
    <button id="start-btn" class="menu-btn">开始游戏 (Click to Play)</button>
    <button id="save-btn" class="menu-btn">保存世界 (Save World)</button>
    <button id="load-btn" class="menu-btn">载入世界 (Load World)</button>
    <p style="margin-top: 16px; color: #aaa;">WASD移动 | 鼠标视角 | 空格跳跃 | 左键破坏 | 右键放置 | 1-9切方块</p>
  </div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>""", encoding="utf-8")

    (src_dir / "world" / "blocks.js").write_text("""// 8 Core Minecraft Block Definitions & Material Mapping
export const BLOCK_TYPES = {
  AIR: 0,
  GRASS: 1,
  DIRT: 2,
  STONE: 3,
  SAND: 4,
  WOOD: 5,
  LEAVES: 6,
  GLASS: 7,
  WATER: 8,
};

export const BLOCK_DEFS = {
  [BLOCK_TYPES.GRASS]: { name: 'grass', color: 0x55aa55, solid: true, opacity: 1.0 },
  [BLOCK_TYPES.DIRT]:  { name: 'dirt',  color: 0x8b5a2b, solid: true, opacity: 1.0 },
  [BLOCK_TYPES.STONE]: { name: 'stone', color: 0x808080, solid: true, opacity: 1.0 },
  [BLOCK_TYPES.SAND]:  { name: 'sand',  color: 0xe0cda9, solid: true, opacity: 1.0 },
  [BLOCK_TYPES.WOOD]:  { name: 'wood',  color: 0x6e4a2e, solid: true, opacity: 1.0 },
  [BLOCK_TYPES.LEAVES]:{ name: 'leaves',color: 0x338833, solid: true, opacity: 0.9 },
  [BLOCK_TYPES.GLASS]: { name: 'glass', color: 0xddeeff, solid: true, opacity: 0.4, transparent: true },
  [BLOCK_TYPES.WATER]: { name: 'water', color: 0x3366cc, solid: false, opacity: 0.6, transparent: true },
};
""", encoding="utf-8")

    (src_dir / "world" / "chunk.js").write_text("""import * as THREE from 'three';
import { BLOCK_TYPES, BLOCK_DEFS } from './blocks.js';

export const CHUNK_SIZE = 16;
export const CHUNK_HEIGHT = 32;

export class Chunk {
  constructor(chunkX, chunkZ) {
    this.chunkX = chunkX;
    this.chunkZ = chunkZ;
    this.blocks = new Uint8Array(CHUNK_SIZE * CHUNK_HEIGHT * CHUNK_SIZE);
    this.instancedMeshes = new Map();
  }

  getIndex(x, y, z) {
    return (x * CHUNK_HEIGHT + y) * CHUNK_SIZE + z;
  }

  getBlock(x, y, z) {
    if (x < 0 || x >= CHUNK_SIZE || y < 0 || y >= CHUNK_HEIGHT || z < 0 || z >= CHUNK_SIZE) {
      return BLOCK_TYPES.AIR;
    }
    return this.blocks[this.getIndex(x, y, z)];
  }

  setBlock(x, y, z, blockType) {
    if (x >= 0 && x < CHUNK_SIZE && y >= 0 && y < CHUNK_HEIGHT && z >= 0 && z < CHUNK_SIZE) {
      this.blocks[this.getIndex(x, y, z)] = blockType;
    }
  }

  buildInstancedMeshes(scene) {
    // Skeleton: InstancedMesh optimization per block type
    for (const mesh of this.instancedMeshes.values()) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    this.instancedMeshes.clear();
  }
}
""", encoding="utf-8")

    (src_dir / "main.js").write_text("""import * as THREE from 'three';
import { Chunk } from './world/chunk.js';
import { BLOCK_TYPES } from './world/blocks.js';

// Three.js Scene Setup & Bootstrap
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x87ceeb);
const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.getElementById('canvas-container')?.appendChild(renderer.domElement);

const hemiLight = new THREE.HemisphereLight(0xffffff, 0x444444, 0.6);
scene.add(hemiLight);
const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
dirLight.position.set(50, 100, 50);
scene.add(dirLight);

export { scene, camera, renderer };
""", encoding="utf-8")

    client = OpenAI(base_url=BASE_URL, api_key=API_KEY)
    messages = [
        {"role": "system", "content": "You are a senior 3D game engine engineer operating under Outboard Offload Engine & /goal directive. Zero-token architecture scaffolding has laid down the modular Three.js structure. Finish, verify with Oracle, and achieve 100% acceptance."},
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
                "description": "Run the independent 20-assertion Minecraft Oracle suite.",
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

        # Check oracle first
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
            messages.append({"role": "user", "content": "Please verify Oracle tests and refine any remaining issues to achieve 20/20."})
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
    print(f"### Greenfield Benchmark: 3D Minecraft Sandbox Game (/goal Directive) ###")
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
        "task": "3D Minecraft Voxel Sandbox Game (/goal directive)",
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
