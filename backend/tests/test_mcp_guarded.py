"""MCP call_tool_guarded allowlist and budget."""

from __future__ import annotations

import pytest

pytest.importorskip("httpx")

from unittest.mock import AsyncMock, patch

from app.registry import refresh_registry
from mcp_server.contracts import ToolResult


@pytest.mark.asyncio
async def test_guarded_rejects_disallowed_tool():
    refresh_registry()
    with patch("app.mcp_client.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = AsyncMock()
        from app.mcp_client import call_tool_guarded

        r = await call_tool_guarded(
            "research_assistant_md",
            "mcp.supabase_query",
            {},
            meta={"run_id": "x", "_tool_budget": [0], "_max_tool_calls": 3},
        )
        assert r.success is False
        assert "allowlist" in (r.error or "").lower()


@pytest.mark.asyncio
async def test_guarded_budget(monkeypatch):
    refresh_registry()
    calls = {"n": 0}

    async def fake_post(*args, **kwargs):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return ToolResult.ok({"ok": True}, tool_name="mcp.web_search").model_dump()

        calls["n"] += 1
        return R()

    with patch("app.mcp_client.httpx.AsyncClient") as client_cls:
        client_cls.return_value.__aenter__.return_value.post = fake_post
        from app.mcp_client import call_tool_guarded

        budget = [0]
        meta = {"_tool_budget": budget, "_max_tool_calls": 2}
        r1 = await call_tool_guarded("research_assistant_md", "mcp.web_search", {"query": "a"}, meta=meta)
        r2 = await call_tool_guarded("research_assistant_md", "mcp.web_search", {"query": "b"}, meta=meta)
        r3 = await call_tool_guarded("research_assistant_md", "mcp.web_search", {"query": "c"}, meta=meta)
        assert r1.success is True
        assert r2.success is True
        assert r3.success is False
        assert "max_tool_calls" in (r3.error or "").lower()
