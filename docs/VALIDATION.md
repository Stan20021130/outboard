# v0.1 验证记录（历史）

日期：2026-11-02。本文区分工程功能验证与研究收益验证。

本文保留 v0.1 当时的实现与实验结果；当前版本见 [v0.2 验证记录](VALIDATION_V2.md)。旧版本的工具数量、测试数量和实现指纹不代表当前代码。

## 环境

- Python：3.12.15。
- 原生基线：Codex CLI `0.166.0-alpha.5.1`；临时模型目录适配第三方函数调用，推理模型名不变。
- 模型：`deepseek-v4.1-flash`；推理配置：`low`。
- API：`http://127.0.0.1:8788/v1`；本地服务使用占位密钥。
- 依赖：openai 2.54.0, mcp 1.30.0, pydantic 2.14.2。

## 工程验收

| 要求 | 结果与证据 |
| --- | --- |
| 可运行入口 | `uv sync --locked` 成功；`trajectory-offload` 提供 serve、investigate、evaluate、profile |
| 单一任务级 MCP Tool | stdio 握手、工具发现和 structuredContent 测试通过，仅暴露 investigate_repository |
| 宿主接入 | 项目 .codex/config.toml 被 Codex 识别，Server 为 enabled、required，绑定本机工作区；debug prompt-input 确认能发现项目 Skill 元数据 |
| 只读与边界 | 模型只持有受限源代码工具；越界、未观察引用、伪造引用、源码变化测试通过 |
| 隔离副本一致性 | 支持的源文件按原始字节复制，BOM、CRLF 与快照标识一致性测试通过；原始测试也按字节检查和恢复 |
| 预算与失败 | 模型与工具轮次、Token 预检、迟到复核、最终接口超额用量、复核失败及未知用量测试通过，未通过不标 completed |
| 三种场景 | 实现定位、配置传播、失败测试相关代码定位均已用真实 API 运行 |
| 验证步骤 | Pydantic 契约、引用／覆盖、独立语义复核、结束快照检查均保留 |
| 三组对照 | 原生 Codex、普通 Subagent、特调 Subagent 均已运行，历史失败结果保留 |
| 主 Agent 集成 | 要求真实卸载的联通测试成功；主 Agent 调用后修改隔离副本并通过原始测试 |
| 轨迹分析 | 普通／特调与原生轨迹 profile 命令均成功，输出高成本运行及重复序列 |
| 自动化测试 | 27 项 unittest 全部通过，包含真实 stdio + SDK 传输的确定性测试 |
| 分发构建 | wheel 与源码包构建成功，wheel 在隔离环境可独立导入、运行 CLI 和发现 MCP 工具；未包含 .trajectory、.venv、.env 或 __pycache__ |

验证命令：

```powershell
uv sync --locked
uv run python -m unittest discover -s tests -v
uv build
uv run trajectory-offload profile --log-dir .trajectory/prefetch-subtasks/runs --output .trajectory/trajectory-profile.json
```

## 开发集观察：不能作为正式质量非劣结论

三条小型 Fixture 同时用于开发和调试，没有保留为独立测试集。以下是单轮观察，未进行统计显著性或跨仓库评估。

### 预取优化前

| 方式 | 完整通过 | 总 Token | 平均延迟（秒） |
| --- | --- | --- | --- |
| native | 2/3 | 252,892 | 96.19 |
| generic | 2/3 | 73,629 | 29.48 |
| specialized | 1/3 | 94,475 | 41.41 |

该轮特调版本未稳定优于普通版本，结果没有删除。随后加入有上限的词法锚点／导入邻域预取，仍使用相同结果协议与验证器。

### 加入预取后的 B／C 对照

| 方式 | 完整通过 | 总 Token | 平均延迟（秒） |
| --- | --- | --- | --- |
| generic | 2/3 | 67,575 | 31.72 |
| specialized | 2/3 | 41,504 | 21.33 |

本轮特调总 Token 低 38.6%，平均延迟低 32.7%，两组完整通过数相同。这不证明真实费用下降或质量非劣。

未通过的分页任务被独立复核拦截：特调结果错误推断末页测试也会失败；实际切片会在列表末尾截断。结果保持 partial。普通版本也出现了缺少依据的全仓测试唯一性断言。

各轮的缓存命中和版本指纹见本机原始结果；不将不同轮次混合成同一次配对实验。

## 主 Agent → MCP → Subagent → 修复 → 回归测试

该用例显式要求先调用工具，用于验证集成，不用于证明自主卸载策略的经济性。数据设置 require_offload=true，未调用不能计为成功。

- 真实调用次数：2；包含一次输入范围不合适的失败调用及一次完成的调查，相关开销均计入。
- 初始失败：环境超时用例得到 30，而断言要求 7。
- 主 Agent 修改隔离副本的 sample_app/middleware.py，从 settings["timeout_seconds"] 读取默认路径的值。
- 最终：原始超时回归 3 项全部通过；测试文件与原仓库保持原状。
- 整链 Token：177,956；耗时：185.97 秒；未定价。
- Patch：[实际修改](../.trajectory/final-mcp-conformance/runs/46e408a5daaa444080196ea9002fdd48.patch)。
- 原始报告：[联通结果](../.trajectory/final-mcp-conformance/report.md)。

Windows 沙箱创建的缓存有时无法由父进程删除。该用例记录了 cleanup_retained_path；清理失败作为独立事件保留，不覆盖已经完成的测试结果，也不调整文件权限。

早期简单分页修复中，三组均完成但 B／C 未发生卸载；当时可选 MCP 初始化的工具可见性问题尚未排除，因此这些结果只能验证直接修复底座，不能证明调用策略正确或卸载收益。

## 本项目源码调查

对本项目源码询问“证据原文匹配在哪里校验、快照／语义复核如何控制 completed”。在 200000 Token 预算下得到 completed，使用 7 次模型调用、92,215 Token。引用、独立语义复核和快照检查全部通过。

同一问题在默认 80000 Token 预算下曾返回 partial，说明预算结束不会被包装为完成。复杂任务需要结合实际上下文与复核开销设定预算。

结果：[源码调查](../.trajectory/real-repository-generous-budget.json)。该运行的引用已通过原文与行号复核，并绑定运行时的快照和实现指纹；后续代码变更可能使当前文件的行号偏移。

## 尚未证明与已知边界

- 没有建立质量非劣的置信区间；需要更多保留任务、真实仓库和重复运行。
- 没有配置价格，费用为 null；Token 减少不等于实际金额同比减少，缓存计费也需要单独考虑。
- 端到端联通用例有明确调用要求，不能据此评价自主调用的误触发、漏调用或净收益。
- 原生 CLI 逐轮推理计数、峰值上下文与逐次输出限制不能全部对齐和观测；当前 A 组用于接入与基线烟测，正式效率实验仍需更细的宿主记录。
- 快照覆盖支持的 UTF-8 源码、配置与文档，且不充当完整仓库备份；Investigator 不运行测试或证明运行时根因。
- 自动生成／搜索 Subagent 编排、通用任务库、大规模基准和在线经济性学习属于后续工作。

本机原始日志位于 .trajectory/ 与默认运行日志目录中，没有打入分发包。运行记录保存实现指纹、快照标识和用量，方便区分开发迭代。
