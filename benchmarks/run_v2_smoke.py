"""Real MCP + model transport smoke for impact, patch verification and diagnosis."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from trajectory_offload.runtime import IMPLEMENTATION_FINGERPRINT


async def run(args):
    project = Path(__file__).resolve().parent.parent
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "summary.json").exists():
        raise ValueError("output already contains a run; choose another output directory")
    environment = {**os.environ, "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8788/v1"),
                   "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY", "local-dev")}
    params = StdioServerParameters(command=sys.executable, args=["-m", "trajectory_offload", "serve",
        "--repo", str(project / "benchmarks/fixtures"), "--checks", str(project / "benchmarks/checks.json"),
        "--model", args.model, "--reasoning", "low", "--max-total-tokens", "200000", "--max-seconds", "180",
        "--max-output-tokens", str(args.max_output_tokens),
        "--log-dir", str(output / "runs")], env=environment)
    results = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            print(json.dumps({"tools": [tool.name for tool in listing.tools]}), flush=True)

            async def call(name, arguments):
                reply = await asyncio.wait_for(session.call_tool(name, arguments), timeout=210)
                if reply.isError or reply.structuredContent is None:
                    raise RuntimeError(f"MCP {name} failed: {reply.content}")
                value = reply.structuredContent
                (output / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps({"tool": name, "status": value.get("status"), "verdict": value.get("verdict"),
                                  "model_calls": value.get("metrics", {}).get("model_calls"),
                                  "process_calls": value.get("metrics", {}).get("process_calls")}), flush=True)
                return value

            state = await call("get_repository_state", {})
            if "impact" in args.scenarios:
                results["impact"] = await call("investigate_repository",
                    json.loads((project / "benchmarks/impact-task.json").read_text(encoding="utf-8")))
            if "verification" in args.scenarios:
                results["verification"] = await call("verify_patch", {
                "objective": "Verify that the patch propagates loaded timeout settings while retaining request overrides and defaults.",
                "snapshot_id": state["snapshot_id"], "patch": (project / "benchmarks/timeout-fix.patch").read_text(),
                "check_ids": ["timeout"]})
            if "diagnosis" in args.scenarios:
                results["diagnosis"] = await call("diagnose_failure", {
                "objective": "Explain the observed timeout discrepancy by locating the failing assertion, environment input, "
                             "settings load and timeout selection. Separate observed facts from untested causal hypotheses.",
                "snapshot_id": state["snapshot_id"], "check_ids": ["timeout"],
                "failure_signature": "30 != 7", "repetitions": 2})
            after = await call("get_repository_state", {})
    summary = {"date_utc": datetime.now(timezone.utc).isoformat(), "model": args.model, "reasoning_effort": "low",
               "implementation_fingerprint": IMPLEMENTATION_FINGERPRINT, "snapshot_id": state["snapshot_id"],
               "max_output_tokens": args.max_output_tokens, "max_total_tokens": 200000,
               "original_unchanged": after["snapshot_id"] == state["snapshot_id"],
               "results": {name: {"status": value["status"], "verdict": value.get("verdict"),
                          "metrics": value["metrics"], "verification": value["verification"]} for name, value in results.items()},
               "note": "Engineering smoke on development fixtures, not evidence of efficiency or quality noninferiority."}
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["original_unchanged"] and all(value["status"] == "completed" for value in results.values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    parser.add_argument("--scenarios", nargs="+", choices=("impact", "verification", "diagnosis"),
                        default=["impact", "verification", "diagnosis"])
    raise SystemExit(asyncio.run(run(parser.parse_args())))
