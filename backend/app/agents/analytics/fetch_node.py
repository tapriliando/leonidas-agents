"""
fetch_node.py — Generic Supabase data fetcher for any analytics workflow.

NODE CONTRACT:
  Reads:  workflow_type, constraints (filters, limit), run_id
  Writes: artifacts.workflow_data["items"]  — list of raw rows
          metrics.item_count                — count of fetched rows
          iteration_count                   — incremented by 1 on empty result
          errors                            — appended on failure
  Calls MCP: mcp.supabase_query (select)

DESIGN:
  This node is completely workflow-agnostic. It never knows what the data represents.

  TABLE RESOLUTION (in order):
    1. constraints.filters.get("table")   — explicit table override from intent_node
    2. workflow_type                       — used as the table name by default
    This means a "complaint_analysis" workflow with no table filter queries the
    "complaint_analysis" table. Setting filters.table = "complaints" queries "complaints".
    A future workflow needs zero code changes here — just configure the filter.

  FILTER KEYS:
    Any key in constraints.filters that is not "table" is passed as an equality filter
    to Supabase. Example: filters = {"table": "complaints", "status": "open", "region": "East Java"}
    produces: SELECT * FROM complaints WHERE status = 'open' AND region = 'East Java' LIMIT N

  EMPTY RESULT:
    Returns {"iteration_count": 1} which triggers retry_router via operator.add.
    After MAX_ITERATIONS the router routes to "fail".

WRITES TO STATE (on success):
  {
    "artifacts": {**existing, "workflow_data": {"items": [row, ...]}},
    "metrics":   {**existing, "item_count": N}
  }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool

if TYPE_CHECKING:
    from app.state import AgentState


async def fetch_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: fetches rows from Supabase and writes them as "items".

    Returns a partial state update. Always writes to the "items" key so downstream
    nodes (summarize_node, enrichment_agent) have a consistent read contract.
    """
    workflow_type: str = state.get("workflow_type") or "unknown"
    run_id: str = state.get("run_id", "")
    constraints = state.get("constraints") or {}
    filters: dict = constraints.get("filters") or {}
    limit: int | None = constraints.get("limit")

    # Resolve the source table: explicit override wins, then fall back to workflow_type
    table = filters.get("table") or workflow_type

    # Build Supabase select params — exclude the "table" key from equality filters
    row_filters = {k: v for k, v in filters.items() if k != "table" and v is not None}

    params: dict[str, Any] = {
        "operation": "select",
        "table": table,
        "filters": row_filters,
    }
    if limit:
        params["limit"] = limit

    try:
        result = await call_tool("mcp.supabase_query", params, meta={"run_id": run_id})
    except Exception as exc:
        return {
            "iteration_count": 1,
            "errors": [f"fetch_node: MCP call failed: {exc}"],
        }

    if not result.success:
        return {
            "iteration_count": 1,
            "errors": [f"fetch_node: {result.error}"],
        }

    rows: list = result.data or []

    if not rows:
        return {
            "iteration_count": 1,
            "errors": [f"fetch_node: no items returned from table '{table}'"],
        }

    artifacts = state.get("artifacts") or {}
    existing_metrics = state.get("metrics") or {}

    return {
        "artifacts": {
            **artifacts,
            "workflow_data": {"items": rows},
        },
        "metrics": {
            **existing_metrics,
            "item_count": len(rows),
        },
    }
