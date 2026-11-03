"""Stand-alone MCP Server launcher for Outboard.
Runs strictly over stdio with client=None (ZERO external API keys configured).
All LLM generation is deferred to the host Harness via MCP Sampling Protocol (Plan A).
"""
import os
import sys
from pathlib import Path
import asyncio

# Tier 2 Fallback: if client lacks MCP sampling capability, inherit or default to local gateway
os.environ.setdefault("OPENAI_BASE_URL", "http://127.0.0.1:8788/v1")
os.environ.setdefault("OPENAI_API_KEY", "local-dev")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trajectory_offload.contracts import Settings, CheckSpec, ExecutionPolicy
from trajectory_offload.server import create_server

# Explicit zero-api-key settings
settings = Settings(
    model="host-sampled-model",
    reasoning_effort="low",
    max_output_tokens=2500,
    max_total_tokens=100000,
    max_seconds=90,
    prefetch_context=True
)

check = CheckSpec(
    id="unit_regression",
    argv=[sys.executable, "-m", "pytest", "-q", "{test_file}", "{junit_option}"],
    timeout_seconds=30
)
policy = ExecutionPolicy(checks=[check])

# client=None: No OpenAI client, no API key inside CTOS!
server = create_server(root=ROOT, settings=settings, mode="specialized", client=None, policy=policy)

if __name__ == "__main__":
    asyncio.run(server.run_stdio_async())
