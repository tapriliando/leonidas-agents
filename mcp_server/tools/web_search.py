"""
mcp-server/tools/web_search.py
─────────────────────────────────────────────────────────────────────────────
Web search tool — wraps Tavily Search API (best LLM-optimized search).

WHY TAVILY?
  Unlike Google/Bing which return HTML pages, Tavily returns clean
  pre-extracted text designed for LLMs. No parsing, no scraping noise.
  Free tier: 1,000 searches/month. Get key at https://tavily.com

FALLBACK:
  If TAVILY_API_KEY is not set, falls back to DuckDuckGo (no key needed,
  but less reliable for structured results).

CALLED BY AGENTS LIKE:
  result = await call_tool("mcp.web_search", {
      "query": "latest SaaS pricing models 2025",
      "max_results": 5,
      "search_depth": "advanced"   # "basic" (faster) or "advanced" (deeper)
  })
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from mcp_server.contracts import ToolResult


TAVILY_API_URL = "https://api.tavily.com/search"
DDGO_API_URL = "https://api.duckduckgo.com/"


async def run(params: dict[str, Any]) -> ToolResult:
    """
    Entry point — every tool module must expose an async `run(params)` function.
    The MCP server dispatcher calls this after looking up the tool by name.
    """
    query: str = params.get("query", "")
    max_results: int = params.get("max_results", 5)
    search_depth: str = params.get("search_depth", "basic")

    if not query:
        return ToolResult.fail("Missing required param: query", tool_name="mcp.web_search")

    tavily_key = os.getenv("TAVILY_API_KEY")

    start = time.perf_counter()
    try:
        if tavily_key:
            result_data = await _search_tavily(query, max_results, search_depth, tavily_key)
        else:
            result_data = await _search_ddgo(query, max_results)

        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult.ok(result_data, tool_name="mcp.web_search", duration_ms=duration_ms)

    except httpx.TimeoutException:
        return ToolResult.fail("Web search timed out", tool_name="mcp.web_search")
    except Exception as exc:
        return ToolResult.fail(f"Web search error: {exc}", tool_name="mcp.web_search")


async def _search_tavily(
    query: str,
    max_results: int,
    search_depth: str,
    api_key: str,
) -> dict[str, Any]:
    """
    Tavily returns structured results with:
      - title, url, content (pre-extracted text), score (relevance 0-1)

    We return a normalized shape so agents don't need to know which
    search engine ran underneath.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TAVILY_API_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": search_depth,
                "include_answer": True,    # Tavily can also summarize the answer
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
        raw = response.json()

    return {
        "query": query,
        "answer": raw.get("answer"),       # high-level summary from Tavily
        "results": [
            {
                "title": r.get("title"),
                "url": r.get("url"),
                "content": r.get("content"),
                "score": r.get("score"),
                "source": "tavily",
            }
            for r in raw.get("results", [])
        ],
        "total_results": len(raw.get("results", [])),
    }


async def _search_ddgo(query: str, max_results: int) -> dict[str, Any]:
    """
    DuckDuckGo instant answer API — no API key needed.
    Less powerful than Tavily (returns topic summaries, not full search results)
    but works for testing without credentials.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            DDGO_API_URL,
            params={
                "q": query,
                "format": "json",
                "no_html": 1,
                "skip_disambig": 1,
            },
        )
        response.raise_for_status()
        raw = response.json()

    # DuckDuckGo instant answers are less structured — normalize anyway
    results = []
    if raw.get("AbstractText"):
        results.append({
            "title": raw.get("Heading", query),
            "url": raw.get("AbstractURL", ""),
            "content": raw.get("AbstractText", ""),
            "score": 1.0,
            "source": "duckduckgo",
        })
    for topic in raw.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            results.append({
                "title": topic.get("Text", "")[:60],
                "url": topic.get("FirstURL", ""),
                "content": topic.get("Text", ""),
                "score": 0.5,
                "source": "duckduckgo",
            })

    return {
        "query": query,
        "answer": raw.get("AbstractText"),
        "results": results[:max_results],
        "total_results": len(results),
    }