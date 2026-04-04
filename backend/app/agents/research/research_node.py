"""
research_node.py — Web search + LLM synthesis for any research query.

NODE CONTRACT:
  Reads:  user_query, goal, constraints, context.domain_context
  Writes: artifacts.workflow_data["items"]     — raw search result dicts
          artifacts.workflow_data["summary"]   — LLM synthesis of all results
          metrics.item_count                   — number of search results
          metrics.quality_score                — LLM confidence in synthesis
          iteration_count                      — incremented on empty search
          errors                               — appended on failure
  Calls MCP: mcp.web_search
  Calls LLM: synthesizes the web results into a structured summary

DESIGN:
  This node is the "fetch + summarize" combined for research workflows.
  Unlike the analytics pipeline (which separates fetch_node from summarize_node),
  research queries need the web results synthesized in the same step because
  the search query itself must be adapted from the user_query/goal.

  QUERY CONSTRUCTION:
    - Primary: constraints.filters.get("query") if set explicitly
    - Fallback: user_query directly
    - Optionally: constraints.filters.get("max_results") or constraints.limit

  WRITES BOTH "items" AND "summary" so:
    - suggest_node (from analytics) can read "summary" to generate recommendations
    - report_node reads both (items = source data, summary = synthesis)
    - The workflow can stop here if suggest_node is not in the graph

  QUALITY SCORING:
    Sets metrics.quality_score from LLM confidence so quality_gate_router
    can decide if the synthesis is good enough to proceed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool
from app.agents.shared.llm_client import call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState

_MAX_RESULTS = 8


async def research_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: fetches web search results and synthesizes them with an LLM.

    Returns a partial state update with items (raw results) and summary (synthesis).
    """
    user_query: str = state.get("user_query") or ""
    goal: str = state.get("goal") or user_query
    run_id: str = state.get("run_id", "")
    constraints = state.get("constraints") or {}
    filters: dict = constraints.get("filters") or {}
    ctx = state.get("context") or {}
    domain_context = ctx.get("domain_context") or {}

    # Resolve the search query
    search_query: str = filters.get("query") or user_query
    max_results: int = min(
        filters.get("max_results") or constraints.get("limit") or _MAX_RESULTS,
        _MAX_RESULTS,
    )

    # ── Step 1: Web search via MCP ────────────────────────────────────────────
    errors: list[str] = []
    raw_results: list[dict] = []

    try:
        result = await call_tool(
            "mcp.web_search",
            {"query": search_query, "max_results": max_results},
            meta={"run_id": run_id},
        )
        if result.success:
            data = result.data or {}
            # Tavily returns {"results": [...], "answer": str}
            # DuckDuckGo returns {"results": [...]}
            raw_list = data.get("results") or (data if isinstance(data, list) else [])
            raw_results = [
                {
                    "title":   r.get("title") or r.get("name") or "",
                    "url":     r.get("url") or r.get("link") or "",
                    "content": r.get("content") or r.get("snippet") or r.get("text") or "",
                    "score":   r.get("score"),
                    "source":  "web",
                }
                for r in raw_list
                if isinstance(r, dict)
            ]
            # Also include the top-level "answer" from Tavily if present
            tavily_answer = data.get("answer") if isinstance(data, dict) else None
        else:
            errors.append(f"research_node: web search failed: {result.error}")
            tavily_answer = None
    except Exception as exc:
        errors.append(f"research_node: MCP call failed: {exc}")
        tavily_answer = None

    if not raw_results and not tavily_answer:
        return {
            "iteration_count": 1,
            "errors": errors or ["research_node: no search results returned"],
        }

    # ── Step 2: LLM synthesis ─────────────────────────────────────────────────
    results_text = "\n\n".join(
        f"[{i+1}] {r['title']}\n{r['url']}\n{r['content'][:600]}"
        for i, r in enumerate(raw_results)
    )
    if tavily_answer:
        results_text = f"DIRECT ANSWER: {tavily_answer}\n\n---\n\n" + results_text

    domain_ctx_str = json.dumps(domain_context, ensure_ascii=False) if domain_context else "(none)"

    prompt = f"""You are a research analyst. Synthesize the web search results below into a
structured summary for a "{goal}" research task.

ORIGINAL QUERY: {user_query}
DOMAIN CONTEXT: {domain_ctx_str}

SEARCH RESULTS ({len(raw_results)} sources):
{results_text}

INSTRUCTIONS:
1. Write a concise overview paragraph (3-5 sentences) covering the key findings.
2. List 4-8 specific, concrete key findings from the sources.
3. Note any important caveats, conflicting information, or data gaps.
4. Rate your confidence in this synthesis (0.0 = very uncertain, 1.0 = very confident).

OUTPUT FORMAT (respond ONLY with valid JSON, no extra text):
{{
  "overview":     "<3-5 sentence synthesis>",
  "key_findings": ["<finding 1>", "<finding 2>", ...],
  "caveats":      ["<caveat>", ...],
  "confidence":   <float 0.0-1.0>
}}"""

    summary: dict[str, Any] = {}
    try:
        raw = await call_llm(prompt)
        summary = parse_json_response(raw, context="research_node")
    except Exception as exc:
        errors.append(f"research_node: synthesis LLM failed: {exc}")
        # Minimal fallback: use Tavily's answer or item count
        summary = {
            "overview": tavily_answer or f"Found {len(raw_results)} results for: {user_query}",
            "key_findings": [r["title"] for r in raw_results[:5] if r.get("title")],
            "caveats": ["Automated synthesis failed — raw results included above."],
            "confidence": 0.3,
        }

    confidence = float(summary.get("confidence") or 0.3)

    artifacts = state.get("artifacts") or {}
    existing_metrics = state.get("metrics") or {}

    updated_workflow_data = {
        **(artifacts.get("workflow_data") or {}),
        "items": raw_results,
        "summary": summary,
    }
    # Seed a video brief for downstream content_generation workflows (multi-workflow plans).
    overview_text = str(summary.get("overview") or "").strip()
    if overview_text:
        updated_workflow_data["content_prompt"] = (
            "A professional on-camera presenter explains the following clearly in about "
            "30–45 seconds, suitable for short-form social video: "
            f"{overview_text[:1200]}"
        )

    update: dict[str, Any] = {
        "artifacts": {**artifacts, "workflow_data": updated_workflow_data},
        "metrics": {
            **existing_metrics,
            "item_count": len(raw_results),
            "quality_score": confidence,
            "confidence": confidence,
        },
    }
    if errors:
        update["errors"] = errors
    return update
