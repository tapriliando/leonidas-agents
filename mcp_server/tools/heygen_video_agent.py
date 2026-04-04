"""
HeyGen Video Agent API — start a presenter-style video generation job.

Docs: https://docs.heygen.com/ (endpoint shape may evolve; we return raw JSON).

CALLED BY AGENTS AS:
    await call_tool(
        "mcp.heygen_video_agent_generate",
        {"prompt": "A presenter explaining our product launch in 30 seconds"},
        meta={"run_id": run_id},
    )

AUTH: HEYGEN_API_KEY in .env.mcp (sent as X-API-KEY header).
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from mcp_server.contracts import ToolResult

HEYGEN_GENERATE_URL = "https://api.heygen.com/v1/video_agent/generate"


async def run(params: dict[str, Any]) -> ToolResult:
    prompt: str = (params.get("prompt") or "").strip()
    if not prompt:
        return ToolResult.fail("Missing required param: prompt", tool_name="mcp.heygen_video_agent_generate")

    api_key = os.getenv("HEYGEN_API_KEY")
    if not api_key:
        return ToolResult.fail(
            "HEYGEN_API_KEY is not set in the MCP server environment",
            tool_name="mcp.heygen_video_agent_generate",
        )

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                HEYGEN_GENERATE_URL,
                headers={
                    "X-API-KEY": api_key,
                    "Content-Type": "application/json",
                },
                json={"prompt": prompt},
            )
        duration_ms = (time.perf_counter() - start) * 1000

        try:
            body = response.json()
        except Exception:
            body = {"raw_text": response.text[:2000]}

        if response.status_code >= 400:
            err_msg = body.get("message") or body.get("error") or response.text[:500]
            return ToolResult.fail(
                f"HeyGen API {response.status_code}: {err_msg}",
                tool_name="mcp.heygen_video_agent_generate",
                duration_ms=duration_ms,
            )

        return ToolResult.ok(
            {"prompt": prompt, "response": body},
            tool_name="mcp.heygen_video_agent_generate",
            duration_ms=duration_ms,
        )
    except httpx.TimeoutException:
        return ToolResult.fail("HeyGen request timed out", tool_name="mcp.heygen_video_agent_generate")
    except Exception as exc:
        return ToolResult.fail(f"HeyGen error: {exc}", tool_name="mcp.heygen_video_agent_generate")
