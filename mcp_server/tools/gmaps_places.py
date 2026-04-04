"""
Google Places Text Search (legacy JSON API) — used by lead_gen scraper.

Requires GOOGLE_MAPS_API_KEY and the Places API enabled for the key
(Google Cloud Console → APIs & Services → Enable "Places API").

Docs: https://developers.google.com/maps/documentation/places/web-service/search-text
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from mcp_server.contracts import ToolResult

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
TOOL_NAME = "mcp.gmaps_places_search"


async def run(params: dict[str, Any]) -> ToolResult:
    query = (params.get("query") or "").strip()
    if not query:
        return ToolResult.fail("Missing required param: query", tool_name=TOOL_NAME)

    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return ToolResult.fail(
            "GOOGLE_MAPS_API_KEY is not set in the MCP server environment (.env.mcp)",
            tool_name=TOOL_NAME,
        )

    location = (params.get("location") or "").strip()
    full_query = f"{query} {location}".strip() if location else query

    try:
        limit = int(params.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 20))

    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                TEXT_SEARCH_URL,
                params={"query": full_query, "key": api_key},
            )
    except httpx.HTTPError as exc:
        return ToolResult.fail(f"HTTP error calling Places API: {exc}", tool_name=TOOL_NAME)

    if resp.status_code != 200:
        return ToolResult.fail(
            f"Places API HTTP {resp.status_code}: {resp.text[:500]}",
            tool_name=TOOL_NAME,
        )

    try:
        payload = resp.json()
    except Exception as exc:
        return ToolResult.fail(f"Invalid JSON from Places API: {exc}", tool_name=TOOL_NAME)

    status = payload.get("status")
    if status == "ZERO_RESULTS":
        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult.ok(data=[], tool_name=TOOL_NAME, duration_ms=duration_ms)

    if status != "OK":
        err = payload.get("error_message") or status or "unknown error"
        return ToolResult.fail(
            f"Places API status={status}: {err}. "
            "Enable Places API for this key and ensure billing is active if required.",
            tool_name=TOOL_NAME,
        )

    results = payload.get("results") or []
    if not isinstance(results, list):
        results = []

    normalized: list[dict[str, Any]] = []
    for row in results[:limit]:
        if not isinstance(row, dict):
            continue
        loc = row.get("geometry") or {}
        loc = loc.get("location") if isinstance(loc, dict) else None
        normalized.append(
            {
                "name": row.get("name"),
                "formatted_address": row.get("formatted_address"),
                "rating": row.get("rating"),
                "user_ratings_total": row.get("user_ratings_total"),
                "place_id": row.get("place_id"),
                "business_status": row.get("business_status"),
                "types": row.get("types"),
                "lat": loc.get("lat") if isinstance(loc, dict) else None,
                "lng": loc.get("lng") if isinstance(loc, dict) else None,
            }
        )

    duration_ms = (time.perf_counter() - start) * 1000
    return ToolResult.ok(data=normalized, tool_name=TOOL_NAME, duration_ms=duration_ms)
