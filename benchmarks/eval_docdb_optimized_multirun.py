"""Multi-run Driver for Optimized Offload Arm on MiniDocDB.
Runs 2 additional clean trials (Trial 2 and Trial 3) to achieve N=3 statistical comparability
against Native Control Arm (N=3) and Standard Offload Arm (N=3).
Saves all 3 trials and generates the 3-arm comparative analysis.
"""
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from benchmarks.eval_docdb_optimized import execute_optimized_offload_arm
from benchmarks.calculate_costs import analyze_cache_and_cost

TRIAL1_FILE = ROOT / ".trajectory/docdb_optimized_result.json"
BASE_3RUNS_FILE = ROOT / ".trajectory/docdb_3runs_result.json"
OPT_3RUNS_FILE = ROOT / ".trajectory/docdb_optimized_3runs_result.json"

def main(total_runs=3):
    print("=" * 85)
    print(f"  OPTIMIZED OFFLOAD ARM: MULTI-RUN STATISTICAL EVALUATION ({total_runs} RUNS)")
    print("=" * 85)

    trials = []

    # 1. Load Trial 1 if available
    if TRIAL1_FILE.exists():
        try:
            t1_data = json.loads(TRIAL1_FILE.read_text(encoding="utf-8"))
            trials.append({
                "trial_id": 1,
                "data": t1_data
            })
            print(f"[Loaded Trial 1] Time: {t1_data['wall_clock_seconds']}s | Total Tok: {t1_data['total_tokens']:,} | Out Tok: {t1_data['completion_tokens']:,} | Oracle: {t1_data['oracle']['passed']}/{t1_data['oracle']['total']}")
        except Exception as e:
            print(f"Warning: Could not load Trial 1: {e}")

    start_trial = len(trials) + 1

    # 2. Run remaining trials (Trial 2, Trial 3)
    for t_idx in range(start_trial, total_runs + 1):
        print("\n" + "#" * 85)
        print(f"  STARTING OPTIMIZED OFFLOAD TRIAL {t_idx} OF {total_runs} (CLEAN SCRATCH RUN)")
        print("#" * 85)

        t_start = time.monotonic()
        res = execute_optimized_offload_arm()
        t_dur = round(time.monotonic() - t_start, 2)

        trials.append({
            "trial_id": t_idx,
            "data": res
        })

        print(f"\n>>> Trial {t_idx} Completed in {t_dur}s:")
        print(f"    Time          : {res['wall_clock_seconds']}s")
        print(f"    Total Tokens  : {res['total_tokens']:,} (Prompt: {res['prompt_tokens']:,}, Completion: {res['completion_tokens']:,})")
        print(f"    Turns         : {res['turns']}")
        print(f"    Oracle Passed : {res['oracle']['passed']} / {res['oracle']['total']} ({res['oracle']['pass_rate']})")
        print(f"    Peak Cost     : RMB {res['cost_analysis']['cost_peak_cny']}")

    # 3. Statistical Aggregations for Optimized Arm
    times = [t["data"]["wall_clock_seconds"] for t in trials]
    totals = [t["data"]["total_tokens"] for t in trials]
    prompts = [t["data"]["prompt_tokens"] for t in trials]
    completions = [t["data"]["completion_tokens"] for t in trials]
    turns_list = [t["data"]["turns"] for t in trials]
    passes = [t["data"]["oracle"]["passed"] for t in trials]
    peak_costs = [t["data"]["cost_analysis"]["cost_peak_cny"] for t in trials]
    offpeak_costs = [t["data"]["cost_analysis"]["cost_off_peak_cny"] for t in trials]

    mean_time = round(statistics.mean(times), 2)
    mean_total = int(statistics.mean(totals))
    mean_prompt = int(statistics.mean(prompts))
    mean_completion = int(statistics.mean(completions))
    mean_turns = round(statistics.mean(turns_list), 1)
    mean_pass = round(statistics.mean(passes), 1)
    mean_peak_cost = round(statistics.mean(peak_costs), 4)
    mean_offpeak_cost = round(statistics.mean(offpeak_costs), 4)

    summary_opt = {
        "trials_count": len(trials),
        "mean_wall_clock_seconds": mean_time,
        "mean_total_tokens": mean_total,
        "mean_prompt_tokens": mean_prompt,
        "mean_completion_tokens": mean_completion,
        "mean_turns": mean_turns,
        "mean_oracle_passed": mean_pass,
        "mean_peak_cost_cny": mean_peak_cost,
        "mean_offpeak_cost_cny": mean_offpeak_cost,
        "trials": trials
    }

    OPT_3RUNS_FILE.write_text(json.dumps(summary_opt, indent=2), encoding="utf-8")
    print(f"\n[Saved] All {len(trials)} trials written to: {OPT_3RUNS_FILE}")

    # 4. Compare with Baseline 3-run Native & Standard Offload
    if BASE_3RUNS_FILE.exists():
        base_data = json.loads(BASE_3RUNS_FILE.read_text(encoding="utf-8"))
        nat_trials = [t["native"] for t in base_data.get("trials", [])]
        std_trials = [t["offload"] for t in base_data.get("trials", [])]

        mean_nat_time = round(statistics.mean([t["wall_clock_seconds"] for t in nat_trials]), 2)
        mean_nat_total = int(statistics.mean([t["total_tokens"] for t in nat_trials]))
        mean_nat_prompt = int(statistics.mean([t["prompt_tokens"] for t in nat_trials]))
        mean_nat_completion = int(statistics.mean([t["completion_tokens"] for t in nat_trials]))
        mean_nat_turns = round(statistics.mean([t["turns"] for t in nat_trials]), 1)
        mean_nat_pass = round(statistics.mean([t["oracle"]["passed"] for t in nat_trials]), 1)

        mean_std_time = round(statistics.mean([t["wall_clock_seconds"] for t in std_trials]), 2)
        mean_std_total = int(statistics.mean([t["total_tokens"] for t in std_trials]))
        mean_std_prompt = int(statistics.mean([t["prompt_tokens"] for t in std_trials]))
        mean_std_completion = int(statistics.mean([t["completion_tokens"] for t in std_trials]))
        mean_std_turns = round(statistics.mean([t["turns"] for t in std_trials]), 1)
        mean_std_pass = round(statistics.mean([t["oracle"]["passed"] for t in std_trials]), 1)

        nat_cost = analyze_cache_and_cost(mean_nat_prompt, mean_nat_completion, int(mean_nat_turns))
        std_cost = analyze_cache_and_cost(mean_std_prompt, mean_std_completion, int(mean_std_turns))

        print("\n" + "=" * 90)
        print("  MINIDOCDB: COMPLETE 3-ARM STATISTICAL COMPARISON (N=3 PER ARM)")
        print("=" * 90)
        header = f"{'Metric':<26} | {'Native Control (N=3)':<20} | {'Standard Offload (N=3)':<22} | {'Optimized Offload (N=3)':<22}"
        print(header)
        print("-" * len(header))
        print(f"{'Mean Wall-Clock Time':<26} | {mean_nat_time:<20} | {mean_std_time:<22} | {mean_time:<22}")
        print(f"{'Mean Output Tokens':<26} | {mean_nat_completion:<20,d} | {mean_std_completion:<22,d} | {mean_completion:<22,d}")
        print(f"{'Mean Input Tokens':<26} | {mean_nat_prompt:<20,d} | {mean_std_prompt:<22,d} | {mean_prompt:<22,d}")
        print(f"{'Mean Total Tokens':<26} | {mean_nat_total:<20,d} | {mean_std_total:<22,d} | {mean_total:<22,d}")
        print(f"{'Mean Interaction Turns':<26} | {mean_nat_turns:<20} | {mean_std_turns:<22} | {mean_turns:<22}")
        print(f"{'Mean Oracle Passed':<26} | {mean_nat_pass}/20 ({mean_nat_pass/20*100:.1f}%)    | {mean_std_pass}/20 ({mean_std_pass/20*100:.1f}%)      | {mean_pass}/20 ({mean_pass/20*100:.1f}%)")
        print(f"{'Mean Peak Cost (RMB)':<26} | RMB {nat_cost['cost_peak_cny']:<16.4f} | RMB {std_cost['cost_peak_cny']:<18.4f} | RMB {mean_peak_cost:<18.4f}")
        print(f"{'Mean Off-Peak Cost (RMB)':<26} | RMB {nat_cost['cost_off_peak_cny']:<16.4f} | RMB {std_cost['cost_off_peak_cny']:<18.4f} | RMB {mean_offpeak_cost:<18.4f}")
        print("=" * 90)

if __name__ == "__main__":
    main()
