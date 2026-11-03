import json
from pathlib import Path

files = [
    ("MiniCacheKV", Path(".trajectory/greenfield_ab_3runs_result.json")),
    ("MiniPipeline", Path(".trajectory/pipeline_ab_result.json")),
    ("MiniTransactionalKV", Path(".trajectory/transaction_ab_3runs_result.json"))
]

for name, f in files:
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        print(f"\n==================== {name} ====================")
        trials = data.get("trials", [data])
        for i, t in enumerate(trials):
            t_id = t.get("trial_id", i + 1)
            nat = t["native"]
            off = t["offload"]
            print(f"--- {name} Trial {t_id} ---")
            print(f"  Native : {nat['wall_clock_seconds']}s | {nat['total_tokens']:,} tokens | {nat['turns']} turns | Oracle: {nat['oracle']['passed']}/{nat['oracle']['total']}")
            print(f"  Offload: {off['wall_clock_seconds']}s | {off['total_tokens']:,} tokens | {off['turns']} turns | Oracle: {off['oracle']['passed']}/{off['oracle']['total']}")
