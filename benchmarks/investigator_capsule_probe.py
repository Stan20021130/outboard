"""Replay an unchanged task capsule extracted from the disclosed DSH development study.

Set PYTHONPATH to the frozen v0.2 package to measure the old executor. These
are development probes, not a substitute for native-main end-to-end trials.
"""

import argparse
import json
from pathlib import Path

from trajectory_offload.contracts import InvestigationTask, Settings
from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT, investigate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("v2", "v3"), required=True)
    parser.add_argument("--case", type=int, choices=(0, 1), required=True)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    base = project / ".trajectory/investigator-optimization"
    if args.label and not args.label.replace("-", "").isalnum():
        raise ValueError("label must contain letters, numbers or hyphens")
    output = base / "probes" / f"{args.label or args.variant}-case{args.case}"
    if output.exists():
        raise ValueError("preserve existing probes; choose an explicitly new study directory")
    source = project / f".trajectory/dsh-study/runs/env-prefix-impact-r{args.case}-offload/subagent"
    records = [json.loads(line) for path in sorted(source.glob("*.jsonl"))
               for line in path.read_text(encoding="utf-8").splitlines()]
    capsule = next(row["capsule"] for row in records if row["event"] == "capsule")
    task = InvestigationTask(**{key: capsule[key] for key in
        ("objective", "task_type", "scope", "known_evidence", "acceptance_criteria")}, **capsule["change_inputs"])
    settings = Settings(model="deepseek-v4.1-flash", reasoning_effort="low", max_output_tokens=8000,
                        max_total_tokens=200000, max_seconds=150, log_dir=output / "logs")
    output.mkdir(parents=True)
    (output / "input.json").write_text(task.model_dump_json(indent=2), encoding="utf-8")
    result = investigate(project / ".trajectory/dsh-study/cases/env-prefix-impact", task, settings)
    result["implementation_fingerprint"] = IMPLEMENTATION_FINGERPRINT
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"variant": args.variant, "case": args.case, "status": result["status"],
                      "metrics": result["metrics"], "errors": result["verification"]["errors"],
                      "unresolved": result["unresolved_questions"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
