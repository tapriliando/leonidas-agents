"""
MCP client — call the MCP server with tool name + JSON input.

Agents should prefer call_tool_guarded() so per-agent allowlists and budgets apply.
"""

from __future__ import annotations

from typing import Any, Optional

import os
import httpx

from mcp_server.contracts import ToolCall, ToolResult


MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8001")


async def call_tool(tool_id: str, input_payload: dict[str, Any], meta: Optional[dict[str, Any]] = None) -> ToolResult:
    meta = meta or {}
    call = ToolCall(name=tool_id, params=input_payload, meta=meta)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{MCP_SERVER_URL}/tools/call", json=call.model_dump())
            response.raise_for_status()
            data = response.json()
            return ToolResult(**data)
    except Exception as exc:
        return ToolResult.fail(f"MCP server unreachable or error: {exc}", tool_name=tool_id)


async def call_tool_guarded(
    agent_id: str,
    tool_id: str,
    input_payload: dict[str, Any],
    meta: Optional[dict[str, Any]] = None,
    *,
    timeout_seconds: float = 30.0,
) -> ToolResult:
    """
    Enforce per-agent MCP tool allowlist and max call budget (tracked in meta).

    meta may contain:
      _tool_budget: single-element list[int] — mutable counter incremented on each allowed call
      _max_tool_calls: int — defaults from registry tool_policy or 8
    """
    from app.registry import get_tool_policy_for_agent, get_tools_for_agent

    allowed = get_tools_for_agent(agent_id)
    if tool_id not in allowed:
        return ToolResult.fail(
            f"Tool {tool_id!r} is not in the allowlist for agent {agent_id!r}",
            tool_name=tool_id,
        )

    meta = dict(meta or {})
    budget = meta.get("_tool_budget")
    if not isinstance(budget, list) or len(budget) != 1 or not isinstance(budget[0], int):
        budget = [0]
        meta["_tool_budget"] = budget

    policy = get_tool_policy_for_agent(agent_id)
    max_calls = int(meta.get("_max_tool_calls") or policy.get("max_tool_calls") or 8)
    if budget[0] >= max_calls:
        return ToolResult.fail(
            f"max_tool_calls ({max_calls}) exceeded for agent {agent_id!r}",
            tool_name=tool_id,
        )
    budget[0] += 1

    call = ToolCall(name=tool_id, params=input_payload, meta=meta)
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(f"{MCP_SERVER_URL}/tools/call", json=call.model_dump())
            response.raise_for_status()
            data = response.json()
            return ToolResult(**data)
    except Exception as exc:
        return ToolResult.fail(f"MCP server unreachable or error: {exc}", tool_name=tool_id)


async def web_search(query: str, max_results: int = 5) -> ToolResult:
    params = {"query": query, "max_results": max_results}
    return await call_tool("mcp.web_search", params)
