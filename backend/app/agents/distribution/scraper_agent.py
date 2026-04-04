"""
scraper_agent.py — Generic external source fetcher for distribution workflows.

NODE CONTRACT:
  Reads:  workflow_type, constraints (filters.source, filters.query, filters.location, limit)
  Writes: artifacts.workflow_data["items"]  — normalized list of fetched records
          metrics.item_count               — count of fetched records
          iteration_count                  — incremented by 1 on empty/failed result
          errors                           — appended on failure
  Calls MCP: mcp.gmaps_places_search  OR  mcp.web_search  (based on filters.source)

DESIGN:
  Source resolution (from constraints.filters.get("source")):
    "gmaps"  (default) → mcp.gmaps_places_search
    "web"              → mcp.web_search
    Any future value   → add one elif here and one MCP tool registration — nodes unchanged

  All results are normalized into a common shape so downstream nodes
  (enrichment_agent, assigner_agent) never see source-specific field names.

  NORMALIZED ITEM SHAPE:
    {
      "id":      str,         # generated from name + source index if absent
      "name":    str,
      "address": str | None,
      "phone":   str | None,
      "rating":  float | None,
      "source":  str,         # "gmaps" | "web" | ...
      "raw":     dict         # original result, preserved for traceability
    }

  WRITES "items" (same key as fetch_node) so enrichment_agent works for both
  analytics and distribution pipelines without code changes.

  QUERY CONSTRUCTION:
    Reads from constraints.filters:
      "query"    → search term (required; defaults to workflow_type)
      "location" → geographic scope e.g. "Surabaya" (optional)
      "limit"    → max results from the source API (also from constraints.limit)
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool

if TYPE_CHECKING:
    from app.state import AgentState


async def scraper_agent(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: fetches external records from a configured source and writes
    normalized items to workflow_data["items"].
    """
    workflow_type: str = state.get("workflow_type") or "search"
    run_id: str = state.get("run_id", "")
    constraints = state.get("constraints") or {}
    filters: dict = constraints.get("filters") or {}
    limit: int = constraints.get("limit") or 20

    source: str = filters.get("source") or "gmaps"
    query: str = filters.get("query") or filters.get("category") or workflow_type
    location: str = filters.get("location") or filters.get("region") or ""

    items: list[dict] = []
    errors: list[str] = []

    if source == "gmaps":
        items, err = await _fetch_gmaps(query, location, limit, run_id)
        if err:
            errors.append(err)
    elif source == "web":
        items, err = await _fetch_web(query, location, limit, run_id)
        if err:
            errors.append(err)
    else:
        errors.append(f"scraper_agent: unknown source '{source}'")

    if not items:
        # No results — trigger retry_router via iteration_count increment
        return {
            "iteration_count": 1,
            "errors": errors or [f"scraper_agent: no items returned (source={source}, query={query!r})"],
        }

    artifacts = state.get("artifacts") or {}
    existing_metrics = state.get("metrics") or {}

    update: dict[str, Any] = {
        "artifacts": {
            **artifacts,
            "workflow_data": {"items": items},
        },
        "metrics": {
            **existing_metrics,
            "item_count": len(items),
        },
    }
    if errors:
        update["errors"] = errors
    return update


# ── Private source helpers ────────────────────────────────────────────────────

async def _fetch_gmaps(
    query: str, location: str, limit: int, run_id: str
) -> tuple[list[dict], str | None]:
    """Calls mcp.gmaps_places_search and normalizes results."""
    params: dict[str, Any] = {"query": query, "limit": limit}
    if location:
        params["location"] = location

    try:
        result = await call_tool("mcp.gmaps_places_search", params, meta={"run_id": run_id})
    except Exception as exc:
        return [], f"scraper_agent: gmaps call failed: {exc}"

    if not result.success:
        return [], f"scraper_agent: gmaps error: {result.error}"

    raw_results: list = result.data or []
    return [_normalize_gmaps(r, i) for i, r in enumerate(raw_results)], None


async def _fetch_web(
    query: str, location: str, limit: int, run_id: str
) -> tuple[list[dict], str | None]:
    """Calls mcp.web_search and normalizes results."""
    full_query = f"{query} {location}".strip() if location else query
    try:
        result = await call_tool(
            "mcp.web_search",
            {"query": full_query, "limit": limit},
            meta={"run_id": run_id},
        )
    except Exception as exc:
        return [], f"scraper_agent: web search failed: {exc}"

    if not result.success:
        return [], f"scraper_agent: web search error: {result.error}"

    raw_results: list = result.data or []
    return [_normalize_web(r, i) for i, r in enumerate(raw_results)], None


def _normalize_gmaps(raw: dict, index: int) -> dict:
    name = raw.get("name") or raw.get("title") or f"result_{index}"
    return {
        "id": _make_id(name, index),
        "name": name,
        "address": raw.get("address") or raw.get("formatted_address"),
        "phone": raw.get("phone") or raw.get("formatted_phone_number"),
        "rating": _to_float(raw.get("rating")),
        "source": "gmaps",
        "raw": raw,
    }


def _normalize_web(raw: dict, index: int) -> dict:
    name = raw.get("title") or raw.get("name") or f"result_{index}"
    return {
        "id": _make_id(name, index),
        "name": name,
        "address": raw.get("address"),
        "phone": raw.get("phone"),
        "rating": None,
        "source": "web",
        "raw": raw,
    }


def _make_id(name: str, index: int) -> str:
    """Deterministic short ID from name + position."""
    seed = f"{name}-{index}"
    return hashlib.md5(seed.encode()).hexdigest()[:12]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
