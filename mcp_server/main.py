"""
mcp-server/main.py
─────────────────────────────────────────────────────────────────────────────
MCP Tool Gateway — FastAPI server that dispatches tool calls to the right
tool module based on the name declared in registry.yaml.

HOW DISPATCHING WORKS:
  1. Agent sends POST /tools/call with ToolCall(name="mcp.web_search", ...)
  2. Server looks up "mcp.web_search" in the TOOL_REGISTRY
  3. Imports the correct tool module (e.g. tools.web_search)
  4. Calls module.run(params)
  5. Returns ToolResult back to agent

WHY REGISTRY-DRIVEN?
  Adding a new tool requires:
    1. Create mcp-server/tools/my_tool.py  with  async def run(params) -> ToolResult
    2. Add entry to registry.yaml
  That's it. No changes to this file. No new routes. No hardcoded if/else.

ENDPOINTS:
  GET  /health            — liveness check
  GET  /tools             — list all registered tools
  POST /tools/call        — execute a tool
  POST /tools/call/batch  — execute multiple tools (returns list of ToolResults)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import importlib
import os
import time
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from mcp_server.contracts import ToolCall, ToolResult, ToolListResponse

# ── App setup ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="MCP Tool Gateway",
    description="Model Context Protocol server — provides tools to agent nodes",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Registry loading ──────────────────────────────────────────────────────
# Loaded once at startup — maps tool names to their module paths
_TOOL_REGISTRY: dict[str, dict[str, Any]] = {}


def _load_registry() -> None:
    """
    Parse registry.yaml and build an in-memory map of:
      { "mcp.web_search": { "module": "mcp_server.tools.web_search", ... } }

    This runs at startup. If registry.yaml is malformed, the server fails
    immediately rather than silently misrouting calls at runtime.
    """
    registry_path = Path(__file__).parent / "registry.yaml"
    if not registry_path.exists():
        print(f"⚠️  registry.yaml not found at {registry_path} — no tools loaded")
        return

    with open(registry_path) as f:
        raw = yaml.safe_load(f)

    tools = raw.get("tools", [])
    for tool in tools:
        name = tool.get("name")
        if name:
            _TOOL_REGISTRY[name] = tool

    print(f"✅ MCP registry loaded: {len(_TOOL_REGISTRY)} tools")
    for name in _TOOL_REGISTRY:
        print(f"   • {name}")


@app.on_event("startup")
async def startup_event() -> None:
    _load_registry()


# ── Tool dispatcher ───────────────────────────────────────────────────────
async def _dispatch_tool(call: ToolCall) -> ToolResult:
    """
    Core dispatcher — finds the right tool module and calls it.

    This is registry-driven: if "mcp.new_tool" is in registry.yaml
    pointing to module "mcp_server.tools.new_tool", it just works.
    """
    tool_name = call.name

    # 1. Check registry
    if tool_name not in _TOOL_REGISTRY:
        available = list(_TOOL_REGISTRY.keys())
        return ToolResult.fail(
            f"Unknown tool: '{tool_name}'. Available: {available}",
            tool_name=tool_name,
        )

    tool_meta = _TOOL_REGISTRY[tool_name]
    module_path = tool_meta.get("module")

    if not module_path:
        return ToolResult.fail(
            f"Tool '{tool_name}' in registry has no 'module' field",
            tool_name=tool_name,
        )

    # 2. Dynamically import the tool module
    #    This is safe because module_path comes from our own registry.yaml
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return ToolResult.fail(
            f"Failed to import tool module '{module_path}': {exc}",
            tool_name=tool_name,
        )

    # 3. Call the tool's run() function
    if not hasattr(module, "run"):
        return ToolResult.fail(
            f"Module '{module_path}' has no async run(params) function",
            tool_name=tool_name,
        )

    try:
        result: ToolResult = await module.run(call.params)
        result.tool_name = tool_name      # ensure tool_name is always set
        return result
    except Exception as exc:
        return ToolResult.fail(
            f"Tool execution error: {exc}", tool_name=tool_name
        )


# ── Routes ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "tools_loaded": len(_TOOL_REGISTRY),
        "server": "mcp-tool-gateway",
    }


@app.get("/tools", response_model=ToolListResponse)
async def list_tools() -> ToolListResponse:
    """List all registered tools — useful for agent discovery."""
    tools = [
        {
            "name": name,
            "description": meta.get("description", ""),
            "params": meta.get("params", {}),
        }
        for name, meta in _TOOL_REGISTRY.items()
    ]
    return ToolListResponse(tools=tools, count=len(tools))


@app.post("/tools/call", response_model=ToolResult)
async def call_tool(call: ToolCall) -> ToolResult:
    """
    Execute a single tool call.

    Example request body:
    {
      "name": "mcp.web_search",
      "params": {"query": "SaaS pricing trends", "max_results": 5}
    }
    """
    return await _dispatch_tool(call)


@app.post("/tools/call/batch", response_model=list[ToolResult])
async def call_tools_batch(calls: list[ToolCall]) -> list[ToolResult]:
    """
    Execute multiple tool calls — runs them concurrently for speed.

    WHY BATCH?
      The LLM-Compiler pattern (novel 2024 research) lets the planner generate
      a DAG of parallel tool calls. E.g. "search web" + "query DB" can run
      simultaneously instead of sequentially — saves 1-2 seconds per request.
    """
    import asyncio
    results = await asyncio.gather(
        *[_dispatch_tool(call) for call in calls],
        return_exceptions=True,
    )
    # Wrap any unexpected exceptions as ToolResult failures
    return [
        r if isinstance(r, ToolResult)
        else ToolResult.fail(str(r), tool_name="unknown")
        for r in results
    ]


# ── Dev runner ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("MCP_SERVER_PORT", 8001))
    uvicorn.run("mcp_server.main:app", host="0.0.0.0", port=port, reload=True)