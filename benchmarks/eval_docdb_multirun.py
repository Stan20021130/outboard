"""Multi-run Greenfield Evaluation Driver for MiniDocDB under /goal directive.
Runs 3 repeated trials to eliminate chance, measure variance on medium-scale system,
and compute statistical aggregates (Time, Tokens, Cache Hit Rate, DeepSeek Official Cost).
"""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.eval_docdb_goal_ab import (
    execute_native_arm,
    execute_offload_arm,
    ROOT
)
from benchmarks.calculate_costs import analyze_cache_and_cost

RESULT_FILE = ROOT / ".trajectory/docdb_3runs_result.json"
PREV_RESULT_FILE = ROOT / ".trajectory/docdb_goal_ab_result.json"

def run_multitrials(total_runs=3):
    print("=" * 85)
    print(f"  MINIDOCDB (/goal): MULTI-RUN STATISTICAL EVALUATION ({total_runs} RUNS)")
    print("=" * 85)

    trials = []

    # Attempt to load Trial 1 if available
    if PREV_RESULT_FILE.exists():
        try:
            prev_data = json.loads(PREV_RESULT_FILE.read_text(encoding="utf-8"))
            if "native" in prev_data and "offload" in prev_data:
                trials.append({
                    "trial_id": 1,
                    "native": prev_data["native"],
                    "offload": prev_data["offload"]
                })
                print(f"[Loaded Trial 1] Native: {prev_data['native']['wall_clock_seconds']}s / {prev_data['native']['total_tokens']} tok | Offload: {prev_data['offload']['wall_clock_seconds']}s / {prev_data['offload']['total_tokens']} tok")
        except Exception as e:
            print(f"Could not load previous trial: {e}")

    start_trial = len(trials) + 1
    for t_idx in range(start_trial, total_runs + 1):
        print("\n" + "#" * 85)
        print(f"  STARTING TRIAL {t_idx} (INDEPENDENT CLEAN RE-RUN FROM SCRATCH UNDER /goal)")
        print("#" * 85)

        t_start = time.monotonic()
        print(f"\n--- [Trial {t_idx}] 1/2: Native Control Arm (/goal) ---")
        native_res = execute_native_arm()

        print(f"\n--- [Trial {t_idx}] 2/2: Orchestrated Offload Arm (/goal) ---")
        offload_res = execute_offload_arm()

        trial_dur = round(time.monotonic() - t_start, 2)
        trial_record = {
            "trial_id": t_idx,
            "trial_duration_seconds": trial_dur,
            "native": native_res,
            "offload": offload_res
        }
        trials.append(trial_record)

        print(f"\n>>> Trial {t_idx} Completed in {trial_dur}s:")
        print(f"    Native : {native_res['wall_clock_seconds']}s | {native_res['total_tokens']:,} tokens | {native_res['turns']} turns | Oracle: {native_res['oracle']['passed']}/{native_res['oracle']['total']}")
        print(f"    Offload: {offload_res['wall_clock_seconds']}s | {offload_res['total_tokens']:,} tokens | {offload_res['turns']} turns | Oracle: {offload_res['oracle']['passed']}/{offload_res['oracle']['total']}")

    n_runs = len(trials)
    native_times = [t["native"]["wall_clock_seconds"] for t in trials]
    offload_times = [t["offload"]["wall_clock_seconds"] for t in trials]
    native_tokens = [t["native"]["total_tokens"] for t in trials]
    offload_tokens = [t["offload"]["total_tokens"] for t in trials]
    native_prompts = [t["native"]["prompt_tokens"] for t in trials]
    offload_prompts = [t["offload"]["prompt_tokens"] for t in trials]
    native_completions = [t["native"]["completion_tokens"] for t in trials]
    offload_completions = [t["offload"]["completion_tokens"] for t in trials]
    native_turns = [t["native"]["turns"] for t in trials]
    offload_turns = [t["offload"]["turns"] for t in trials]
    native_passes = [t["native"]["oracle"]["passed"] for t in trials]
    offload_passes = [t["offload"]["oracle"]["passed"] for t in trials]

    mean_nat_time = round(statistics.mean(native_times), 2)
    mean_off_time = round(statistics.mean(offload_times), 2)
    mean_nat_tokens = int(statistics.mean(native_tokens))
    mean_off_tokens = int(statistics.mean(offload_tokens))
    mean_nat_prompts = int(statistics.mean(native_prompts))
    mean_off_prompts = int(statistics.mean(offload_prompts))
    mean_nat_completions = int(statistics.mean(native_completions))
    mean_off_completions = int(statistics.mean(offload_completions))
    mean_nat_turns = round(statistics.mean(native_turns), 1)
    mean_off_turns = round(statistics.mean(offload_turns), 1)

    time_delta = round(mean_nat_time - mean_off_time, 2)
    time_pct = round((time_delta / mean_nat_time) * 100, 1)
    token_delta = mean_nat_tokens - mean_off_tokens
    token_pct = round((token_delta / mean_nat_tokens) * 100, 1)

    # Calculate Cache Hit and Cost aggregates
    nat_cost = analyze_cache_and_cost(mean_nat_prompts, mean_nat_completions, int(mean_nat_turns))
    off_cost = analyze_cache_and_cost(mean_off_prompts, mean_off_completions, int(mean_off_turns))

    cost_saved_cny = round(nat_cost["cost_peak_cny"] - off_cost["cost_peak_cny"], 4)
    cost_saved_pct = round((cost_saved_cny / nat_cost["cost_peak_cny"]) * 100, 1)

    summary = {
        "task": "MiniDocDB (/goal directive)",
        "trials_count": n_runs,
        "trials": trials,
        "aggregates": {
            "native": {
                "wall_clock_seconds_mean": mean_nat_time,
                "wall_clock_seconds_min": min(native_times),
                "wall_clock_seconds_max": max(native_times),
                "total_tokens_mean": mean_nat_tokens,
                "total_tokens_min": min(native_tokens),
                "total_tokens_max": max(native_tokens),
                "prompt_tokens_mean": mean_nat_prompts,
                "completion_tokens_mean": mean_nat_completions,
                "cache_hit_rate": nat_cost["cache_hit_rate"],
                "cost_peak_cny": nat_cost["cost_peak_cny"],
                "cost_off_peak_cny": nat_cost["cost_off_peak_cny"],
                "cost_peak_usd": nat_cost["cost_peak_usd"],
                "turns_mean": mean_nat_turns,
                "oracle_pass_rate_mean": f"{(sum(native_passes) / (n_runs * 20)) * 100:.1f}%"
            },
            "offload": {
                "wall_clock_seconds_mean": mean_off_time,
                "wall_clock_seconds_min": min(offload_times),
                "wall_clock_seconds_max": max(offload_times),
                "total_tokens_mean": mean_off_tokens,
                "total_tokens_min": min(offload_tokens),
                "total_tokens_max": max(offload_tokens),
                "prompt_tokens_mean": mean_off_prompts,
                "completion_tokens_mean": mean_off_completions,
                "cache_hit_rate": off_cost["cache_hit_rate"],
                "cost_peak_cny": off_cost["cost_peak_cny"],
                "cost_off_peak_cny": off_cost["cost_off_peak_cny"],
                "cost_peak_usd": off_cost["cost_peak_usd"],
                "turns_mean": mean_off_turns,
                "oracle_pass_rate_mean": f"{(sum(offload_passes) / (n_runs * 20)) * 100:.1f}%"
            },
            "delta": {
                "time_speedup_seconds": time_delta,
                "time_speedup_percent": f"{time_pct}%" if time_delta > 0 else f"{time_pct}% (slower)",
                "tokens_saved_count": token_delta,
                "tokens_saved_percent": f"{token_pct}%" if token_delta > 0 else f"{token_pct}% (more tokens)",
                "cost_saved_cny_peak": cost_saved_cny,
                "cost_saved_cny_percent": f"{cost_saved_pct}%",
                "turns_reduced": round(mean_nat_turns - mean_off_turns, 1)
            }
        }
    }

    RESULT_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 95)
    print(f"  AGGREGATED STATISTICAL RESULTS FOR MINIDOCDB (/goal) (N={n_runs} RUNS)")
    print("=" * 95)
    print(f"{'Metric':<25} | {'Native Control Arm (Mean)':<28} | {'Orchestrated Offload Arm (Mean)':<30} | {'Advantage / Delta'}")
    print("-" * 115)
    print(f"{'Wall-Clock Time':<25} | {mean_nat_time}s ({min(native_times)}s - {max(native_times)}s)   | {mean_off_time}s ({min(offload_times)}s - {max(offload_times)}s)     | {summary['aggregates']['delta']['time_speedup_percent']}")
    print(f"{'Total Tokens':<25} | {mean_nat_tokens:,} ({min(native_tokens):,} - {max(native_tokens):,})  | {mean_off_tokens:,} ({min(offload_tokens):,} - {max(offload_tokens):,})    | {summary['aggregates']['delta']['tokens_saved_percent']}")
    print(f"{'Cache Hit Rate':<25} | {nat_cost['cache_hit_rate']}                    | {off_cost['cache_hit_rate']}                     | Prefix Caching Active")
    print(f"{'Cost (Peak, CNY)':<25} | RMB {nat_cost['cost_peak_cny']}                   | RMB {off_cost['cost_peak_cny']}                    | Saved {cost_saved_pct}% (RMB {cost_saved_cny})")
    print(f"{'Oracle Pass Rate':<25} | {summary['aggregates']['native']['oracle_pass_rate_mean']} (all 20-test)        | {summary['aggregates']['offload']['oracle_pass_rate_mean']} (all 20-test)         | Quality Conformance")
    print("=" * 95)
    print(f"Full multi-run data written to: {RESULT_FILE}")

if __name__ == "__main__":
    run_multitrials(total_runs=3)
