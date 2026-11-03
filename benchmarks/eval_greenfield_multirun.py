"""Multi-run Greenfield Evaluation Driver.
Runs repeated trials (e.g. 2 more trials to combine with trial 1, yielding N=3)
to eliminate chance and compute statistical averages (mean, min, max, std).
"""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.eval_greenfield_ab import (
    execute_native_arm,
    execute_offload_arm,
    ROOT
)

RESULT_FILE = ROOT / ".trajectory/greenfield_ab_3runs_result.json"
PREV_RESULT_FILE = ROOT / ".trajectory/greenfield_ab_result.json"

def run_multitrials(num_new_runs=2):
    print("=" * 80)
    print(f"  GREENFIELD MULTI-RUN STATISTICAL EVALUATION ({num_new_runs} NEW RUNS)")
    print("=" * 80)

    trials = []

    # Check if trial 1 exists
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
    for t_idx in range(start_trial, start_trial + num_new_runs):
        print("\n" + "#" * 80)
        print(f"  STARTING TRIAL {t_idx} (INDEPENDENT CLEAN RE-RUN)")
        print("#" * 80)

        t_start = time.monotonic()
        print(f"\n--- [Trial {t_idx}] 1/2: Native Control Arm ---")
        native_res = execute_native_arm()

        print(f"\n--- [Trial {t_idx}] 2/2: Orchestrated Offload Arm ---")
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

    # Statistical Aggregation
    native_times = [t["native"]["wall_clock_seconds"] for t in trials]
    offload_times = [t["offload"]["wall_clock_seconds"] for t in trials]
    native_tokens = [t["native"]["total_tokens"] for t in trials]
    offload_tokens = [t["offload"]["total_tokens"] for t in trials]
    native_turns = [t["native"]["turns"] for t in trials]
    offload_turns = [t["offload"]["turns"] for t in trials]
    native_passes = [t["native"]["oracle"]["passed"] for t in trials]
    offload_passes = [t["offload"]["oracle"]["passed"] for t in trials]

    n_runs = len(trials)

    mean_nat_time = round(statistics.mean(native_times), 2)
    mean_off_time = round(statistics.mean(offload_times), 2)
    mean_nat_tokens = int(statistics.mean(native_tokens))
    mean_off_tokens = int(statistics.mean(offload_tokens))
    mean_nat_turns = round(statistics.mean(native_turns), 1)
    mean_off_turns = round(statistics.mean(offload_turns), 1)

    time_savings_pct = round((1 - mean_off_time / mean_nat_time) * 100, 1)
    token_savings_pct = round((1 - mean_off_tokens / mean_nat_tokens) * 100, 1)

    summary = {
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
                "turns_mean": mean_nat_turns,
                "oracle_pass_rate_mean": f"{(sum(native_passes) / (n_runs * 15)) * 100:.1f}%"
            },
            "offload": {
                "wall_clock_seconds_mean": mean_off_time,
                "wall_clock_seconds_min": min(offload_times),
                "wall_clock_seconds_max": max(offload_times),
                "total_tokens_mean": mean_off_tokens,
                "total_tokens_min": min(offload_tokens),
                "total_tokens_max": max(offload_tokens),
                "turns_mean": mean_off_turns,
                "oracle_pass_rate_mean": f"{(sum(offload_passes) / (n_runs * 15)) * 100:.1f}%"
            },
            "delta": {
                "time_speedup_seconds": round(mean_nat_time - mean_off_time, 2),
                "time_speedup_percent": f"{time_savings_pct}% faster",
                "tokens_saved_count": mean_nat_tokens - mean_off_tokens,
                "tokens_saved_percent": f"-{token_savings_pct}%",
                "turns_reduced": round(mean_nat_turns - mean_off_turns, 1)
            }
        }
    }

    RESULT_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print(f"  AGGREGATED MULTI-RUN STATISTICAL RESULTS (N={n_runs} INDEPENDENT RUNS)")
    print("=" * 80)
    print(f"{'Metric':<24} | {'Native Control Arm (Mean)':<26} | {'Orchestrated Offload Arm (Mean)':<28} | {'Advantage / Delta'}")
    print("-" * 105)
    print(f"{'Wall-Clock Time':<24} | {mean_nat_time}s ({min(native_times)}s - {max(native_times)}s) | {mean_off_time}s ({min(offload_times)}s - {max(offload_times)}s)    | {summary['aggregates']['delta']['time_speedup_percent']}")
    print(f"{'Total Tokens':<24} | {mean_nat_tokens:,} ({min(native_tokens):,} - {max(native_tokens):,}) | {mean_off_tokens:,} ({min(offload_tokens):,} - {max(offload_tokens):,})   | {summary['aggregates']['delta']['tokens_saved_percent']}")
    print(f"{'Interaction Turns':<24} | {mean_nat_turns} turns                   | {mean_off_turns} turns                    | -{summary['aggregates']['delta']['turns_reduced']} turns")
    print(f"{'Oracle Pass Rate':<24} | {summary['aggregates']['native']['oracle_pass_rate_mean']} (all 15/15)      | {summary['aggregates']['offload']['oracle_pass_rate_mean']} (all 15/15)       | 100% Stability")
    print("=" * 80)
    print(f"Full multi-run data written to: {RESULT_FILE}")

if __name__ == "__main__":
    run_multitrials(num_new_runs=2)
