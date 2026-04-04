"""
complaint_graph.py — First example instantiation of the analytics pipeline.

WHAT THIS FILE IS:
  Pure graph wiring — zero business logic. All domain behavior lives in the
  shared analytics nodes (fetch_node, summarize_node, suggest_node) and
  system nodes (report_node, persist_node).

  This file only declares which nodes run, in what order, and what conditional
  edges connect them.

WHAT THIS FILE IS NOT:
  - It does not know anything about complaints as a domain
  - It does not contain any LLM calls, MCP calls, or data transformations
  - It is not the only place these nodes are used

HOW TO CREATE A NEW ANALYTICS-TYPE WORKFLOW:
  1. Register docs/registry/workflows/<new_type>.yaml  (YAML only)
  2. Copy this file → graph/workflows/<new_type>_graph.py
  3. Rename `build_complaint_graph` → `build_<new_type>_graph`
  4. Add one line to base_graph.py WORKFLOW_SUBGRAPHS
  That's it — all analytics nodes are shared, zero changes to node code.

GRAPH STRUCTURE:
  fetch → [retry_router] → summarize → [quality_gate_router] → suggest → report → persist
                ↓retry                        ↓retry
              fetch                        summarize
                ↓fail                        ↓fail
               END                           END
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import StateGraph, END

from app.state import AgentState
from app.graph.conditions import retry_router, quality_gate_router
from app.agents.analytics.fetch_node import fetch_node
from app.agents.analytics.summarize_node import summarize_node
from app.agents.analytics.suggest_node import suggest_node
from app.agents.system.report_node import report_node
from app.agents.system.persist_node import persist_node


def build_complaint_graph(checkpointer: Optional[Any] = None):
    """
    Builds and compiles the complaint_analysis subgraph.

    Called by base_graph.py when registering WORKFLOW_SUBGRAPHS.
    Returns a compiled LangGraph graph ready to use as a node in the meta-graph.
    """
    from app.checkpointer import get_memory_checkpointer

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()

    g = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    g.add_node("fetch",     fetch_node)
    g.add_node("summarize", summarize_node)
    g.add_node("suggest",   suggest_node)
    g.add_node("report",    report_node)
    g.add_node("persist",   persist_node)

    # ── Entry point ─────────────────────────────────────────────────────────
    g.set_entry_point("fetch")

    # ── fetch → retry_router ────────────────────────────────────────────────
    # "continue" when items were fetched successfully
    # "retry"    when fetch returned empty (increments iteration_count)
    # "fail"     when retry limit exceeded
    g.add_conditional_edges(
        "fetch",
        retry_router,
        {"continue": "summarize", "retry": "fetch", "fail": END},
    )

    # ── summarize → quality_gate_router ─────────────────────────────────────
    # "pass"  when quality_score (= LLM confidence) meets the threshold
    # "retry" when quality is low but retries remain
    # "fail"  when retry limit exceeded
    g.add_conditional_edges(
        "summarize",
        quality_gate_router,
        {"pass": "suggest", "retry": "summarize", "fail": END},
    )

    # ── Linear tail: suggest → report → persist ─────────────────────────────
    g.add_edge("suggest", "report")
    g.add_edge("report",  "persist")

    return g.compile(checkpointer=cp)
