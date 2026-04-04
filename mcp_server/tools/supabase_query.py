"""
mcp-server/tools/supabase_query.py
─────────────────────────────────────────────────────────────────────────────
Supabase query tool — gives agents read/write access to your database
without importing Supabase directly in every agent.

SUPPORTED OPERATIONS:
  select   — query rows (with optional filters, limit, order)
  insert   — insert one or more rows
  update   — update rows matching a filter
  delete   — delete rows matching a filter
  upsert   — insert or update (merge on conflict)
  rpc      — call a Postgres function (for complex queries / vector search)

CALLED BY AGENTS LIKE:
  # Simple select
  result = await call_tool("mcp.supabase_query", {
      "operation": "select",
      "table": "leads",
      "filters": {"status": "new"},
      "limit": 20,
      "order": {"column": "created_at", "ascending": False}
  })

  # Insert
  result = await call_tool("mcp.supabase_query", {
      "operation": "insert",
      "table": "workflow_runs",
      "data": {"run_id": "...", "status": "running", "user_id": "..."}
  })

  # RPC (e.g. vector similarity search via pgvector)
  result = await call_tool("mcp.supabase_query", {
      "operation": "rpc",
      "function_name": "match_memories",
      "params": {"query_embedding": [...], "match_count": 5}
  })

WHY RPC FOR VECTOR SEARCH?
  pgvector's <-> operator (cosine distance) can't be expressed in Supabase's
  filter API. A Postgres function wraps the SQL so agents just pass embeddings.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import time
import asyncio
from typing import Any

from mcp_server.contracts import ToolResult

# Supabase Python SDK — lazy import so the MCP server starts even without it
try:
    from supabase import create_client, Client as SupabaseClient
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False


def _get_client() -> "SupabaseClient":
    """
    Build the Supabase client from environment variables.
    Called lazily so the module can be imported without credentials present
    (useful in tests with mocks).
    """
    if not _SUPABASE_AVAILABLE:
        raise RuntimeError(
            "supabase package not installed. Run: pip install supabase"
        )
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
    return create_client(url, key)


async def run(params: dict[str, Any]) -> ToolResult:
    """
    Dispatcher — routes to the correct Supabase operation.
    Every tool module must expose an async `run(params)` function.
    """
    operation = params.get("operation", "select")
    table = params.get("table")

    if operation != "rpc" and not table:
        return ToolResult.fail(
            "Missing required param: table", tool_name="mcp.supabase_query"
        )

    start = time.perf_counter()
    try:
        client = _get_client()
        data = await _dispatch(client, operation, params)
        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult.ok(data, tool_name="mcp.supabase_query", duration_ms=duration_ms)

    except Exception as exc:
        return ToolResult.fail(
            f"Supabase error ({operation}): {exc}", tool_name="mcp.supabase_query"
        )


async def _dispatch(
    client: "SupabaseClient",
    operation: str,
    params: dict[str, Any],
) -> Any:
    """
    Route to the right Supabase SDK call.

    NOTE: The Supabase Python SDK is synchronous under the hood (it wraps
    httpx synchronously). We run it in a thread executor to avoid blocking
    the async FastAPI event loop.
    """
    loop = asyncio.get_event_loop()

    if operation == "select":
        return await loop.run_in_executor(None, lambda: _select(client, params))
    elif operation == "insert":
        return await loop.run_in_executor(None, lambda: _insert(client, params))
    elif operation == "update":
        return await loop.run_in_executor(None, lambda: _update(client, params))
    elif operation == "delete":
        return await loop.run_in_executor(None, lambda: _delete(client, params))
    elif operation == "upsert":
        return await loop.run_in_executor(None, lambda: _upsert(client, params))
    elif operation == "rpc":
        return await loop.run_in_executor(None, lambda: _rpc(client, params))
    else:
        raise ValueError(f"Unknown operation: {operation}. Use: select, insert, update, delete, upsert, rpc")


def _select(client: "SupabaseClient", params: dict) -> list[dict]:
    table = params["table"]
    columns = params.get("columns", "*")
    filters = params.get("filters", {})
    limit = params.get("limit", 100)
    order = params.get("order")          # {"column": "created_at", "ascending": False}

    query = client.table(table).select(columns)

    # Apply equality filters — for advanced filters (gte, lt) agents can use RPC
    for column, value in filters.items():
        query = query.eq(column, value)

    if order:
        query = query.order(
            order["column"],
            desc=not order.get("ascending", True),
        )

    response = query.limit(limit).execute()
    return response.data


def _insert(client: "SupabaseClient", params: dict) -> dict:
    table = params["table"]
    data = params["data"]                # dict or list of dicts
    response = client.table(table).insert(data).execute()
    return response.data


def _update(client: "SupabaseClient", params: dict) -> dict:
    table = params["table"]
    data = params["data"]                # fields to update
    filters = params.get("filters", {})
    query = client.table(table).update(data)
    for column, value in filters.items():
        query = query.eq(column, value)
    return query.execute().data


def _delete(client: "SupabaseClient", params: dict) -> dict:
    table = params["table"]
    filters = params.get("filters", {})
    query = client.table(table).delete()
    for column, value in filters.items():
        query = query.eq(column, value)
    return query.execute().data


def _upsert(client: "SupabaseClient", params: dict) -> dict:
    table = params["table"]
    data = params["data"]
    return client.table(table).upsert(data).execute().data


def _rpc(client: "SupabaseClient", params: dict) -> Any:
    """
    Call a Postgres function — most powerful escape hatch.
    Used for vector similarity search, complex aggregations, etc.
    """
    function_name = params.get("function_name")
    if not function_name:
        raise ValueError("rpc operation requires: function_name")
    rpc_params = params.get("params", {})
    return client.rpc(function_name, rpc_params).execute().data