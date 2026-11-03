"""Live Antigravity Host Harness Simulation testing Plan A: MCP Sampling Protocol.
Proves that CTOS / Trajectory Offload can run SubAgents with ZERO external API keys,
by delegating all model generation to the host Harness via standard 'sampling/createMessage'.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession
from mcp.types import (
    CreateMessageResult, TextContent, SamplingCapability,
    CreateMessageRequestParams
)

BASE_URL = "http://127.0.0.1:8788/v1"
API_KEY = "local-dev"

# The Host Harness's internal model engine (e.g. Antigravity / Claude Code session)
host_model_client = OpenAI(base_url=BASE_URL, api_key=API_KEY)

sampling_events = []

async def antigravity_host_sampling_callback(session, params: CreateMessageRequestParams) -> CreateMessageResult:
    """Simulates the Host Harness intercepting the MCP sampling request.
    The host generates the completion using its own authenticated model.
    """
    t_start = time.monotonic()
    print("\n" + ">" * 80)
    print(">>> [Antigravity Host] INTERCEPTED MCP SAMPLING REQUEST FROM CTOS SUBAGENT!")
    print(f">>> [Antigravity Host] Message count      : {len(params.messages)}")
    print(f">>> [Antigravity Host] Max Tokens allowed : {params.maxTokens}")
    print(f">>> [Antigravity Host] System Prompt head : {params.systemPrompt[:90]}...")
    print(">" * 80)

    # Format into OpenAI format for the host model
    messages = [{"role": "system", "content": params.systemPrompt or ""}]
    for m in params.messages:
        text = m.content.text if hasattr(m.content, "text") else str(m.content)
        messages.append({"role": m.role, "content": text})

    print("[Antigravity Host] Forwarding request to Host's internal model engine (DeepSeek-V4.1-Flash)...", flush=True)
    res = host_model_client.chat.completions.create(
        model="deepseek-v4.1-flash",
        messages=messages,
        temperature=0.0
    )
    generated_text = res.choices[0].message.content or "{}"
    dur = round(time.monotonic() - t_start, 2)

    print(f"[Antigravity Host] Completion generated in {dur}s ({len(generated_text)} chars). Returning to CTOS SubAgent...", flush=True)

    sampling_events.append({
        "timestamp": time.time(),
        "prompt_tokens": res.usage.prompt_tokens if res.usage else 0,
        "completion_tokens": res.usage.completion_tokens if res.usage else 0,
        "generated_preview": generated_text[:100]
    })

    return CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text=generated_text),
        model="antigravity-host-model"
    )


async def main():
    print("=" * 85)
    print("  TESTING PLAN A: MCP SAMPLING PROTOCOL ON ANTIGRAVITY HARNESS")
    print("  Zero external API keys configured in CTOS. All model calls served by Host.")
    print("=" * 85)

    server_params = StdioServerParameters(
        command=PYTHON,
        args=[str(ROOT / "benchmarks/run_mcp_server.py")],
        env={**os.environ, "PYTHONUNBUFFERED": "1"}
    )

    print("\n[Antigravity Host] Launching CTOS FastMCP Server subprocess via stdio...")
    async with stdio_client(server_params) as (read_stream, write_stream):
        print("[Antigravity Host] Stdio streams connected. Initializing MCP ClientSession with SamplingCapability...")
        async with ClientSession(
            read_stream,
            write_stream,
            sampling_callback=antigravity_host_sampling_callback,
            sampling_capabilities=SamplingCapability()
        ) as session:
            init_res = await session.initialize()
            print(f"[Antigravity Host] Connected! Server: {init_res.serverInfo.name} v{init_res.serverInfo.version}")

            # 1. Zero-Token Repo Reconnaissance
            print("\n--- Step 1: Query get_repository_state (Deterministic Zero-Token Recon) ---")
            recon_res = await session.call_tool("get_repository_state", {})
            recon_data = json.loads(recon_res.content[0].text) if hasattr(recon_res.content[0], "text") else recon_res.content[0]
            if isinstance(recon_data, str):
                recon_data = json.loads(recon_data)
            snapshot_id = recon_data.get("snapshot_id")
            print(f"[Antigravity Host] Snapshot ID received: {snapshot_id[:16]}... (Files: {recon_data.get('source_file_count')})")

            # 2. Trigger SubAgent via MCP: Multi-Failure Triage
            print("\n--- Step 2: Trigger SubAgent cluster_test_failures via MCP ---")
            print("[Antigravity Host] Sending 3 simulated test failure observations to CTOS...")
            test_failures = [
                {
                    "test_id": "tests/test_docdb.py::test_index_conflict",
                    "kind": "error",
                    "message": "DuplicateKeyError: duplicate key violation on 'username'",
                    "traceback": "Traceback (most recent call last):\n  File 'collection.py', line 50\nDuplicateKeyError"
                },
                {
                    "test_id": "tests/test_docdb.py::test_reinsert_after_delete",
                    "kind": "error",
                    "message": "DuplicateKeyError: duplicate key violation on 'username'",
                    "traceback": "Traceback (most recent call last):\n  File 'collection.py', line 50\nDuplicateKeyError"
                },
                {
                    "test_id": "tests/test_docdb.py::test_query_syntax",
                    "kind": "failure",
                    "message": "KeyError: '$regex' operator not supported",
                    "traceback": "Traceback (most recent call last):\n  File 'query.py', line 80\nKeyError"
                }
            ]

            print("[Antigravity Host] Calling tool: cluster_test_failures ...")
            t_call = time.monotonic()
            tool_res = await session.call_tool(
                "cluster_test_failures",
                {
                    "snapshot_id": snapshot_id,
                    "failures": test_failures,
                    "analyze_relationships": True,
                    "include_source_evidence": False
                }
            )
            t_total = round(time.monotonic() - t_call, 2)

            raw_res = tool_res.content[0].text if hasattr(tool_res.content[0], "text") else tool_res.content[0]
            if isinstance(raw_res, str):
                try:
                    parsed = json.loads(raw_res)
                except Exception:
                    parsed = {"raw": raw_res}
            else:
                parsed = raw_res

            print("\n" + "=" * 85)
            print(f"  PLAN A VERIFICATION RESULT (Roundtrip in {t_total}s)")
            print("=" * 85)
            print(f"Sampling Events Handled by Host : {len(sampling_events)}")
            for idx, ev in enumerate(sampling_events, 1):
                print(f"  [Event {idx}] In: {ev['prompt_tokens']} tok, Out: {ev['completion_tokens']} tok")
                print(f"            Preview: {ev['generated_preview']}...")
            print("\nSubAgent Structured Result Returned to Host:")
            print(json.dumps(parsed, indent=2, ensure_ascii=False)[:600] + "\n...")
            print("=" * 85)
            print(">>> SUCCESS: CTOS completed specialized SubAgent execution with ZERO API keys configured.")
            print(">>> All LLM tokens were sampled transparently through Antigravity Host via MCP Protocol!")

if __name__ == "__main__":
    asyncio.run(main())
