# v0.7 源码事实编译与已知状态交接

v0.7 保留主 Agent 主动调用 MCP＋Skill 的方式，优先改进只读 Python Repository Investigator。使用与主 Agent 相同的模型和推理配置，不额外强制模型审稿，不改变补丁验证器和故障诊断器的必跑检查。完整模型报告可用 `--full-model-report` 恢复。`--compact-model-report` 保留旧的精简提交实验。Python 调用旧协议时设置 `Settings(compiled_report=False)`。

## 对应 v0.4 的具体不足

- 主 Agent 委派前通常已经读过源码，但四次委派中三次没有传入已知线索。另一例传入了准确线索，仍委派了整份调查。
- 最后的取证回合仍有单行搜索，测试断言和参数化条件未得到充分观察。两次默认值调查返回 partial。
- 报告生成占子 Agent 输出 Token 的 81.8%。这些样本没有报告修补重试，不能把 v0.5、v0.6 的重试问题归到 v0.4。
- 引用和行号校验通过，仍出现不存在的 `Context.to_context`，以及把 5→4 误认为会改变 `>=4` 条件结果的语义错误。
- v0.4 两臂各运行四次完整测试，不能把其延迟差归因于卸载组重复执行完整测试。缓存输入、未缓存输入、输出和非 API 时间需要分别核算。

## 委派契约

`InvestigationTask` 和 MCP `investigate_repository` 新增以下交接字段。

| 字段 | 语义 |
| --- | --- |
| known_sources | 已读源码的 path/start_line/end_line，可附 expected_quote。最多 12 段、单段 100 行，总计 260 行 / 16000 字符 |
| remaining_criteria | 需要新调查的零基验收问题索引。未提供时默认为全部，空数组表示复用已有源码 |
| expected_snapshot_id | 可选的已知快照，用于拒绝过期交接 |

`known_evidence` 仍作为未验证提示。程序从当前快照重新读取 `known_sources`，校验范围、作用域和可选原文，构造独立上下文。已知源码与预取源码共享初始上下文预算。重复读取按证据 ID 复用。`remaining_criteria` 只指示新增工作，完整 `acceptance_criteria` 始终保留，输出必须逐项提供证据。

Skill 指导主 Agent 传递已读范围、在有缺口时发起调查。当前上下文已足以完成调查时，主 Agent 直接继续任务。该选择策略尚未经过自主卸载实验，本轮仍强制卸载以评估执行器。

## 事实、推断与校验范围

默认 `--compiled-report` 下，源码观察复用 E 证据 ID。程序用 AST 确定已读源码片段所属的限定符号，测试体和可校验的枚举比较使用额外的 F 事实 ID。程序在观察到必要原文后才发放事实 ID。测试事实要求观察装饰器、参数表和完整函数体。

模型为每个问题选择 E 或 F ID，提交简短解释、证据充分性和未解决事项。程序生成 findings、related_tests、精确引用、覆盖索引和摘要。格式不合规时可用 `amend_investigation` 局部修补选择结果。

MCP 默认返回 `format_version=4`。

- `findings` 记录程序生成的源码观察。
- `related_tests` 记录对完整测试声明体的静态观察。
- `inferences` 记录模型的解释与推断。
- `verification.source_facts_mechanically_checked` 标记事实编译和源码引用检查是否成功。
- `verification.comparison_checks` 记录针对单个比较谓词的可选静态值替换检查。
- `verification.natural_language_inferences_verified=false` 标记自然语言推断未经程序验证。
- `verification.semantic_review_status` 记录独立模型复核状态，默认 `not_requested`。

引用仍在顶层 evidence 表存储一次，条目引用 E ID。CLI/Python 返回展开引用。旧报告模式仍返回 MCP 格式 3。

成员名检查仅针对本地 AST 可见类及继承成员，不能证明动态属性和外部类型。枚举检查不导入、不执行仓库代码，仅对简单声明值执行白名单比较操作。证明范围限于被检查的比较条件。`completed` 仍是模型报告验收问题已回答且程序检查通过。需要执行测试的验收由原有执行工具或主 Agent 完成。

## 实现与验证

核心改动在 `src/trajectory_offload/facts.py`、`runtime.py`、`contracts.py`、`repository.py`、`server.py`。CLI 默认选择新协议。只读调查采用事实编译，执行型诊断继续使用原有包含运行记录的报告协议。无新增项目依赖。

离线验证包括原有快照、路径、预算、执行策略和兼容性检查，以及不存在的成员、枚举阈值、完整测试体、未知事实 ID、过期源码、验收不可豁免和实际 stdio MCP＋Responses SDK 传输。Skill 通过 quick_validate。

开发回放保存于 `.trajectory/compiled-report-optimization/probe-env` 和 `probe-default-map`。首次回放暴露了私有事实条目上限不一致，已对齐公开报告容量。日志完整保留当时的修补、Token 和耗时。默认值开发回放保留测试覆盖缺口，不算成功调查。开发回放不混入正式评测均值。

## 正式评测设计

复用 v0.4 的 env-prefix-impact、default-map-none-impact 两个任务、代码快照、原有测试和隐藏验收，每个任务两次重复。首轮交错执行 v0.4 和初版 v0.7 卸载，共八次。v0.4 从独立环境的原版 0.4 wheel 启动，两版依赖版本一致。各轮冻结并校验实际 worker 源码指纹。原生主 Agent 沿用历史记录。

初版 v0.7 的四次修复通过验收，其中一次用量超出预算。相对同期 v0.4，平均总 Token 增加 58.2%，平均耗时增加 3.3%。源码与逐语句事实目录同时进入模型上下文，三次调查触发合计四次报告修补。报告还把预期的只读限制写成了部分未解决问题。最终候选复用源码证据 ID，在比较字段中明确约束事实 ID，并要求只报告实际验收缺口。

固定模型 `deepseek-v4.1-flash`、reasoning=low、主任务 300 秒 / 500000 Token、子 Agent 150 秒 / 200000 Token。保留所有输入拒绝、模型修补、主 Agent 回退和最终请求预算超额。Token 为父子调用总和，缓存输入不重复计入。最终补丁必须通过原有完整测试、隐藏验收、测试 AST 和配置保护，并验证可应用性。

两版比较的是完整卸载系统。v0.7 同时包含报告编译、取证及交接指导。实验衡量这些改动的组合效果。v0.4 保留原有宿主指导，v0.7 提供新增交接字段和完成编辑后执行完整验证的指导。两版最终验收不变。

首轮数据见 [交错对照](../.trajectory/compiled-report-optimization/dsh-v7/comparison.json)。最终候选只执行四次卸载，数据保存在 `.trajectory/compiled-report-optimization/final/dsh-v7`。最终候选与对照分时段运行，样本只有一个仓库、两个曾用于开发的受控回归。度量为实际 API 用量和墙钟时间，计费金额未测量。

## 复测结果

| 运行组 | 平均总 Token | 平均未缓存输入 | 平均输出 Token | 平均主任务耗时 | 通过验收且在预算内 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 历史原生主 Agent | 377,202 | 23,574 | 6,428 | 130.0 秒 | 3/4 |
| 历史 v0.4 卸载 | 269,263 | 48,004 | 7,179 | 147.1 秒 | 4/4 |
| 首轮同期 v0.4 卸载 | 278,254 | 48,540 | 8,914 | 158.7 秒 | 4/4 |
| 初版 v0.7 卸载 | 440,134 | 98,025 | 9,341 | 163.9 秒 | 3/4 |
| 最终 v0.7 卸载 | 319,334 | 44,632 | 6,862 | 157.8 秒 | 4/4 |

最终版相对初版总 Token 减少 27.4%，四次报告修补降到零。相对首轮同期 v0.4，子 Agent 平均耗时由 31.9 秒降到 22.6 秒，汇总请求合计输出由 14,736 降到 7,051 Token。端到端未缓存输入减少 8.1%，输出减少 23.0%，缓存输入增加 21.3%，总 Token 增加 14.8%，平均耗时相差 0.8 秒。相对历史原生主 Agent，总 Token 减少 15.3%，耗时增加 21.4%。

四个最终补丁均通过原有测试、隐藏验收和可应用性检查。每次调查均返回 `partial`，由主 Agent 处理缺口。其中一次主 Agent 在完整测试后又添加回归用例并重跑全套，主 Agent 用量达 462,127 Token。该次运行计入平均值。验收项相同，主 Agent 自行生成的委派问题会变化，因此结果反映完整流程，包含主 Agent 的决策波动。

父子用量、模型配置、实际子 Agent 版本、调用记录和测试保护均已核对。最终四次均未请求独立复核，也未发起静态比较检查。两个已知错误的防护由 82 项本地测试中的回归用例覆盖。 [最终对照与逐次审计](../.trajectory/compiled-report-optimization/final/dsh-v7/comparison.json)
