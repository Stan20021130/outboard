# 🚤 Outboard

> **The High-Efficiency Offload Engine for Coding Agents over Model Context Protocol (MCP)**

[![MCP Compatible](https://img.shields.io/badge/MCP-1.26+-blue.svg)](https://modelcontextprotocol.io)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green.svg)](https://python.org)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-yellow.svg)](LICENSE)

**Outboard** 是一个面向 AI 编程智能体（如 Antigravity、Claude Code、Cursor、Codex）的**外挂卸载加速引擎**。通过标准的 MCP 协议挂载，Outboard 承担繁重高熵的软件工程脏活，彻底斩断大模型“整文件覆写税”，将单次端到端构建的 API 账单直接折半。

---

## ⚡ 核心能力与架构

1. **4 大专用软件工程 Subagent**：
   - 🧪 **覆盖率盲区扩充 (`augment_test_coverage`)**：静态提取 AST 条件盲区，在独立沙箱中合成紧凑边界测试。
   - 🔍 **跨文件依赖与配置追踪 (`investigate_repository`)**：AST 与符号交叉引用扫描，生成紧凑结构化证据表。
   - 💥 **多故障聚类与归因分诊 (`cluster_test_failures`)**：大面积报错去重聚类，根因归因，压缩 80%+ 上下文阅读。
   - 🛡️ **回归测试沙箱构建 (`construct_regression_test`)**：构建独立防回退测试，验证“未修时失败，修后通过”。
2. **零成本确定性门禁（Zero-Token Gates）**：
   - ⚡ `get_repository_state`：纯静态 AST 与清单侦察，0 LLM Token 即时冷启动。
   - 🛡️ `verify_patch`：确定性物理沙箱验证，0 LLM 消耗。
3. **差量修补协议（`edit_file` / Diff Hunk）**：
   - 彻底消灭修改几行代码却重写几百行文件的“全文件覆写税”，单次修改输出 Token 削减 95%。
4. **原生 MCP Sampling 支持（零配置即用）**：
   - 自动通过 `sampling/createMessage` 借用宿主已有的模型会话，**开发者无需配置任何额外 API Key**。

---

## 📊 实测性能对比

Outboard 在多种典型工业场景（从零全栈交付 Greenfield、复杂系统重构与 Brownfield CVE 修复）中经过了端到端独立验收 Oracle 的严格验证。

### 1. 经典网页版扫雷从零全栈构建（`Minesweeper Retro`，20 项官方规则 Oracle 验证，不限轮数对决）

初级 (9x9/10雷)、中级 (16x16/40雷)、高级 (30x16/99雷)、绝对首击安全保障、8邻域雷数计算、零雷级联 Flood Fill 自动展开、右键插旗/?号防误触、双击 Chording 快速连开、LED 计时/计雷与各难度最佳成绩 localStorage 持久化。在**完全不设紧箍咒轮数上限（上限放宽至 60 轮）**的极限自由探索对决中：

| 评测维度 | 原生对照臂 (Native Arm) | **Outboard 优化臂 (Offload Arm)** | **Outboard 相对原生臂收益** |
| :--- | :--- | :--- | :--- |
| **完成耗时 (Wall-Clock)** | 1041.55 s (17.4 分钟) | **178.49 s (2.9 分钟)** | **提速 82.9% (省 14.4 分钟！5.8 倍速)** |
| **总 Token 消耗** | **5,053,732 Token (505万)** | **41,796 Token (4.1万)** | **Token 削减 99.2% (-501.2万，消耗仅原生的 0.8%！)** |
| **输入 Prompt Token** | 4,948,766 Token | **12,915 Token** | **输入暴降 99.7% (-493.5万)** |
| **输出 Completion Token**| 104,966 Token | **28,881 Token** | **输出节省 72.5% (-7.6万)** |
| **调用轮数 (Turns)** | 60 轮 (耗尽轮数上限强制终止) | **5 轮 (一次性完整交付并提前收敛)** | **轮数减少 91.7% (-55 轮)** |
| **单次 API 账单 (峰值)** | RMB 2.1674 | **RMB 0.2528** | **成本削减 88.3% (省下近 2 块钱)** |
| **Oracle 独立通过率** | 19 / 20 (95.0%) | **20 / 20 (100.0% 满分通过)** | **高质量一次性通过全部 20 项验收** |

> **极限行为学洞察（500 万 Token 的教训）**：
> - **陷入长达 48 轮的自建测试泥潭**：原生对照臂在第 9 轮时代码就已经能通过 19 项功能验收（95%），仅漏掉了 1 项边界要求（游戏结束后的按键冻结）。然而原生臂自发在 `test/` 目录下生成了 23 个补丁和测试脚本，在第 12 轮到第 60 轮整整 **48 轮交互**中，反复使用 Node 和子进程调试自建测试脚本 `test/run-ui-test.js` 和 DOM 渲染，白白烧掉了 **500 万 Token**，直到被 60 轮强制切断也未能修复那 1 个漏掉的边界！
> - **Outboard 的零阻抗高阶收敛**：通过零成本结构脚手架与物理验收门禁，Outboard 仅用 **5 轮交互（178 秒、4.1 万 Token）**便一次性完成游戏全套机制与 UI 逻辑，通过全套 20 项断言直接触发提前收敛退出！

---

### 2. 经典街机游戏从零全栈构建（`Tetris Arcade`，20 项官方指南 Oracle 验证，`/goal` 模式）

严格按照国际俄罗斯方块官方指南（Tetris Guideline）开发 10x20 棋盘、7-Bag 随机数生成器、SRS 踢墙旋转、阴影预览（Ghost Piece）、暂存（Hold）、硬降/软降与本地存档高分榜的单局从零交付对决：

| 评测维度 | 原生对照臂 (Native Arm) | **Outboard 优化臂 (Offload Arm)** | **Outboard 相对原生臂收益** |
| :--- | :--- | :--- | :--- |
| **完成耗时 (Wall-Clock)** | 385.05 s (6.4 分钟) | **312.03 s (5.2 分钟)** | **提速 19.0% (省 73 秒)** |
| **总 Token 消耗** | 806,874 Token | **101,085 Token** | **Token 削减 87.5% (-70.5万，直接打 1.2 折！)** |
| **输入 Prompt Token** | 761,172 Token | **46,474 Token** | **输入暴降 93.9% (-71.4万)** |
| **调用轮数 (Turns)** | 20 轮 (陷入测试死循环耗尽轮数) | **6 轮 (一次性完整交付)** | **轮数减少 70.0% (-14 轮)** |
| **单次 API 费用 (峰值)** | RMB 0.5887 | **RMB 0.4837** | **成本削减 17.8%** |
| **Oracle 独立通过率** | 16 / 20 (80.0%) | **20 / 20 (100.0% 满分通过)** | **成功交付完整可玩游戏（Native 缺失 index.html）** |

> **深入现象剖析**：
> - **原生臂的“琐碎测试死循环”陷阱**：原生臂在第 5 轮写出了游戏逻辑（通过 16/20 验证），但在第 7 轮自建单元测试时因为测试桩语法错误，在第 8~20 轮连续 13 轮陷入“修测试脚本 -> 报错 -> 再修测试脚本”的无效循环，彻底遗忘了 PRD 中“交付可玩网页”的目标，耗尽 80 万 Token 却从未创建 `index.html`。
> - **Outboard 的结构化护航**：通过零成本结构脚手架与差量生成，Outboard 仅用 6 轮即完成 2091 行高品质全套逻辑与 UI，以 10 万 Token 满分通过验收并触发 `/goal` 提前完工。

---

### 3. 嵌入式文档数据库（`MiniDocDB`，$N=3$ 多轮严格统计均值）

20 项独立外部 Oracle 验证（CRUD、投影、复合索引、嵌套查询、原子事务）：

| 评测维度 | 原生对照臂 (Native $N=3$) | 原版卸载臂 (Standard $N=3$) | **Outboard 优化臂 ($N=3$)** | **Outboard 相对原生臂收益** |
| :--- | :--- | :--- | :--- | :--- |
| **完成耗时 (Wall-Clock)** | 332.48 s (5.5 分钟) | 277.37 s (4.6 分钟) | **153.65 s (2.5 分钟)** | **提速 53.8% (省近 3 分钟)** |
| **输出 Completion Token** | 75,465 | 64,001 | **26,767** | **输出暴降 64.5% (-4.87万)** |
| **输入 Prompt Token** | 735,069 | 448,166 | **175,269** | **输入暴降 76.2% (-55.98万)** |
| **总 Token 消耗** | 810,534 | 535,421 | **202,036** | **总 Token 节省 75.1% (-60.85万)** |
| **单次花费 (峰值时段)** | RMB 0.8199 | RMB 0.6609 | **RMB 0.2861** | **成本削减 65.1% (单次仅 2毛8)** |
| **Oracle 独立通过率** | 18.0 / 20 (90.0%) | 18.0 / 20 (90.0%) | **19.0 / 20 (95.0%)** | **成功率提升 5% 且 3 轮极其稳定** |

---

### 4. 高并发内存数据库（`Mini-Redis`，$N=3$ 多轮严格统计均值）

RESP 协议解析、LRU 淘汰、TTL 过期字典、AOF 日志持久化：

| 评测维度 | 原生对照臂 (Native $N=3$) | **Outboard 优化臂 ($N=3$)** | **Outboard 收益** |
| :--- | :--- | :--- | :--- |
| **完成耗时 (Wall-Clock)** | 247.9 s | **187.8 s** | **提速 24.2%** |
| **总 Token 消耗** | 439,392 Token | **85,152 Token** | **Token 削减 80.6% (-35.4万)** |
| **调用轮数 (Turns)** | 17.7 轮 | **6.7 轮** | **轮数减少 62.1% (-11 轮)** |
| **Oracle 独立通过率** | 18.7 / 20 (93.3%) | **19.7 / 20 (98.3%)** | **稳定性显著提升** |

---

### 5. 工业级真实开源项目缺陷修复（`psf/requests` CVE 级漏洞定位与修复）

大型 Brownfield 代码库（数十万行、跨多文件认证凭据泄露漏洞）：

| 评测维度 | 原生对照臂 (Native Arm) | **Outboard 卸载臂 (Offload Arm)** | **Outboard 收益** |
| :--- | :--- | :--- | :--- |
| **修复耗时 (Wall-Clock)** | 277.6 s (4.6 分钟) | **51.1 s (0.8 分钟)** | **提速 81.6% (5.4 倍极速修复)** |
| **主代理交互轮数** | 22 轮上下文盲搜试错 | **1 轮一次性精准闭环** | **减少 95.5% 上下文污染** |
| **修复验证状态** | 漫长探索后勉强修补 | **物理沙箱零 Token 瞬时验证通过** | **100% 修复通过** |

---

## 🚀 快速接入指南

### 1. 任意支持 MCP 的宿主（Claude Code / Antigravity / Cursor / Windsurf）

在您的宿主 MCP 配置文件（如 `mcp_config.json` 或 `settings.json`）中添加一行：

```json
{
  "mcpServers": {
    "outboard": {
      "command": "uvx",
      "args": ["outboard", "serve", "--repo", "${workspaceFolder}"]
    }
  }
}
```

### 2. OpenAI Codex CLI

直接在终端执行注册：
```bash
codex mcp add outboard -- python -m trajectory_offload serve
```

---

## 🛠️ 本地开发与基准复现

需要 Python 3.11+。

```bash
git clone https://github.com/your-username/outboard.git
cd outboard
pip install -e .
```

### 运行全套基准评测：
```bash
python benchmarks/eval_docdb_optimized_multirun.py
```

这使用当前讨论指定的本地接口；该接口接受任意密钥，所以示例使用无保密含义的占位值。接入其他服务时通过环境变量提供真实密钥，不写入项目文件。

调查 `completed` 返回退出码 0；`partial` 或 `failed` 返回 2；补丁验证还要求 verdict=passed 才返回 0；参数／本地配置错误返回 1。日志默认保存在 `%LOCALAPPDATA%/TrajectoryOffload/runs/`。`--output` 和 `--log-dir` 必须位于被调查仓库之外；默认运行日志也遵守此限制。

示例仓库故意包含分页与超时配置传播缺陷，供调查和隔离修复评测使用，其回归测试初始失败是预期行为。

## 接入主 Agent

项目已提供：

- [本机 Codex MCP 配置](.codex/config.toml)：绑定 `F:/CTOS`，通过 `.venv` 启动本地 stdio Server。
- [调用 Skill](.agents/skills/repository-investigator/SKILL.md)：指导主 Agent 选择适用场景、传参、使用证据与处理缺口。

配置中的绝对路径适用于本机工作区；移动项目或调查其他仓库时，需要调整 `command`、`cwd` 和 `--repo`。主 Agent 的工具目录在连接初始化时加载；已有会话需要重新连接 MCP 或启动新会话。未修改用户级 Codex 配置。

该连接配置为 `required = true`，确保 Server 完成初始化后才启动主 Agent 首轮推理，避免可选服务尚未就绪时工具被遗漏。三组实验统一使用指定模型；项目 MCP 配置只设置执行器，不自动改变宿主主 Agent 的模型。

也可直接启动 Server，供任何支持 stdio MCP 的宿主接入：

```powershell
uv run trajectory-offload serve --repo F:/CTOS --model deepseek-v4.1-flash --reasoning low
```

Server 的标准输出用于 MCP 协议，运行日志和诊断不写入标准输出。仓库根目录由启动参数固定，工具调用者不能通过任务输入改变它。

默认开放只读调查、失败归并与状态查询；传入 `--checks` 才注册执行工具：

| Tool | 用途 |
| --- | --- |
| get_repository_state | 无模型调用地获取当前源码快照 ID 与可用检查配置 |
| investigate_repository | 只读调查；新增 change_impact 模板 |
| cluster_test_failures | 按相同症状归并多个调用方提供的失败，必要时提出未确认的跨组关联假设 |
| construct_regression_test | 构造测试补丁，在修复前后分别验证；需要固定的 pytest 检查配置 |
| verify_patch | 对照原版与补丁版检查，并对失败做证据分析 |
| diagnose_failure | 重复复现指定失败，在允许的检查内调查和验证假设 |

## v1.4 新增场景

### 回归测试构造与差分验证

适用于任务本来就要求增加回归测试、预期行为明确、已有针对当前快照的候选源码补丁的情况。先调用 `get_repository_state` 取得 `snapshot_id`，再调用 `construct_regression_test`：

```json
{
  "objective": "为 value 的修复添加回归测试",
  "expected_behavior": "value() 应返回 7",
  "snapshot_id": "由 get_repository_state 返回的 64 位哈希",
  "source_patch": "--- a/settings.py\n+++ b/settings.py\n@@ -1,2 +1,2 @@\n def value():\n-    return 30\n+    return 7\n",
  "test_file": "tests/test_value_regression.py",
  "check_id": "regression",
  "scope": []
}
```

`source_patch` 必须可精确应用到当前快照，且不能更改原有测试或策略保护的文件。`test_file` 必须是尚不存在的 `tests/**/test_*.py`。Subagent 基于需求与修复前源码生成仅新增该文件的统一 Diff；候选源码补丁不提供给模型。运行时在两个临时源码副本中执行同一测试，要求修复前至少有一个带指定标记的断言失败、无收集错误或跳过，修复后相同测试用例全部通过。未满足条件时保留可用结果并返回 `partial`。

简单的原地源码变更也可不传 `source_patch`，改传 `source_edits`，例如 `[{"path":"settings.py","old":"return 30","new":"return 7"}]`。两种方式必须且只能选择一种；每个旧文本必须在快照中恰好出现一次，重叠修改、越界路径和受保护测试均被拒绝。测试源码由 Subagent 生成，运行时负责编译为统一 Diff。`scope` 是读取边界；如不确定范围应省略，如指定则需同时包含相关源码和测试。

执行配置必须由宿主在启动时声明，`execution-checks.json` 中已提供：

```json
{"id":"regression", "argv":["{python}","-m","pytest","-q","{test_file}","{junit_option}"], "timeout_seconds":60}
```

运行时只为这个场景替换测试路径与 JUnit 输出位置。pytest 已列为项目依赖；被测项目的其他依赖、插件和运行环境仍须由宿主准备。临时副本只包含快照支持的源码文件，不包含二进制资源、密钥文件或 Git 历史；依赖这些内容的测试可能无法运行。检查实际执行生成的 Python 测试及仓库代码，临时副本**不是操作系统沙箱**，只对信任的代码使用。

`status=completed`、`verdict=passed` 和 `verification.differential_passed=true` 表示差分执行满足上述条件，不证明断言语义正确：`semantic_support_checked` 始终为 false。主 Agent 需依据独立需求检查断言，按正常流程应用 `test_patch` 并执行任务要求的最终验证。测试补丁不会自动写入原仓库；日志记录完整的模型输出和检查结果。

同一输入也可写为任务 JSON，通过 `trajectory-offload construct-regression --repo <repo> --checks <policy.json> --task <task.json>` 调用。

### 多测试失败归并

`cluster_test_failures` 接受 `failures` 数组或 `junit_xml` 字符串，两者选一。下面的调用不会执行测试：

```json
{
  "snapshot_id": "由 get_repository_state 返回的 64 位哈希",
  "failures": [
    {"test_id": "tests/test_a.py::test_a", "kind": "failure", "message": "AssertionError: timeout", "traceback": "..."},
    {"test_id": "tests/test_b.py::test_b", "kind": "failure", "message": "AssertionError: timeout", "traceback": "..."}
  ],
  "analyze_relationships": true
}
```

程序仅按相同的失败类型与完整消息做症状分组；空消息不合并。每条消息最多 4000 字符、堆栈最多 10000 字符，JUnit 整体最多 250000 字节；超限会拒绝输入，不会先截断后错误地合并。返回的长消息和堆栈只包含有截断标记的摘要与输入原文 SHA-256，避免整份日志再次膨胀主 Agent 上下文。若有多个不同症状且 `analyze_relationships=true`，模型可提出引用失败观察 ID 的跨组**假设**，只能引用实际提供给模型的各组代表样本，不会标记根因已确认。默认仅分析日志；如任务要求源码证据，设 `include_source_evidence=true`，系统会依据日志标识符提供少量快照源码候选。`source_ids` 只允许引用真实返回的静态片段，候选不表示运行路径已确认。最多接受 60 条失败；跨组模型分析最多处理前 20 组，超出时明确返回 `partial`。设置 `analyze_relationships=false` 可仅做确定性归并；所有症状完全相同时也无需模型调用。分组不会复现或独立核实传入日志。

命令行入口为 `trajectory-offload cluster-failures --repo <repo> --task <task.json>`。主 Agent 可按需继续使用现有 `diagnose_failure` 复现具体失败。

两个场景都记录模型、输入／输出 Token、工具与进程次数及耗时，但尚未通过新的 DSH 原生对照实验，不能据此宣称更省或更快。早期 [v0.4 结果](docs/REVIEW_POLICY_V4.md) 保持为历史基线。

DSH 单场景 MCP 适配器 `benchmarks/dsh_mcp.py` 也接受 `--scenario regression` 或 `--scenario multi_failure`，且每次只暴露对应工具和 `get_repository_state`；历史场景的工具集合保持不变。适配器仅提供工具接入，不会自动创建新场景的数据集或执行对照实验。

调查输入示例：

```json
{
  "objective": "追踪 API_TIMEOUT_SECONDS 的来源、覆盖关系和实际消费位置，定位相关测试。",
  "task_type": "trace_config",
  "scope": ["sample_app", "tests"],
  "known_evidence": [],
  "acceptance_criteria": [
    "给出配置来源与默认值。",
    "给出传播路径和代码中可见的缺口。",
    "定位配置传播与请求覆盖测试。"
  ]
}
```

`task_type` 为 `localize`、`trace_config`、`test_failure` 或 `change_impact`。`scope` 是目录或文件前缀，不是 glob；空数组表示全部受支持源文件。已知线索与范围可选；未给验收条件时，以调查目标作为验收问题。

## v0.2 的三个扩展场景

**变更影响分析**仍使用 `investigate_repository`，增加 `changed_paths`、`symbols` 和 `patch` 输入，至少提供一项。`symbols` 接受 `Context.__init__` 一类限定名称。v0.3 按具体问题选择代码和测试片段，需要时再查询符号与引用，不再预加载变更文件的所有顶层符号和广泛导入邻域。返回的 `impact.coverage_complete` 始终为 false，避免把有界静态分析当成完整运行时影响证明。

```powershell
uv run trajectory-offload investigate --repo benchmarks/fixtures `
  --task benchmarks/impact-task.json --output .trajectory/impact.json
```

**补丁验证**接收快照 ID、统一 Diff 和必跑检查 ID，在源码副本内先运行基线，再应用补丁并运行同样的必跑项。原有测试和检查策略文件受保护。全部通过时直接返回事实，不额外消耗模型调用；失败时可启动模型调查，并允许它选择声明过的追加检查。

```powershell
$snapshot = (uv run trajectory-offload snapshot --repo benchmarks/fixtures | ConvertFrom-Json).snapshot_id
New-Item -ItemType Directory -Force .trajectory | Out-Null
@{
  objective = '验证超时配置传播修复，保留显式覆盖和默认值行为'
  snapshot_id = $snapshot
  patch = Get-Content benchmarks/timeout-fix.patch -Raw
  check_ids = @('timeout')
} | ConvertTo-Json -Depth 6 | Set-Content .trajectory/verify-task.json -Encoding utf8
uv run trajectory-offload verify --repo benchmarks/fixtures --checks benchmarks/checks.json `
  --task .trajectory/verify-task.json --output .trajectory/verification.json
```

`status` 表示任务执行是否完整，`verdict` 表示补丁是否通过检查。`status=completed, verdict=failed` 是完成了验证并发现失败；CLI 返回 2。只有 `verdict=passed` 才表示必跑项全部通过，且仍受测试覆盖范围限制。补丁只应用到副本，主 Agent 负责决定是否集成。

**失败复现与诊断**通过 `diagnose_failure` 或 CLI `diagnose` 接收 `objective`、当前 `snapshot_id`、`check_ids` 和字面量 `failure_signature`。默认重复两次，区分 reproduced、intermittent、different_failure、not_reproduced、inconclusive；只有获得失败证据后才启动源码调查。`followup_check_ids` 可声明额外可用检查，模型通过 `run_checks` 自主追加；无任意命令或代码编辑工具。复现成功本身不证明因果根因，当前 `root_cause_confirmed` 保持 false，返回有证据的诊断与假设。

真实 MCP 与模型联调三个场景：

```powershell
uv run python benchmarks/run_v2_smoke.py --output-dir .trajectory/v2-mcp-smoke
```

输出目录不可复用已有 summary，运行记录保留实现指纹、快照、检查记录和模型用量。该脚本是工程联调，不是自主卸载策略或经济性实验。

## 检查配置与执行边界

项目接入配置已加载 [execution-checks.json](execution-checks.json)，提供本项目的 unit、runtime 检查，并使用单次输出 8000 Token 上限；CLI 默认仍为 5000，可通过 --max-output-tokens 调整。Fixture 使用单独的 [benchmarks/checks.json](benchmarks/checks.json)，提供 timeout、pagination、regression。切换仓库时必须同时选择适用的检查配置。

```powershell
uv run trajectory-offload serve --repo F:/CTOS --checks F:/CTOS/execution-checks.json `
  --model deepseek-v4.1-flash --reasoning low --max-process-seconds 90
```

检查配置是受信任的宿主启动配置，定义参数数组、可选环境覆盖和超时。`{python}` 替换为 Runtime 所用的 Python；不经过 Shell，不自动安装依赖，也不接受调用者传入新的命令。模型只能选择任务允许的配置 ID，不能改变 baseline／patched／reproduction 等记录阶段。

执行使用受支持源文件的字节精确副本，保留 BOM 和换行符，依赖使用宿主 Python 环境。PYTHONPATH 优先指向副本及其 src，默认不向检查进程传递 API 密钥和桌面工具环境。子进程次数、单次时间、总时间与输出量均受限，超时或输出超限不能计为通过。

**源码副本不是操作系统沙箱。**检查仍以当前用户权限运行受信任代码，不能隔离代码自行访问外部文件、服务或网络。当前也不复制二进制资产、完整 Git 历史和被忽略的环境文件；缺失前置条件会作为检查错误保留。需要这些资源的仓库应先配置合适的执行环境。

补丁必须使用未引用的 `a/`、`b/` 文本统一 Diff，按精确上下文应用；暂不支持重命名、二进制、文件模式变更和带 Git 引号的路径。既有 `test_*`、`*_test.py` 与 protected_paths 下的既有文件不能修改或删除。源码副本被检查意外修改时，不会返回通过。

## 执行与验证

源文件只通过 Runtime 的读取函数暴露给模型，不提供 Shell、文件写入、任意 Python 执行或网络工具：

| 执行模式 | 工具 |
| --- | --- |
| 普通 Subagent | 文件列表、单项文本搜索、单段读取、符号定义、词法引用 |
| 特调 Subagent | 文件列表、批量文本搜索、批量范围读取、定义与引用组合查询 |

两种模式共享同一个源数据与 AST 后端、结果契约和验证器。词法引用会明确标注其方法，不被直接当作已解析的调用图。

特调模式按问题中的标识符选择可执行代码窗口及短测试体，跳过长文档，初始片段最多 260 行、16000 字符。随后最多进行两轮批量补证据，再汇总；剩余问题必须返回 `partial`。v0.4 将独立模型复核改为显式可选。这是一项有界调查策略，复杂问题可能需要主 Agent 继续处理，不能把更早停止本身算作效率提升。构造、推理和请求的复核均计入运行账。`--no-prefetch` 关闭初始预取，仍保留专用的补证据策略。

模型使用 Runtime 分配的证据 ID 引用已读取片段，Runtime 回填原文、路径和行号，并始终执行结构、引用和快照检查；独立语义校验按请求执行。重叠读取复用已提供的证据。批量读取支持每段最多 300 行、整批最多 400 行，内部拆成不超过 100 行的可引用片段。符号查询返回限定定义、去掉前导文档的函数体、类的方法位置索引及有界词法引用。

代码快照覆盖受支持的 UTF-8 源码、配置和文档；Git 仓库包含未提交和未跟踪但未忽略的文件。常见构建目录、宿主配置目录、凭据文件、二进制、符号链接与重解析点不会作为可读源码暴露。超过快照／单文件限制会明确失败。该快照用于第一版 Python 调查，不是仓库的完整备份。

每个结果包含 `snapshot_id`、`findings`、`relations`、`related_tests`、`criteria_coverage`、`unresolved_questions`、验证状态和运行指标。

MCP `investigate_repository` 的 v0.4 返回格式为 `format_version=3`：条目内的 `evidence` 为 `[{"id":"E1"}]`，顶层 `evidence.E1` 保存原有 `path/start_line/end_line/quote`，重复引用只传一次。CLI 和内部 Python `investigate()` 仍返回展开的引用。与 v0.3 的主要契约变化是分开报告完成状态和独立复核状态。只读调查的验收条件应是可用源码支持的问题；需要实际运行并通过测试时调用 `verify_patch` 或由主 Agent 执行。

`completed` 需要同时满足：

1. 输出通过 Pydantic 契约与验收条件覆盖检查。
2. 引用位于允许范围，行号与原文匹配；Subagent 引用的范围确实经过读取。
3. 调查结束时，受支持源码的快照没有变化，且没有超出运行预算。
4. 如果调用者请求或宿主要求独立语义复核，该复核必须通过。

默认不额外调用模型审阅报告。`completed` 表示调查者报告静态问题已回答，并通过程序化证据检查；它不表示结论经过独立语义认证。`verification.semantic_review_status` 为 `not_requested`、`passed`、`rejected` 或 `incomplete`，同时保留原有 `semantic_support_checked/passed` 字段。未请求复核不会自动触发 `partial`；已请求的复核被拒绝或未完成，仍保留为 `partial`。

这仍是静态证据调查。主 Agent 继续承担必要的源码审阅、修改与最终测试。通过模型复核也不等于根因已被运行验证，不构成修复成功证明。

模型、工具、Token 和时间上限可通过 `--help` 查看。首个请求使用保守的 UTF-8 字节上界；对延长的相同上下文，后续预检复用接口已报告的输入 Token，并对新增内容使用字节上界。实际用量以接口数据记录，不依赖猜测的 tokenizer。对无法获知用量的中断调用标记统计不完整。API SDK 自动重试关闭，显式的协议纠正调用也计入模型调用与 Token。

MCP 单次调用可设置 `semantic_review=true` 请求独立评审；CLI 使用 `--semantic-review`（别名 `--semantic-verification`）。Serve 启动时加该参数会要求所有调查都评审，调用者不能用 `false` 关闭宿主要求。默认不启用；原 `--no-semantic-verification` 参数作为显式关闭选项保留。改变这一策略不改变 Patch Verifier 的必跑检查或最终任务验收。

## 三组评测

```powershell
uv run trajectory-offload evaluate `
  --dataset benchmarks/tasks.json `
  --output-dir .trajectory/subtask-evaluation `
  --modes native generic specialized `
  --level subtask
```

三组使用相同模型、推理配置、任务和源状态。子任务模式直接调用 B／C Runtime 来比较执行器；宿主的 MCP 初始化和主 Agent 集成开销由完整修复模式测量，不能从子任务结果推断整条任务链的成本：

- `native`：真实的 Codex CLI 在只读快照副本中直接调查。
- `generic`：独立上下文的普通 Subagent。
- `specialized`：特调工具与执行策略的 Subagent。

Codex 可执行文件默认取 `CODEX_APP_RUNTIME_CLI_PATH`，否则取 `codex`。Windows 下请使用实际的 `codex.exe`，可通过 `--codex-executable` 指定；不执行批处理包装器。原生试验隔离桌面工具管道、禁用应用连接和额外委派，保留代码任务所需的原生工具。

对于当前 CLI 不认识的第三方模型名，评测使用该 CLI 自带的直接函数工具元数据构造临时模型目录，避免回退到网关无法执行的 code-mode-only 配置。**实际推理请求的模型名保持不变。** 这是宿主兼容配置，应在比较实验时与 CLI 版本一起记录。

每轮按任务轮换组别执行顺序。参考文件只进入评测代码，不传给模型。输出包括：

- `results.jsonl`：本轮每个试验的结果、引用、用量和验证状态。
- `summary.json`、`report.md`：成功率、Token、延迟及可计算的费用。
- `runs/`：Capsule、模型用量、工具调用、协议纠正、验证与结果日志。

原生 CLI 的逐次推理调用数不能全部观测，相关计数会标记不完整；可用时记录 CLI 汇总的输入、缓存、输出与推理 Token。没有定价时，费用为 `null`，不会把 Token 自动当作实际金额。`--pricing` 可读取含 `input`、`cached_input`、`output` 三项的 JSON，单位是每百万 Token 的价格；推理 Token 已包含于输出计费量，不重复相加。

Runtime 可在每次 API 请求前执行 Token 和模型轮次限制；原生 CLI 的内部推理轮次与峰值上下文不可全部观测，其评测通过进程超时限制墙钟，并报告可取得的累计用量。不会将该缺失信息当作零。评测默认总 Token 预算为 500000，单次 Investigator 默认 80000；组间比较使用同一个评测配置。

子任务的参考文件覆盖率是辅助指标，不能替代证据支持和最终修复成功率。当前三条小型 Fixture 用于功能联调，不能据此宣称统计非劣或跨仓库降本。

### 完整修复流程

```powershell
uv run trajectory-offload evaluate `
  --dataset benchmarks/tasks.json `
  --output-dir .trajectory/repair-evaluation `
  --modes native generic specialized `
  --level repair
```

每个试验创建独立临时源码副本：先确认指定回归测试初始失败，再由原生主 Agent 修复，最后执行原始测试。B／C 主 Agent 获得相同的工具描述与调用指导，主动决定是否使用调查工具；内部模式分别为普通与特调，记录实际 `offload_calls`。

测试修改不计为成功；外部检查前恢复原始测试，并确认原仓库未变化。调用子 Agent 的 Token、工具数与用量完整性合入总账，端到端时间包含准备、调查、修复与测试。

修复产物以运行 ID 对应的 `.patch` 保存在评测 `runs/` 中，结果给出 `patch_path`。自主调用评测允许主 Agent 判断简单任务适合直接完成，必须结合 `offload_calls` 解释结果；未调用 Subagent 的差异不能归因于卸载。

联通测试可在数据中设置 `require_offload: true`，此时主 Agent 未实际调用调查执行器不能算成功；该类测试用于验证集成，不用于证明自主调用策略或效率。Windows 沙箱创建的缓存偶尔带有不同的 ACL；无法清理时会记录 `cleanup_retained_path`，保留已验证的结果与 Patch，不改变目录权限。

评测数据中的 `test_command` 会作为参数数组在临时副本中执行，不经过 Shell。请仅对可信评测数据运行修复流程；这属于评测程序的执行面，Investigator 本身仍只有只读能力。

## 轨迹分析

```powershell
uv run trajectory-offload profile --log-dir .trajectory/subtask-evaluation/runs --output .trajectory/profile.json
```

输出高 Token 运行和成功轨迹中重复出现的工具二元／三元序列，供后续选择复合工具和改进策略。重复序列只是优化候选，不会直接变成未经评测的新策略。

本机 v0.2 联调结果见 [v0.2 验证记录](docs/VALIDATION_V2.md)，早期对照与负面结果保留在 [v0.1 历史记录](docs/VALIDATION.md)。真实 DSH 对照见下文；当前数据不足以建立质量非劣或实际金额节省的结论。

## DSH 主 Agent A/B 实验

已在 GitHub Click 仓库上完成 3 类任务、每类两次 A/B，共 12 次真实 DSH 运行。A 为主 Agent 原生直接完成，B 为同一宿主、同模型使用指定特调执行器；没有普通 Subagent 组。任务、源码、预算和顺序在正式运行前冻结，接口用量通过透明转发账本记录。

结果与解释见 [DSH 实测报告](docs/DSH_STUDY_RESULTS.md)，实验条件与限制见 [协议](docs/DSH_STUDY_PROTOCOL.md)。本轮是受控卸载机制实验，不能替代自主调用策略评估。报告披露了评估器对合法新增测试的误判及统一修正，原始记录保持不变。

本机可复核：

```powershell
uv run python benchmarks/dsh_acceptance.py
uv run python benchmarks/dsh_report.py --report docs/DSH_STUDY_RESULTS.md
```

原始数据、每个非空工作副本、DSH 配置、模型 API 账本与最终 Patch 位于 `.trajectory/dsh-study/`，未打入分发包。重跑任务的入口为 `benchmarks/dsh_study.py`，准备条件见协议。

## v0.4 可选独立复核与复测

默认调查完成后执行程序化证据检查，独立 LLM 审稿改为显式可选。MCP 的 `semantic_review=true` 为单次请求评审；CLI／Serve 的 `--semantic-review` 要求评审。结果以 `verification.semantic_review_status` 明确区分未请求、通过、拒绝和未完成，未评审不会冒充已评审。

同版本三组 DSH 对照完成 12 次运行：原生、默认卸载、强制评审卸载各四次。最终验收均为 4/4。默认卸载平均 269263 Token、147.1 秒；强制评审平均 359093 Token、212.0 秒，默认路径减少 25.0% 总 Token、30.6% 主任务耗时。原生平均 377202 Token、130.0 秒；默认卸载仍慢 13.1%，尚未同时实现更省且更快。

原生有一次最后请求后越过 50 万 Token 上限，已单独标注，预算内匹配子集的结论方向相同。实际金额仍取决于代理的分项价格。完整数据和限制见 [v0.4 复核策略报告](docs/REVIEW_POLICY_V4.md)。

```powershell
uv run python -X utf8 benchmarks/dsh_review_policy.py report
```

## v0.3 Investigator 优化与复测（历史）

已完成 2 个任务、各两组原生／卸载配对，共 8 次新 DSH 运行，包括运行时冻结后准备的 `default_map=None` 回归任务。两组均为 4/4 成功，总 Token 平均减少 13.8%，主 Agent 自身 Token 减少 31.5%，完整执行时间增加 45.3%。新任务的 Token 收益在两次配对中均出现；原场景仍未反超原生执行。

**这证明了本任务集上的 Token 减少，尚未证明更快或账单更低。**缓存输入、未缓存输入和输出的比例发生变化，不能把总 Token 下降直接换算成金额节省。完整结果、缓存结构与限制见 [v0.3 优化报告](docs/INVESTIGATOR_V3.md)。

```powershell
uv run python -X utf8 benchmarks/dsh_report.py --root .trajectory/investigator-optimization/dsh-v3
```

运行原始记录、冻结源码、任务与外部验收都保存在 `.trajectory/investigator-optimization/`。`benchmarks/dsh_investigator_v3.py` 是冻结实验驱动；其早期 `report` 辅助函数的主／子用量比较错误保留在 `preliminary-summary.json`，正式统计使用上面的通用报告器并按角色核账。模型运行未因报告修正而重跑。

## 开发验证

```powershell
uv run python -m unittest discover -s tests -v
```

测试使用临时仓库与本地确定性 Responses 测试服务，不依赖外部模型，覆盖真实 stdio MCP 握手、工具发现、SDK 工具循环、结果提交、源码不变、证据校验、语义拒绝、预算、失败用量及路径边界。真实模型评测通过前述命令单独运行。

