"""N=3 Multi-Run Statistical Evaluation for Mini-Redis Greenfield Benchmark under /goal directive.
Conducts 3 independent trials for Native Control Arm vs Outboard Offload Arm.
Aggregates mean, min, max, and cost metrics into .trajectory/redis_3runs_result.json.
"""
import json
import statistics
import time
from pathlib import Path

from eval_redis_goal_ab import execute_native_arm, execute_offload_arm, RESULT_FILE

MULTI_RESULT_FILE = Path(__file__).resolve().parent.parent / ".trajectory/redis_3runs_result.json"

def aggregate_arm_runs(runs: list[dict]) -> dict:
    wall_clocks = [r["wall_clock_seconds"] for r in runs]
    total_tokens = [r["total_tokens"] for r in runs]
    prompt_tokens = [r["prompt_tokens"] for r in runs]
    completion_tokens = [r["completion_tokens"] for r in runs]
    turns = [r["turns"] for r in runs]
    peak_costs = [r["costs"]["peak_cost_cny"] for r in runs]
    offpeak_costs = [r["costs"]["offpeak_cost_cny"] for r in runs]
    passed_counts = [r["oracle"]["passed"] for r in runs]

    return {
        "trials_count": len(runs),
        "wall_clock_seconds_mean": round(statistics.mean(wall_clocks), 2),
        "wall_clock_seconds_min": round(min(wall_clocks), 2),
        "wall_clock_seconds_max": round(max(wall_clocks), 2),
        "total_tokens_mean": int(statistics.mean(total_tokens)),
        "total_tokens_min": min(total_tokens),
        "total_tokens_max": max(total_tokens),
        "prompt_tokens_mean": int(statistics.mean(prompt_tokens)),
        "completion_tokens_mean": int(statistics.mean(completion_tokens)),
        "turns_mean": round(statistics.mean(turns), 1),
        "peak_cost_cny_mean": round(statistics.mean(peak_costs), 4),
        "offpeak_cost_cny_mean": round(statistics.mean(offpeak_costs), 4),
        "oracle_passed_mean": round(statistics.mean(passed_counts), 1),
        "oracle_pass_rate_mean": f"{(statistics.mean(passed_counts) / 20) * 100:.1f}%",
        "trials": runs,
    }

def main():
    print("="*75)
    print("Mini-Redis Greenfield N=3 Multi-Run Benchmark (/goal directive)")
    print("="*75)

    native_runs = []
    offload_runs = []

    for trial in range(1, 4):
        print(f"\n==================== TRIAL {trial} / 3 ====================")
        # Execute Native Control Arm
        n_res = execute_native_arm(trial)
        native_runs.append(n_res)

        # Execute Outboard Offload Arm
        o_res = execute_offload_arm(trial)
        offload_runs.append(o_res)

    native_agg = aggregate_arm_runs(native_runs)
    offload_agg = aggregate_arm_runs(offload_runs)

    delta = {
        "time_speedup_seconds": round(native_agg["wall_clock_seconds_mean"] - offload_agg["wall_clock_seconds_mean"], 2),
        "time_speedup_percent": f"{(1 - offload_agg['wall_clock_seconds_mean'] / native_agg['wall_clock_seconds_mean']) * 100:.1f}% faster",
        "tokens_saved_count": native_agg["total_tokens_mean"] - offload_agg["total_tokens_mean"],
        "tokens_saved_percent": f"{(1 - offload_agg['total_tokens_mean'] / native_agg['total_tokens_mean']) * 100:.1f}%",
        "completion_tokens_saved_percent": f"{(1 - offload_agg['completion_tokens_mean'] / native_agg['completion_tokens_mean']) * 100:.1f}%",
        "cost_saved_cny_peak": round(native_agg["peak_cost_cny_mean"] - offload_agg["peak_cost_cny_mean"], 4),
        "cost_saved_percent": f"{(1 - offload_agg['peak_cost_cny_mean'] / native_agg['peak_cost_cny_mean']) * 100:.1f}%",
        "turns_reduced": round(native_agg["turns_mean"] - offload_agg["turns_mean"], 1),
    }

    final_report = {
        "task": "Mini-Redis Greenfield (/goal directive)",
        "trials_count": 3,
        "aggregates": {
            "native": native_agg,
            "offload": offload_agg,
            "delta": delta,
        }
    }

    MULTI_RESULT_FILE.write_text(json.dumps(final_report, indent=2), encoding="utf-8")
    print(f"\nSaved multi-run results to {MULTI_RESULT_FILE}")

    print("\n" + "="*85)
    print("FINAL N=3 AGGREGATE COMPARISON REPORT: Mini-Redis (/goal)")
    print("="*85)
    print(f"{'Metric':<30} | {'Native (Mean N=3)':<18} | {'Outboard (Mean N=3)':<18} | {'Delta':<18}")
    print("-" * 88)
    print(f"{'Wall-Clock Time (s)':<30} | {native_agg['wall_clock_seconds_mean']:<18} | {offload_agg['wall_clock_seconds_mean']:<18} | {delta['time_speedup_percent']}")
    print(f"{'Completion Tokens':<30} | {native_agg['completion_tokens_mean']:<18} | {offload_agg['completion_tokens_mean']:<18} | {delta['completion_tokens_saved_percent']} cut")
    print(f"{'Total Tokens':<30} | {native_agg['total_tokens_mean']:<18} | {offload_agg['total_tokens_mean']:<18} | {delta['tokens_saved_percent']} saved")
    print(f"{'Peak Cost (CNY)':<30} | CNY {native_agg['peak_cost_cny_mean']:<14} | CNY {offload_agg['peak_cost_cny_mean']:<14} | {delta['cost_saved_percent']} saved")
    print(f"{'Main Agent Turns':<30} | {native_agg['turns_mean']:<18} | {offload_agg['turns_mean']:<18} | -{delta['turns_reduced']} turns")
    print(f"{'Oracle Passed (out of 20)':<30} | {native_agg['oracle_passed_mean']:<18} | {offload_agg['oracle_passed_mean']:<18} | {native_agg['oracle_pass_rate_mean']} vs {offload_agg['oracle_pass_rate_mean']}")
    print("="*85)

if __name__ == "__main__":
    main()
