"""MCP Sampling Protocol Adapter for Trajectory Offload.
Enables SubAgents to request LLM completions through the host Harness (Antigravity,
Claude Code, Cursor) via the standard MCP 'sampling/createMessage' protocol.
Eliminates the need for separate external API keys or model configuration.
"""
import asyncio
from dataclasses import dataclass, field
import json
import re
import time
from typing import Any

from mcp.types import CreateMessageResult, SamplingMessage, TextContent, ModelPreferences

def has_sampling_capability(session) -> bool:
    """Check if the connected MCP client/host advertises sampling capability."""
    if session is None:
        return False
    client_params = getattr(session, "_client_params", None)
    if client_params and getattr(client_params, "capabilities", None):
        return getattr(client_params.capabilities, "sampling", None) is not None
    return False

def extract_json_payload(text: str) -> str:
    """Extract clean JSON string from raw model text (handles markdown fences)."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        return m.group(1).strip()
    s = text.find("{")
    e = text.rfind("}")
    if s != -1 and e != -1 and e > s:
        return text[s:e+1].strip()
    return text

@dataclass
class SamplingUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def model_dump(self):
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens
        }

@dataclass
class SamplingOutputItem:
    type: str = "message"
    name: str | None = None
    arguments: str | None = None
    call_id: str | None = None
    text: str | None = None

    def model_dump(self, exclude_none=True):
        d = {"type": self.type}
        if self.name is not None: d["name"] = self.name
        if self.arguments is not None: d["arguments"] = self.arguments
        if self.call_id is not None: d["call_id"] = self.call_id
        if self.text is not None: d["text"] = self.text
        return d

@dataclass
class SamplingResponse:
    id: str
    status: str = "completed"
    output: list = field(default_factory=list)
    output_text: str = ""
    usage: SamplingUsage = field(default_factory=SamplingUsage)
    model: str = "host-sampled"

class MCPSamplingResponsesGateway:
    """Implements client.responses.create interface expected by Run.request."""
    def __init__(self, client: "MCPSamplingClient"):
        self.client = client

    def create(self, **kwargs) -> SamplingResponse:
        return self.client.sample(**kwargs)

class MCPSamplingClient:
    """Adapter allowing SubAgent Run instances to sample from host via MCP sampling/createMessage."""
    def __init__(self, session, loop: asyncio.AbstractEventLoop | None = None):
        self.session = session
        self.loop = loop or asyncio.get_event_loop()
        self.responses = MCPSamplingResponsesGateway(self)

    def sample(self, model: str = "default", instructions: str = "",
               input: list | None = None, tools: list | None = None,
               max_output_tokens: int = 2500, timeout: float = 60.0,
               tool_choice: dict | None = None, **kwargs) -> SamplingResponse:
        
        force_tool = None
        if tool_choice and isinstance(tool_choice, dict):
            force_tool = tool_choice.get("name") or tool_choice.get("function", {}).get("name")

        # 1. Convert history to MCP SamplingMessages
        sampling_messages = []
        for item in (input or []):
            role = item.get("role", "user")
            content = item.get("content", "")
            
            # Format tool results or special payloads if present
            if isinstance(content, dict) or isinstance(content, list):
                content_str = json.dumps(content, ensure_ascii=False)
            else:
                content_str = str(content)

            if role in {"user", "assistant"}:
                sampling_messages.append(
                    SamplingMessage(
                        role=role,
                        content=TextContent(type="text", text=content_str)
                    )
                )
            elif role == "tool":
                sampling_messages.append(
                    SamplingMessage(
                        role="user",
                        content=TextContent(type="text", text=f"[Tool Output]: {content_str}")
                    )
                )

        if not sampling_messages:
            sampling_messages.append(
                SamplingMessage(role="user", content=TextContent(type="text", text="Execute task."))
            )

        # 2. Enrich system prompt with submission schema guidance if requested
        system_prompt = instructions
        if force_tool:
            matching_tool = next((t for t in (tools or []) if t.get("name") == force_tool or t.get("function", {}).get("name") == force_tool), None)
            if matching_tool:
                schema = matching_tool.get("parameters") or matching_tool.get("function", {}).get("parameters", {})
                system_prompt += (
                    f"\n\n[SUBMISSION MANDATE]:\nYou MUST submit your final response as valid JSON matching this schema for {force_tool}:\n"
                    f"{json.dumps(schema, ensure_ascii=False)}\nOutput pure JSON without markdown explanation."
                )

        # 3. Dispatch to host harness via MCP Sampling Protocol
        call_id = f"samp_{int(time.monotonic()*1000)}"
        coro = self.session.create_message(
            messages=sampling_messages,
            system_prompt=system_prompt,
            max_tokens=max_output_tokens,
            model_preferences=ModelPreferences(
                costPriority=0.2,
                speedPriority=0.8,
                intelligencePriority=0.8
            )
        )

        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        result: CreateMessageResult = future.result(timeout=timeout)

        # 4. Extract generated content
        if hasattr(result.content, "text"):
            raw_text = result.content.text
        elif isinstance(result.content, list) and len(result.content) > 0:
            raw_text = getattr(result.content[0], "text", str(result.content[0]))
        else:
            raw_text = str(result.content)

        # 5. Pack into SamplingResponse
        output_items = []
        if force_tool:
            clean_json = extract_json_payload(raw_text)
            output_items.append(
                SamplingOutputItem(
                    type="function_call",
                    name=force_tool,
                    arguments=clean_json,
                    call_id=f"call_{call_id}"
                )
            )
        else:
            output_items.append(SamplingOutputItem(type="message", text=raw_text))

        in_est = sum(len(m.content.text) for m in sampling_messages) // 4 + len(system_prompt) // 4
        out_est = len(raw_text) // 4

        return SamplingResponse(
            id=call_id,
            status="completed",
            output=output_items,
            output_text=raw_text,
            usage=SamplingUsage(
                input_tokens=in_est,
                output_tokens=out_est,
                total_tokens=in_est + out_est
            ),
            model=getattr(result, "model", "host-model")
        )
