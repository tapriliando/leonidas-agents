"""
enrichment_agent.py — Generic item enrichment via web search context.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["items"]  — from scraper_agent (or fetch_node)
          workflow_type, constraints        — used for query building and limit
  Writes: artifacts.workflow_data["enriched_items"]  — items with "context" field added
          errors                                      — appended on MCP failure
  Calls MCP: mcp.web_search  (one call per item, up to limit)

DESIGN:
  This node adds a "context" field to each item by querying the web for information
  about the item. The search query is constructed from the item's name and an optional
  "enrich_field" constraint.

  QUERY CONSTRUCTION:
    constraints.filters.get("enrich_field", "name") picks which item field to use in
    the search query. Default "name" works for most datasets.
    The search query is: "{item[enrich_field]} {item.get('address', '')}".strip()

  LIMIT HANDLING:
    Only enriches up to constraints.limit items (or all items if limit is None).

  FAILURE PER ITEM:
    If a web search fails for a single item, that item is included with context = None
    and the error is appended. We never fail the entire node for a partial lookup failure.

  ALWAYS PRODUCES enriched_items:
    Even if all web searches fail, enriched_items is set (with all contexts = None)
    so assigner_agent never encounters a missing key.

  KEY CONTRACT:
    Reads "items", writes "enriched_items" — same as if the data were "leads",
    "suppliers", "candidates", or any other domain. No domain-specific keys in this file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool

if TYPE_CHECKING:
    from app.state import AgentState


async def enrichment_agent(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: enriches items with web-search context snippets.

    Returns a partial state update with "enriched_items" added to workflow_data.
    """
    run_id: str = state.get("run_id", "")
    constraints = state.get("constraints") or {}
    filters: dict = constraints.get("filters") or {}
    limit: int | None = constraints.get("limit")

    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}
    items: list[dict] = workflow_data.get("items") or []

    # Respect limit — only enrich up to N items
    items_to_enrich = items[:limit] if limit is not None else items
    enrich_field: str = filters.get("enrich_field") or "name"

    enriched: list[dict] = []
    errors: list[str] = []

    for item in items_to_enrich:
        item_with_ctx = {**item}  # shallow copy to avoid mutating state

        name_val = item.get(enrich_field) or item.get("name") or str(item.get("id", ""))
        address_val = item.get("address") or ""
        query = f"{name_val} {address_val}".strip()

        context_snippet: str | None = None
        if query:
            try:
                result = await call_tool(
                    "mcp.web_search",
                    {"query": query, "limit": 1},
                    meta={"run_id": run_id},
                )
                if result.success and result.data:
                    first = result.data[0] if isinstance(result.data, list) else result.data
                    context_snippet = (
                        first.get("snippet") or first.get("description") or first.get("text")
                    )
                elif not result.success:
                    errors.append(f"enrichment_agent: web search failed for '{name_val}': {result.error}")
            except Exception as exc:
                errors.append(f"enrichment_agent: exception for '{name_val}': {exc}")

        item_with_ctx["context"] = context_snippet
        enriched.append(item_with_ctx)

    updated_workflow_data = {**workflow_data, "enriched_items": enriched}
    updated_artifacts = {**artifacts, "workflow_data": updated_workflow_data}

    update: dict[str, Any] = {"artifacts": updated_artifacts}
    if errors:
        update["errors"] = errors
    return update
