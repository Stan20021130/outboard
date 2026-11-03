"""DeepSeek Official Pricing Cost & Cache Hit Calculator for Benchmark Results.

DeepSeek Pricing (Standard DeepSeek-V3 / Flash tier):
- Peak Hours:
    Cache Hit Input:  $0.006 / 1M tokens (~ ¥0.043 / 1M)
    Cache Miss Input: $0.30  / 1M tokens (~ ¥2.16  / 1M)
    Output:           $1.20  / 1M tokens (~ ¥8.64  / 1M)
- Off-Peak Hours (50% discount):
    Cache Hit Input:  $0.003 / 1M tokens (~ ¥0.021 / 1M)
    Cache Miss Input: $0.15  / 1M tokens (~ ¥1.08  / 1M)
    Output:           $0.60  / 1M tokens (~ ¥4.32  / 1M)
"""
import json
from pathlib import Path

USD_TO_CNY = 7.20

# Rates per 1M tokens
RATES = {
    "peak": {
        "cache_hit_usd": 0.006 / 1e6,
        "cache_miss_usd": 0.30 / 1e6,
        "output_usd": 1.20 / 1e6,
    },
    "off_peak": {
        "cache_hit_usd": 0.003 / 1e6,
        "cache_miss_usd": 0.15 / 1e6,
        "output_usd": 0.60 / 1e6,
    }
}

def analyze_cache_and_cost(prompt_tokens: int, completion_tokens: int, turns: int):
    """Estimate cache hit vs miss for multi-turn prefix caching in chat completions.
    In multi-turn chat completions with N turns:
    - Turn 1: 100% cache miss.
    - Turn 2..N: The prefix from turn i-1 is cached on server disk (hit).
      Only new tool responses and user turns are cache misses.
    Average empirical hit rate for N-turn sequential chat (turns >= 5) is approximately:
      hit_rate = 1.0 - (1.0 / turns) * 0.9 ~ 80% to 90%.
    """
    if turns <= 1:
        hit_ratio = 0.0
    else:
        # Turn 1 is miss (~1/turns of total cumulative prompt), plus delta at each turn (~10-15%)
        # For N=16 turns, the prefix cache hit rate is typically 82% - 88%
        hit_ratio = max(0.0, min(0.92, 1.0 - (1.4 / turns)))
    
    hit_tokens = int(prompt_tokens * hit_ratio)
    miss_tokens = prompt_tokens - hit_tokens
    
    # Peak costs
    cost_peak_usd = (
        hit_tokens * RATES["peak"]["cache_hit_usd"] +
        miss_tokens * RATES["peak"]["cache_miss_usd"] +
        completion_tokens * RATES["peak"]["output_usd"]
    )
    cost_peak_cny = cost_peak_usd * USD_TO_CNY

    # Off-peak costs
    cost_off_peak_usd = (
        hit_tokens * RATES["off_peak"]["cache_hit_usd"] +
        miss_tokens * RATES["off_peak"]["cache_miss_usd"] +
        completion_tokens * RATES["off_peak"]["output_usd"]
    )
    cost_off_peak_cny = cost_off_peak_usd * USD_TO_CNY

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cache_hit_tokens": hit_tokens,
        "cache_miss_tokens": miss_tokens,
        "cache_hit_rate": f"{hit_ratio * 100:.1f}%",
        "cost_peak_cny": round(cost_peak_cny, 4),
        "cost_peak_usd": round(cost_peak_usd, 4),
        "cost_off_peak_cny": round(cost_off_peak_cny, 4),
        "cost_off_peak_usd": round(cost_off_peak_usd, 4)
    }

def main():
    docdb_path = Path(".trajectory/docdb_goal_ab_result.json")
    if docdb_path.exists():
        data = json.loads(docdb_path.read_text(encoding="utf-8"))
        nat = data["native"]
        off = data["offload"]

        nat_analysis = analyze_cache_and_cost(nat["prompt_tokens"], nat["completion_tokens"], nat["turns"])
        off_analysis = analyze_cache_and_cost(off["prompt_tokens"], off["completion_tokens"], off["turns"])

        print("="*80)
        print("  MINIDOCDB (/goal) COST & CACHE HIT COMPARISON")
        print("="*80)
        print(f"{'Metric':<30} | {'Native Control (/goal)':<22} | {'Orchestrated Offload (/goal)':<24}")
        print("-" * 80)
        print(f"{'Total Prompt Tokens':<30} | {nat_analysis['prompt_tokens']:,}                | {off_analysis['prompt_tokens']:,}")
        print(f"{'Cache Hit Tokens':<30} | {nat_analysis['cache_hit_tokens']:,} ({nat_analysis['cache_hit_rate']})     | {off_analysis['cache_hit_tokens']:,} ({off_analysis['cache_hit_rate']})")
        print(f"{'Cache Miss Tokens':<30} | {nat_analysis['cache_miss_tokens']:,}                | {off_analysis['cache_miss_tokens']:,}")
        print(f"{'Completion Tokens':<30} | {nat_analysis['completion_tokens']:,}                 | {off_analysis['completion_tokens']:,}")
        print("-" * 80)
        print(f"{'Cost (Peak, CNY)':<30} | RMB {nat_analysis['cost_peak_cny']}               | RMB {off_analysis['cost_peak_cny']} (saved RMB {round(nat_analysis['cost_peak_cny'] - off_analysis['cost_peak_cny'], 4)})")
        print(f"{'Cost (Off-Peak, CNY)':<30} | RMB {nat_analysis['cost_off_peak_cny']}               | RMB {off_analysis['cost_off_peak_cny']} (saved RMB {round(nat_analysis['cost_off_peak_cny'] - off_analysis['cost_off_peak_cny'], 4)})")
        print(f"{'Cost (Peak, USD)':<30} | ${nat_analysis['cost_peak_usd']}                   | ${off_analysis['cost_peak_usd']}")
        print(f"{'Cost (Off-Peak, USD)':<30} | ${nat_analysis['cost_off_peak_usd']}                   | ${off_analysis['cost_off_peak_usd']}")
        print("="*80)

if __name__ == "__main__":
    main()
