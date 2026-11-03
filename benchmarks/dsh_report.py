"""Descriptive paired analysis of all preregistered DSH trials, including failures."""

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import statistics


def summarize(root: Path):
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    trials, missing = [], []
    for entry in manifest["schedule"]:
        name = f"{entry['task']}-r{entry['repeat']}-{entry['arm']}"
        path = root / "runs" / name / "result.json"
        if path.exists():
            trial = json.loads(path.read_text(encoding="utf-8"))
            audit_path = path.parent / "external-audit.json"
            if not audit_path.exists():
                missing.append(name + ":external-audit")
                continue
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            trial["raw_success"] = trial["success"]
            trial["raw_main_task_success"] = trial["main_task_success"]
            trial["raw_end_to_end_seconds"] = trial["end_to_end_seconds"]
            trial.update({key: audit[key] for key in ("success", "main_task_success", "end_to_end_seconds", "grading_seconds")})
            trial["external_acceptance"] = audit
            trial["verified_patch_path"] = str((path.parent / "verified.patch").resolve())
            ledger = [json.loads(line) for line in (path.parent / "api-ledger.jsonl").read_text(encoding="utf-8").splitlines()]
            trial["peak_main_input_tokens"] = max(((row.get("usage") or {}).get("input_tokens", 0)
                                                  for row in ledger if row.get("role") == "main"), default=0)
            trial["main_usage_crosscheck"] = trial["metrics"]["main"]["total_tokens"] == trial["dsh_reported_total_tokens"]
            trial["main_tool_names"] = sorted({name for row in ledger if row["role"] == "main" for name in row.get("tool_names", [])})
            trial["unexpected_delegation_tools"] = [name for name in trial["main_tool_names"]
                                                       if name in {"subagent", "subagent_fork", "workflow", "send_message"}]
            trials.append(trial)
        else:
            missing.append(name)

    def group(values):
        if not values: return None
        successes = sum(trial["success"] for trial in values)
        complete = all(trial["usage_complete"] for trial in values)
        tokens = sum(trial["total_tokens"] for trial in values)
        return {"trials": len(values), "successes": successes,
                "main_task_successes": sum(trial["main_task_success"] for trial in values),
                "protocol_conformant": sum(trial["protocol_conformant"] for trial in values),
                "usage_complete": complete, "total_tokens": tokens if complete else None,
                "known_token_lower_bound": tokens,
                "mean_total_tokens": tokens / len(values) if complete else None,
                "tokens_per_success": tokens / successes if complete and successes else None,
                "mean_main_tokens": statistics.mean(trial["metrics"]["main"]["total_tokens"] for trial in values) if complete else None,
                "mean_subagent_tokens": statistics.mean(trial["metrics"]["subagent"]["total_tokens"] for trial in values) if complete else None,
                "cached_input_tokens": sum(trial["metrics"][role]["cached_input_tokens"] for trial in values for role in ("main", "subagent")),
                "mean_peak_main_input_tokens": statistics.mean(trial["peak_main_input_tokens"] for trial in values),
                "mean_agent_wall_seconds": statistics.mean(trial["agent_wall_seconds"] for trial in values),
                "mean_end_to_end_seconds": statistics.mean(trial["end_to_end_seconds"] for trial in values),
                "main_model_calls": sum(trial["metrics"]["main"]["model_calls"] for trial in values),
                "subagent_model_calls": sum(trial["metrics"]["subagent"]["model_calls"] for trial in values),
                "offload_calls": sum(trial["offload_calls"] for trial in values)}

    arms = {arm: group([trial for trial in trials if trial["arm"] == arm]) for arm in ("direct", "offload")}
    task_ids = {task["id"]: task["scenario"] for task in manifest["tasks"]}
    tasks = {task_id: {arm: group([trial for trial in trials if trial["task"] == task_id and trial["arm"] == arm])
                      for arm in ("direct", "offload")} for task_id in task_ids}
    scenarios = {scenario: {arm: group([trial for trial in trials if task_ids[trial["task"]] == scenario and trial["arm"] == arm])
                           for arm in ("direct", "offload")} for scenario in dict.fromkeys(task_ids.values())}
    lookup = {(trial["task"], trial["repeat"], trial["arm"]): trial for trial in trials}
    pairs = []
    for task in manifest["tasks"]:
        for repeat in range(manifest["repeats"]):
            left = lookup.get((task["id"], repeat, "direct")); right = lookup.get((task["id"], repeat, "offload"))
            if left is None or right is None: continue
            comparable = left["usage_complete"] and right["usage_complete"]
            pairs.append({"task": task["id"], "scenario": task["scenario"], "repeat": repeat,
                          "direct_success": left["success"], "offload_success": right["success"],
                          "both_successful": left["success"] and right["success"],
                          "token_delta_offload_minus_direct": right["total_tokens"] - left["total_tokens"] if comparable else None,
                          "agent_seconds_delta_offload_minus_direct": right["agent_wall_seconds"] - left["agent_wall_seconds"],
                          "end_to_end_seconds_delta_offload_minus_direct": right["end_to_end_seconds"] - left["end_to_end_seconds"],
                          "offload_calls": right["offload_calls"]})
    effects = {"total_token_wins": sum(pair["token_delta_offload_minus_direct"] is not None and pair["token_delta_offload_minus_direct"] < 0 for pair in pairs),
               "agent_latency_wins": sum(pair["agent_seconds_delta_offload_minus_direct"] < 0 for pair in pairs),
               "pareto_wins": sum(pair["both_successful"] and pair["token_delta_offload_minus_direct"] is not None
                                  and pair["token_delta_offload_minus_direct"] < 0 and pair["agent_seconds_delta_offload_minus_direct"] < 0 for pair in pairs)}
    if arms["direct"] and arms["offload"] and arms["direct"]["usage_complete"] and arms["offload"]["usage_complete"]:
        for key in ("mean_total_tokens", "mean_main_tokens", "mean_peak_main_input_tokens", "mean_agent_wall_seconds", "mean_end_to_end_seconds"):
            effects[key + "_change_percent"] = (arms["offload"][key] / arms["direct"][key] - 1) * 100
    return {"manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
            "design": manifest["design"], "dsh_version": manifest["dsh_version"], "model": manifest["model"],
            "repository": manifest["repository"], "expected_trials": len(manifest["schedule"]),
            "completed_trials": len(trials), "missing_trials": missing, "arms": arms, "scenarios": scenarios, "tasks": tasks,
            "paired_results": pairs, "observed_effects": effects, "trials": trials,
            "timing_note": "agent_wall_seconds is the observed process wall clock. end_to_end_seconds replaces an overstrict grading attempt with uniform independent acceptance duration; post-hoc queue and evaluator-repair delays are excluded.",
            "integrity": {"all_main_usage_crosschecks_match": all(trial["main_usage_crosscheck"] for trial in trials),
                          "all_request_models_valid": all(trial["request_models_valid"] for trial in trials),
                          "no_native_delegation_tools": all(not trial["unexpected_delegation_tools"] for trial in trials),
                          "all_provider_usage_complete": all(trial["usage_complete"] for trial in trials)},
            "quality_noninferiority_established": False, "actual_price_savings_established": False,
            "note": f"{len(manifest['tasks'])} controlled mutations in one repository; repeated trials are clustered by task. Descriptive results only; no general or autonomous-policy claim."}


def markdown(data):
    def fmt(value): return "未知" if value is None else f"{value:,.2f}"
    def row(label, arm):
        return f"| {label} | {arm['successes']}/{arm['trials']} | {fmt(arm['mean_main_tokens'])} | {fmt(arm['mean_subagent_tokens'])} | {fmt(arm['mean_total_tokens'])} | {fmt(arm['mean_agent_wall_seconds'])} | {fmt(arm['mean_end_to_end_seconds'])} |"
    lines = ["# DSH 主 Agent 直接执行与特调卸载：实测结果", "", "日期：2026-11-02。比较对象为真实 DSH 主 Agent 直接执行与其使用特调 Subagent，没有普通 Subagent 组。", "",
             f"已完成 {data['completed_trials']}/{data['expected_trials']} 次冻结清单中的运行；宿主 {data['dsh_version']}，模型 {data['model']}。", "",
             "## 完整任务结果", "", "下表合计全部运行，包括失败、回退和未调用。Token 包含主 Agent、子 Agent 及子 Agent 的语义复核；没有配置实际价格。", "",
             "| 执行方式 | 成功 | 平均主 Agent Token | 平均 Subagent Token | 平均总 Token | 平均主任务冷启动与执行（秒） | 含准备及验收的校正耗时（秒） |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for name, arm in data["arms"].items():
        if arm: lines.append(row("直接执行" if name == "direct" else "特调卸载", arm))
    effects = data["observed_effects"]
    if "mean_total_tokens_change_percent" in effects:
        lines += ["", f"本轮卸载平均总 Token 变化 {effects['mean_total_tokens_change_percent']:+.1f}%，平均主任务耗时变化 {effects['mean_agent_wall_seconds_change_percent']:+.1f}%，准备至外部验收结束的时间变化 {effects['mean_end_to_end_seconds_change_percent']:+.1f}%。",
                  f"{len(data['paired_results'])} 个配对中，卸载总 Token 更低 {effects['total_token_wins']} 次，主任务更快 {effects['agent_latency_wins']} 次，同时更省且更快 {effects['pareto_wins']} 次。", "",
                  ("当前配置没有显示总体净效率收益。" if effects["mean_total_tokens_change_percent"] > 0 and effects["mean_agent_wall_seconds_change_percent"] > 0
                   else "当前观察仅适用于这一冻结任务集。") + "单次正面结果不应替代整组对照，也不能据此否定其他任务分布或长上下文下的潜在收益。",
                  "", "主任务列是实测进程墙钟。校正耗时列将初始误判的验收替换为统一验收持续时间，累计准备、主执行和验收工作量，排除了事后排队与修复评估器的等待时间；它不是未经调整的现实经过时间。原始时长也保留在 JSON 中。"]
    lines += ["", "## 分场景结果", ""]
    for scenario, arms in data["scenarios"].items():
        label = {"impact": "变更影响分析", "diagnosis": "失败诊断", "verification": "候选补丁验证"}[scenario]
        lines += [f"### {label}", "", "| 执行方式 | 成功 | 平均主 Agent Token | 平均 Subagent Token | 平均总 Token | 平均主任务秒数 | 平均校正耗时秒数 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name, arm in arms.items():
            if arm: lines.append(row("直接执行" if name == "direct" else "特调卸载", arm))
        lines.append("")
    lines += ["## 配对明细", "", "正差值表示卸载更贵或更慢，负差值表示卸载更省或更快。", "",
              "| 任务 | 重复 | A 成功 | B 成功 | B−A Token | B−A 主任务秒数 | B−A 端到端秒数 | B 卸载次数 |",
              "| --- | ---: | --- | --- | ---: | ---: | ---: | ---: |"]
    for pair in data["paired_results"]:
        lines.append(f"| {pair['task']} | {pair['repeat']} | {pair['direct_success']} | {pair['offload_success']} | {fmt(pair['token_delta_offload_minus_direct'])} | {fmt(pair['agent_seconds_delta_offload_minus_direct'])} | {fmt(pair['end_to_end_seconds_delta_offload_minus_direct'])} | {pair['offload_calls']} |")
    if data["completed_trials"] == data["expected_trials"]:
        direct, offload = data["arms"]["direct"], data["arms"]["offload"]
        lines += ["", "## 轨迹分析", "",
                  f"两组模型请求分别为 {direct['main_model_calls']} 次与 {offload['main_model_calls'] + offload['subagent_model_calls']} 次；卸载组包含主 Agent {offload['main_model_calls']} 次、Subagent {offload['subagent_model_calls']} 次。六次卸载运行共尝试 {offload['offload_calls']} 次任务级工具调用，输入失败和重试均计入。",
                  f"卸载组主 Agent 自身平均 Token 变化 {effects['mean_main_tokens_change_percent']:+.1f}%，平均单次峰值输入变化 {effects['mean_peak_main_input_tokens_change_percent']:+.1f}%。本轮没有观察到稳定的主 Agent 负担或上下文收益。", "",
                  "日志中可以直接看到几类开销：调用者把 Context.__init__ 等限定名称交给只接受 Python identifier 的 symbols 参数，引起验证失败与重试；影响调查返回 partial 后主 Agent 继续原生调查；修复后再次调用失败诊断、使用过期快照或再次提交无效 Patch。它们属于实际调用和集成成本，不能从总账里删除。各因素的独立贡献尚未做消融量化。", "",
                  "补丁验证的成功分支没有调用子模型，是确定性执行工作流。首次配对中总 Token 下降，但仍更慢；第二次没有复现 Token 优势。因此它提供了局部执行编排的潜在线索，没有证明 LLM Subagent 本身的收益。", "",
                  "这些任务修复点小，且主 Agent 从新会话开始。结合轨迹，我推断当前接口、交接、停止与验证开销尚不能被省下的步骤抵消。这个判断适用于所测配置；是否能在长历史、跨模块或更复杂任务中获益，仍需新的保留任务验证。"]
    lines += ["", "## 解释范围", "", "- 真实 Click 仓库，3 个受控回归，各做两次 A/B；不等同于随机抽样的真实历史 Issue。", 
              "- 本轮 B 要求调用指定工具，是受控卸载机制实验，不证明自主选择卸载的策略有效。", 
              "- 两组均有原生编辑与脚本能力，原生额外委派禁用；同模型、同推理配置、相同任务和起点。", 
              "- 成功由原始测试与不传给模型的行为验收决定；改动既有测试或配置不能算成功。", 
              "- 每次新建宿主进程的启动计入主任务时间；这不表示清空了 OS 文件缓存或提供方 KV 缓存。一次性安装不摊入单次运行，缓存单列，未推算真实金额。", 
              "- 任务重复存在聚类，样本不足以建立质量非劣结论；不据此推广到长历史、其他仓库或其他任务分布。", 
              "- 本机 Windows 限制执行模式的 ACL 初始化失败，两组统一使用相同的非 OS 沙箱本地权限。", "",
              "## 评估器修正披露", "", "首个原生 Agent 在现有文件中新增测试函数，被初始字节级文件保护误判。所有运行统一复核原测试 AST 语句是否完整保留；对新增函数的文件，使用原始测试与配置、覆盖候选生产源码，在新副本中独立验收。没有重跑 Agent、移除样本或改变任务、模型及 Subagent。原始评分保存在 result.json，修正依据保存在 external-audit.json；这是运行期间发现并披露的评估器修正，因此本轮仅作探索性结论。", "",
              "协议见 [DSH_STUDY_PROTOCOL.md](DSH_STUDY_PROTOCOL.md)。完整数据见 [summary.json](../.trajectory/dsh-study/summary.json)，每次运行的 events、API 账本、回归和隐藏验收结果保存在 .trajectory/dsh-study/runs。", "",
              "每次的 verified.patch 从最终工作副本按原始字节导出，并对对应初始仓库执行 git apply --check。原 result.patch 也保留；它曾经过 Windows 文本换行转换，不作为可应用补丁使用。此修正只影响导出文件，不影响 Agent、验收或计时。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent / ".trajectory/dsh-study")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    data = summarize(args.root)
    (args.root / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.report:
        if data["missing_trials"]: raise ValueError("formal report requires every preregistered trial")
        args.report.write_text(markdown(data), encoding="utf-8")
    print(json.dumps({key: data[key] for key in ("completed_trials", "expected_trials", "arms", "scenarios")}, ensure_ascii=True))
