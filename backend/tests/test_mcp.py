"""
tests/test_mcp.py
─────────────────────────────────────────────────────────────────────────────
Phase 2 tests — MCP server, tools, and client.

STRATEGY:
  We don't call real Supabase/Redis/Tavily in tests.
  Instead we mock the external calls and verify:
    ✓ The server dispatches correctly by tool name
    ✓ Each tool returns a well-formed ToolResult
    ✓ Errors are handled gracefully (no exceptions escape)
    ✓ The client handles server-down scenarios cleanly

Run: pytest tests/test_mcp.py -v
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mcp_server.contracts import ToolCall, ToolResult


# ── Contract tests ────────────────────────────────────────────────────────
class TestContracts:
    def test_tool_result_ok(self):
        r = ToolResult.ok({"rows": [1, 2]}, tool_name="mcp.test")
        assert r.success is True
        assert r.data == {"rows": [1, 2]}
        assert r.error is None
        assert r.tool_name == "mcp.test"

    def test_tool_result_fail(self):
        r = ToolResult.fail("something broke", tool_name="mcp.test")
        assert r.success is False
        assert r.data is None
        assert r.error == "something broke"

    def test_tool_call_defaults(self):
        call = ToolCall(name="mcp.web_search")
        assert call.params == {}
        assert call.meta == {}

    def test_tool_call_with_params(self):
        call = ToolCall(name="mcp.web_search", params={"query": "hello"})
        assert call.params["query"] == "hello"


# ── web_search tool tests ─────────────────────────────────────────────────
class TestWebSearchTool:
    @pytest.mark.asyncio
    async def test_missing_query_returns_failure(self):
        from mcp_server.tools import web_search
        result = await web_search.run({})
        assert result.success is False
        assert "query" in result.error.lower()

    @pytest.mark.asyncio
    async def test_tavily_success(self):
        from mcp_server.tools import web_search

        # The mock must return the SAME shape _search_tavily actually returns
        # (which includes the "query" key added by that function)
        mock_response = {
            "query": "SaaS trends",
            "answer": "SaaS is booming.",
            "results": [
                {"title": "Test", "url": "https://test.com", "content": "...", "score": 0.9}
            ],
            "total_results": 1,
        }

        with patch("mcp_server.tools.web_search.os.getenv", return_value="fake-tavily-key"):
            with patch("mcp_server.tools.web_search._search_tavily", new_callable=AsyncMock) as mock_search:
                mock_search.return_value = mock_response
                result = await web_search.run({"query": "SaaS trends"})

        assert result.success is True
        assert result.data["query"] == "SaaS trends"
        assert result.tool_name == "mcp.web_search"

    @pytest.mark.asyncio
    async def test_uses_ddgo_when_no_tavily_key(self):
        from mcp_server.tools import web_search

        ddgo_response = {"query": "test", "answer": None, "results": [], "total_results": 0}

        with patch("mcp_server.tools.web_search.os.getenv", return_value=None):
            with patch("mcp_server.tools.web_search._search_ddgo", new_callable=AsyncMock) as mock_ddgo:
                mock_ddgo.return_value = ddgo_response
                result = await web_search.run({"query": "test query"})

        mock_ddgo.assert_called_once()
        assert result.success is True

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        import httpx
        from mcp_server.tools import web_search

        with patch("mcp_server.tools.web_search.os.getenv", return_value="fake-key"):
            with patch("mcp_server.tools.web_search._search_tavily", side_effect=httpx.TimeoutException("timeout")):
                result = await web_search.run({"query": "anything"})

        assert result.success is False
        assert "timed out" in result.error.lower()


# ── redis_cache tool tests ────────────────────────────────────────────────
class TestRedisCacheTool:
    @pytest.mark.asyncio
    async def test_set_and_get_lifecycle(self):
        """Simulate the full cache set → get cycle with a mock Redis."""
        from mcp_server.tools import redis_cache
        import json

        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock(return_value=True)
        mock_redis.get = AsyncMock(return_value=json.dumps({"count": 42}))

        with patch("mcp_server.tools.redis_cache._get_redis", return_value=mock_redis):
            set_result = await redis_cache.run({
                "operation": "set",
                "key": "leads:count:user-1",
                "value": {"count": 42},
                "ttl": 300,
            })
            get_result = await redis_cache.run({
                "operation": "get",
                "key": "leads:count:user-1",
            })

        assert set_result.success is True
        assert get_result.success is True
        assert get_result.data == {"count": 42}

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self):
        from mcp_server.tools import redis_cache

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)   # cache miss

        with patch("mcp_server.tools.redis_cache._get_redis", return_value=mock_redis):
            result = await redis_cache.run({"operation": "get", "key": "nonexistent"})

        assert result.success is True
        assert result.data is None         # None = cache miss, not an error

    @pytest.mark.asyncio
    async def test_missing_key_returns_failure(self):
        from mcp_server.tools import redis_cache

        mock_redis = AsyncMock()
        with patch("mcp_server.tools.redis_cache._get_redis", return_value=mock_redis):
            result = await redis_cache.run({"operation": "get"})  # no key!

        assert result.success is False
        assert "key" in result.error.lower()

    @pytest.mark.asyncio
    async def test_unknown_operation_returns_failure(self):
        from mcp_server.tools import redis_cache

        mock_redis = AsyncMock()
        with patch("mcp_server.tools.redis_cache._get_redis", return_value=mock_redis):
            result = await redis_cache.run({"operation": "explode", "key": "x"})

        assert result.success is False
        assert "unknown operation" in result.error.lower()


# ── supabase_query tool tests ─────────────────────────────────────────────
class TestSupabaseQueryTool:
    @pytest.mark.asyncio
    async def test_select_operation(self):
        from mcp_server.tools import supabase_query

        mock_response = MagicMock()
        mock_response.data = [{"id": 1, "name": "Lead A"}]

        mock_table = MagicMock()
        mock_table.select.return_value.limit.return_value.execute.return_value = mock_response

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        # asyncio is now a top-level import in supabase_query.py — patchable
        with patch("mcp_server.tools.supabase_query._get_client", return_value=mock_client):
            with patch("mcp_server.tools.supabase_query.asyncio") as mock_asyncio:
                mock_loop = MagicMock()
                mock_asyncio.get_event_loop.return_value = mock_loop
                # run_in_executor should call the lambda synchronously
                async def fake_executor(_, fn):
                    return fn()
                mock_loop.run_in_executor = fake_executor

                result = await supabase_query.run({
                    "operation": "select",
                    "table": "leads",
                    "filters": {},
                })

        assert result.success is True

    @pytest.mark.asyncio
    async def test_missing_table_returns_failure(self):
        from mcp_server.tools import supabase_query

        result = await supabase_query.run({"operation": "select"})  # no table!
        assert result.success is False
        assert "table" in result.error.lower()


# ── MCP client tests ──────────────────────────────────────────────────────
class TestMCPClient:
    @pytest.mark.asyncio
    async def test_call_tool_success(self):
        from backend.app.mcp_client import call_tool

        mock_result = ToolResult.ok({"results": []}, tool_name="mcp.web_search")

        # httpx Response.json() is SYNCHRONOUS — use MagicMock not AsyncMock
        mock_response = MagicMock()
        mock_response.json.return_value = mock_result.model_dump()
        mock_response.raise_for_status = MagicMock()

        mock_inner_client = AsyncMock()
        mock_inner_client.post = AsyncMock(return_value=mock_response)

        with patch("backend.app.mcp_client.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_inner_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await call_tool("mcp.web_search", {"query": "test"})

        assert result.success is True

    @pytest.mark.asyncio
    async def test_call_tool_server_unreachable(self):
        import httpx
        from backend.app.mcp_client import call_tool

        with patch("backend.app.mcp_client.httpx.AsyncClient") as mock_client_cls:
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(
                side_effect=httpx.ConnectError("refused")
            )
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_cm

            result = await call_tool("mcp.web_search", {"query": "test"})

        assert result.success is False
        assert "unreachable" in result.error.lower()

    @pytest.mark.asyncio
    async def test_convenience_wrapper_web_search(self):
        from backend.app.mcp_client import web_search

        with patch("backend.app.mcp_client.call_tool", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = ToolResult.ok({"results": []})
            await web_search("test query", max_results=3)

        mock_call.assert_called_once_with(
            "mcp.web_search", {"query": "test query", "max_results": 3}
        )