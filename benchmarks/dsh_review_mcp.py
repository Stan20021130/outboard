"""One Investigator with the review policy fixed by the experiment host."""

import argparse
from pathlib import Path

from trajectory_offload.contracts import Settings
from trajectory_offload.execution import load_policy
from trajectory_offload.server import create_server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--checks", type=Path, required=True)
    parser.add_argument("--logs", type=Path, required=True)
    parser.add_argument("--scenario", choices=("impact",), required=True)
    parser.add_argument("--semantic-review", action="store_true")
    args = parser.parse_args()
    settings = Settings(model="deepseek-v4.1-flash", reasoning_effort="low", max_output_tokens=8000,
                        max_total_tokens=200000, max_seconds=150, log_dir=args.logs,
                        semantic_verification=args.semantic_review)
    server = create_server(args.repo, settings, policy=load_policy(args.checks))
    for name in ("verify_patch", "diagnose_failure", "cluster_test_failures",
                 "construct_regression_test", "augment_test_coverage"):
        try:
            server.remove_tool(name)
        except Exception:
            pass
    server.run(transport="stdio")
