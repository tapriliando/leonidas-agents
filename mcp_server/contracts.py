"""
mcp-server/contracts.py
─────────────────────────────────────────────────────────────────────────────
Shared Pydantic models used by BOTH the MCP server AND mcp_client.py.

WHY A SEPARATE CONTRACTS FILE?
  The server and client must agree on the exact shape of requests/responses.
  Keeping them in one file means if you change ToolResult, both sides update.

USAGE:
  from mcp_server.contracts import ToolCall, ToolResult

  # Agent side (client):
  call = ToolCall(name="mcp.supabase_query", params={"table": "leads"})

  # Tool side (server):
  return ToolResult(success=True, data=rows, tool_name="mcp.supabase_query")
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations
from typing import Any, Optional
from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """
    What an agent sends to the MCP server.

    name   — must match a key in registry.yaml (e.g. "mcp.supabase_query")
    params — tool-specific dict; each tool defines its own expected keys
    meta   — optional pass-through (session_id, run_id for tracing)
    """
    name: str = Field(..., description="Tool name as defined in registry.yaml")
    params: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """
    What the MCP server returns to the agent.

    success   — True if the tool ran without errors
    data      — the actual result (list, dict, str — tool-specific)
    error     — human-readable error if success=False
    tool_name — echoed back so clients can match responses to calls
    duration_ms — how long the tool took (useful for performance logging)
    """
    success: bool
    data: Any = None
    error: Optional[str] = None
    tool_name: str = ""
    duration_ms: Optional[float] = None

    @classmethod
    def ok(cls, data: Any, tool_name: str = "", duration_ms: float | None = None) -> "ToolResult":
        """Convenience constructor for successful results."""
        return cls(success=True, data=data, tool_name=tool_name, duration_ms=duration_ms)

    @classmethod
    def fail(cls, error: str, tool_name: str = "") -> "ToolResult":
        """Convenience constructor for failures."""
        return cls(success=False, error=error, tool_name=tool_name)


class ToolListResponse(BaseModel):
    """Response from GET /tools — lists all registered tools."""
    tools: list[dict[str, Any]]
    count: int