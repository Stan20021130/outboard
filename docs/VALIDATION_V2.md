# v0.2 验证记录

日期：2026-11-02。新增范围为变更影响分析、补丁验证、失败复现与诊断。工程联通与效率收益分别判断。

## 环境与接入

- Python 3.12.15，模型 deepseek-v4.1-flash，推理配置 low。
- API 为用户指定的 http://127.0.0.1:8788/v1，使用无保密含义的占位密钥。
- 继续使用锁定的 openai、mcp、pydantic；未增加第三方依赖。
- 项目 MCP 注册 get_repository_state、investigate_repository、verify_patch、diagnose_failure。未提供启动检查配置时，只注册前两个只读工具。
- Responses 沿用函数型结构化提交，工具参数采用严格对象契约；接口规则参考 [OpenAI 官方 Function calling 文档](https://developers.openai.com/api/docs/guides/function-calling)。

## 自动化验证

最终执行 `uv run python -m unittest discover -s tests -v`，40 项全部通过，耗时 77.90 秒。测试使用临时仓库、真实 Python 子进程和确定性的本地模型传输服务，不依赖外部模型的随机输出。

新增及扩展验证包括：

- 有界影响上下文提供导入、符号和测试证据，不承诺完整运行时覆盖。
- 原版检查失败、补丁版通过，原仓库及原有测试不变；全通过分支无额外模型调用。
- 验证任务 completed 与补丁 verdict=failed 可以同时出现，避免把验证结束解释成修复成功。
- 过期快照、未知检查 ID、越界 Patch、修改既有测试及不匹配的 Patch 上下文均不能启动有效验证。
- Patch 的新增、删除、BOM 与 CRLF 保留。
- 进程超时、输出超限及源码副本被检查修改，均不能返回通过。
- 检查本身通过后，结束快照发现源码再次变化，最终状态仍为 partial、inconclusive。
- 失败重复复现、模型追加白名单检查、运行时持有阶段标签、独立复核能看到检查证据。
- 不同检查项各自稳定的成功／失败不会误报为随机失败；按检查项记录 reproduction_by_check。
- 检查进程不继承 OPENAI_API_KEY；未启用检查策略时没有执行工具。
- 真实 stdio MCP 握手、四项工具发现、状态查询、调查和补丁验证；返回不包含中间日志路径。
- 模型、检查运行及最终结果共用运行账，嵌套诊断结果不重复计为独立运行；profile 包含检查序列。

既有证据、预算、语义拒绝、Git 脏文件／未跟踪文件、原生基线用量和清理失败等检查继续通过。

## 真实 MCP 与模型联调

本轮通过真实 stdio 客户端调用 MCP Server，再由 Server 调用指定模型。使用开发 Fixture，不包含原生主 Agent 的自主决策。因此验证的是接口、执行器和证据链，不能证明自主卸载的经济性。

| 场景／轮次 | 状态 | 模型调用 | 子进程 | 总 Token | 任务耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| 影响分析，单次输出 5000 上限 | partial | 3 | 0 | 20,089 | 42.64 秒 |
| 影响分析，单次输出 8000 上限 | completed | 3 | 0 | 18,780 | 32.91 秒 |
| 超时修复补丁验证 | completed，passed | 0 | 2 | 0 | 4.11 秒 |
| 超时失败复现与诊断 | completed，reproduced | 4 | 2 | 32,058 | 50.56 秒 |

这些是不同任务或配置的记录，没有合并为一组配对效率实验。耗时为各任务 Runtime 记录，不包含外层客户端和 Server 初始化。0 Token 不代表 0 计算、0 时间或 0 费用。

首次影响分析的引用检查通过，但独立复核调用的 5000 输出 Token 全部被接口报告为推理用量，响应 incomplete。系统保持 partial，没有跳过复核。提高单次输出额度至 8000 后，另一次独立调用 completed，源码引用、验收覆盖、语义支持和快照检查全部通过；不能据此保证以后每次调用均能完成。

项目 MCP 的单次输出上限已据此设为 8000，总 Token 上限仍为默认 80000；CLI 默认输出上限保持 5000，联调脚本使用 8000／总量 200000 并记录配置。不同配置不混合用于效率比较。

补丁验证先在原版记录超时测试失败，再在临时副本中把默认路径改为消费 settings["timeout_seconds"]，最终原有三项超时测试全部通过。原仓库保持原样，Patch 没有被自动合入。

失败诊断在原始源码副本上两次得到同一字面量失败 AssertionError: 30 != 7，并返回失败断言、输入、配置加载、传播和消费位置的源码证据。root_cause_confirmed 仍为 false；复现与源码支持不等同于完成因果实验。

本机记录：

- [首轮完整联调](../.trajectory/v2-mcp-smoke/summary.json)。
- [影响分析额度调整后独立重跑](../.trajectory/v2-impact-output-budget/summary.json)。
- [补丁验证详情](../.trajectory/v2-mcp-smoke/verify_patch.json)。
- [诊断详情](../.trajectory/v2-mcp-smoke/diagnose_failure.json)。
- [影响分析详情](../.trajectory/v2-impact-output-budget/investigate_repository.json)。
- [最终代码的补丁验证重跑](../.trajectory/v2-final-verification/summary.json)：completed、passed，2 次检查、0 模型调用、3.78 秒，原仓库不变。

原始记录保留当次实现指纹与快照 ID。联调后增加的多检查项分类、执行配置指纹记录与结束快照门槛，已纳入最终 40 项自动化回归。

## 执行与结论边界

执行工具只接受预先登记的检查 ID。源码副本、精确 Patch、原有测试保护、进程上限、干净环境及原仓库快照检查均已实现。检查的实际参数、阶段、退出状态、时间和输出摘要写入记录。

临时源码副本不是 OS 沙箱：执行的代码仍使用当前用户的文件与网络权限，只适用于受信任代码。依赖由宿主 Python 提供，尚无依赖安装、容器、二进制资产或完整 Git 历史物化。补丁暂不支持重命名、二进制和 Git 引号路径。

当前没有证明新增场景比主 Agent 直接执行更省、更快且质量非劣。既有 evaluate 入口继续支持只读 A／B／C 和完整修复基线；新增执行场景的正式配对基准、跨仓库评估与自主调用策略评估仍需后续开展。未配置实际价格，金额为 null。

复现实验命令与检查配置见 [README](../README.md)。v0.1 的开发对照及负面结果保留在 [历史验证记录](VALIDATION.md)。本机 .trajectory 原始数据不进入分发包。
