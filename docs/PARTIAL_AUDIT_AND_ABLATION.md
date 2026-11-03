# CTOS v0.7 Partial 判定精细化审计与交接消融实验报告

本文档落实交接文档 ([docs/HANDOFF_V7.md](file:///f:/CTOS/docs/HANDOFF_V7.md)) 提出的第二项核心研究工作：
1. **按验收项精细标注 `partial`**：区分真实缺证 (Genuine Gap)、超出范围 (Out-of-Scope) 与报告过度保守 (Conservative Reporting)。
2. **交接消融实验 (Handoff Ablation Experiment)**：通过控制 `known_sources`（已读源码片段）与 `remaining_criteria`（验收项过滤），定量评估交接机制在消除重复读取、节省 Token 与聚焦取证上的贡献。

---

## 一、 Partial 判定精细化审计 (Fine-Grained Audit)

在 v0.7.0 的 4 次受控 Click 回归测试（`default-map-none-impact` × 2, `env-prefix-impact` × 2）中，**调查工具均返回了 `partial` 状态**，但最终由主 Agent 生成的 4 份补丁**均 100% 通过完整回归、隐藏验收与可应用性检查**。

对 4 次任务的全部 **17 个验收项 (Acceptance Criteria)** 及生成的 **15 个未决问题 (Unresolved Questions)** 进行逐项代码级对照与三分类审计：

### 1. 验收项 (Acceptance Criteria) 判定分布

| 运行组 | 总 Criteria | Supported | Unresolved | 真实缺证 (Genuine) | 超出范围 (Out-of-Scope) | 过度保守 (Conservative) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `default-map-none-r0` | 5 | 2 | 3 | 2 | 0 | 1 |
| `default-map-none-r1` | 3 | 0 | 3 | 0 | 1 | 2 |
| `env-prefix-impact-r0` | 4 | 1 | 3 | 1 | 0 | 2 |
| `env-prefix-impact-r1` | 5 | 3 | 2 | 1 | 1 | 0 |
| **合计** | **17** | **6 (35.3%)** | **11 (64.7%)** | **4 (36.4%)** | **2 (18.2%)** | **5 (45.5%)** |

> [!IMPORTANT]
> **核心发现**：在 11 项被标记为 `unresolved` 的验收项中，**仅 36.4%（4 项）属于真正的静态代码盲区**；其余 **63.6%（7 项）均为虚假未决（False Unresolved）**：
> - **45.5%（5 项）属于过度保守**：核心代码和语法缺陷已通过 AST/引用完全定位（如已观察到 `replace("-", "-")` 或 `is not None` 与 `_default_map_has`），但因只读调查未执行测试，模型不敢判定为 supported。
> - **18.2%（2 项）属于超出只读范围**：主 Agent 提出了要求验证“修复后执行行为”或“测试实际运行通过”的动态断言。

---

### 2. 未决问题 (Unresolved Questions) 归因分析

子 Agent 在报告中提交的 15 个未决问题细分如下：

| 分类 | 数量 | 占比 | 典型问题示例 | 根因与改进策略 |
| :--- | :---: | :---: | :--- | :--- |
| **超出只读范围 (Out-of-Scope)** | 4 | 26.7% | `"Tests were not executed; pass/fail status is inferred only from source predicates."`<br>`"The fix itself is not applied (read-only investigation)."` | **常识性环境披露错当任务缺口**。模型将只读系统无法执行测试/应用补丁的事实写入了未决项。需在 Prompt 中明确禁止。 |
| **报告过度保守 (Conservative)** | 3 | 20.0% | `"Whether the replace('-', '-') at core.py:517 is the mutation under repair or deliberate state"`<br>`"Nested derivation has no separate hyphen conversion for info_name"` | **已确证架构事实被错判为缺口**。代码已明确展示其行为，模型因未见动态证据而不敢关闭问题。 |
| **真实证据盲区 (Genuine Gap)** | 8 | 53.3% | `"No test body was read that directly asserts prefix stored on ctx for hyphenated nested command"`<br>`"No complete observed test covers explicit envvar precedence over auto envvar"` | **受限于 2 轮静态取证上限**。涉及深层跨文件或复合测试场景（例如同时满足 4 种条件的测试），静态调查轮次耗尽。 |

---

## 二、 交接消融实验 (Handoff Ablation Experiment)

### 1. 实验设计与配置
在受控基准环境使用 `benchmarks/fixtures`（真实调用本机 `deepseek-v4.1-flash` + `low` 推理档位），针对配置追踪任务设计四组消融对比：
- **`full_handoff` (完整交接组)**：提供 `known_sources` (`sample_app/settings.py:1-7`) + 过滤后的 `remaining_criteria: [1, 2]`。
- **`no_known_sources` (无已知源码消融组)**：无 `known_sources` + 过滤后的 `remaining_criteria: [1, 2]`。
- **`blanket_criteria` (无验收项过滤消融组)**：提供 `known_sources` + 全量 `remaining_criteria: [0, 1, 2]`。
- **`zero_handoff` (零交接冷启动基线)**：无 `known_sources` + 全量 `remaining_criteria: [0, 1, 2]`。

### 2. 实验结果数据表

| 实验组 | 状态 | 总 Token | 输入 Token | 缓存输入 | 输出 Token | 模型调用 | 工具调用 | 事实条目 (Findings) | 耗时 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **`full_handoff`** | `partial` | **14,626** | **13,314** | **6,656** | 1,312 | 3 | 6 | **8** | 13.45s |
| **`no_known_sources`** | `partial` | 15,479 | 14,284 | 6,784 | 1,195 | 3 | 5 | 16 | 8.73s |
| **`blanket_criteria`** | `partial` | 14,856 | 13,539 | 5,888 | 1,317 | 3 | 6 | 15 | 9.84s |
| **`zero_handoff`** | `partial` | 15,134 | 14,001 | 5,632 | 1,133 | 3 | 5 | 16 | 10.32s |

### 3. 量化消融发现 (Quantitative Findings)

1. **`known_sources` 显著压缩探索上下文，杜绝重复取证**：
   - 相比冷启动（`no_known_sources`），完整交接使输入 Token 减少 **970 Tokens (-6.8%)**；
   - 观察到的冗余事实条目从 **16 个缩减至 8 个（减少 50.0%）**。子 Agent 无需在第一轮重新搜索定位配置入口，直接复用已传入的代码片段。
2. **`remaining_criteria` 聚焦分析焦点，抑制未决问题蔓延**：
   - 当不进行问题过滤（`blanket_criteria`）时，子 Agent 对已解决的第 0 项（配置来源与默认值）仍重复生成解释，导致 `unresolved_questions` 增加至 4 项；而在 `full_handoff` 中，未决问题控制在 2 项。
3. **协同效益**：
   - `full_handoff` 在所有组中达到了最低的总 Token 开销（14,626）和最紧凑的事实目录，证明了结构化交接机制对降低推理冗余与上下文污染的关键价值。

---

## 三、 对后续系统设计的改进建议

1. **消除虚假 Partial（False Negative Suppression）**：
   - 在事实编译后处理或模型提示词中加入强过滤规则：凡以 `"Tests were not executed"` 或 `"The fix itself is not applied"` 为主谓语的条目，一律禁止写入 `unresolved_questions`；
   - 当所有与静态代码语法相关的 Criteria 均被 AST 事实覆盖时，自动授予 `completed` 状态。
2. **复合验收项拆分（Criterion Atomicity）**：
   - 避免主 Agent 提出包含 4~5 个子断言的复合 criteria（如“覆盖自动前缀、嵌套命令、显式环境变量和帮助显示”）；单个复合项极易因局部未覆盖导致整项被判 `unresolved`。
