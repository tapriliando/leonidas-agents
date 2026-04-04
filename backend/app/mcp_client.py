"""
Phase 2: MCP client — call the MCP server with tool name + JSON input.

Agents NEVER call external APIs directly. They always call this client:

    result = await call_tool("mcp.web_search", {"query": "test"})

Under the hood, this sends an HTTP request to the MCP server:

    POST {MCP_SERVER_URL}/tools/call
    {
      "name": "mcp.web_search",
      "params": {"query": "test"},
      "meta": {"run_id": "...", "session_id": "..."}
    }

The MCP server returns a ToolResult JSON, which we parse back into a
ToolResult Pydantic model from mcp_server.contracts.

This keeps ALL external I/O behind the MCP gateway.
"""

from __future__ import annotations

from typing import Any, Optional

import os
import httpx

from mcp_server.contracts import ToolCall, ToolResult


MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")


async def call_tool(tool_id: str, input_payload: dict[str, Any], meta: Optional[dict[str, Any]] = None) -> ToolResult:
    """
    Execute a registered MCP tool by id.

    This is the ONLY function agents should use to access tools.
    They never see raw URLs or API keys — only tool IDs and params.

    Failure modes:
      - If the MCP server is unreachable, returns ToolResult.fail(...)
      - If the server returns a non-200 status, httpx.raise_for_status() will
        raise, which we catch and wrap as a failed ToolResult.
    """
    meta = meta or {}
    call = ToolCall(name=tool_id, params=input_payload, meta=meta)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{MCP_SERVER_URL}/tools/call", json=call.model_dump())
            response.raise_for_status()
            data = response.json()
            return ToolResult(**data)
    except Exception as exc:
        # Mirror server-down / network issues as a clean ToolResult
        return ToolResult.fail(f"MCP server unreachable or error: {exc}", tool_name=tool_id)


async def web_search(query: str, max_results: int = 5) -> ToolResult:
    """
    Convenience wrapper for the most common tool: mcp.web_search.

    Agents can call:
        result = await web_search("SaaS pricing 2025", max_results=3)

    instead of manually constructing the tool_id and params dict.
    """
    params = {"query": query, "max_results": max_results}
    return await call_tool("mcp.web_search", params)

