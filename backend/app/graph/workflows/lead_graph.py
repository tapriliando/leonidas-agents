"""
lead_graph.py — First example instantiation of the distribution pipeline.

WHAT THIS FILE IS:
  Pure graph wiring — zero business logic. All domain behavior lives in the
  shared distribution nodes (scraper_agent, enrichment_agent, assigner_agent),
  analytics node (analytics_agent), and system nodes (report_node, persist_node).

  This file only declares which nodes run, in what order, and what conditional
  edges connect them.

WHAT THIS FILE IS NOT:
  - It does not know anything about leads as a domain
  - It does not contain any LLM calls, MCP calls, or data transformations
  - It is not the only place these nodes can be used

HOW TO CREATE A NEW DISTRIBUTION-TYPE WORKFLOW:
  1. Register docs/registry/workflows/<new_type>.yaml  (YAML only)
  2. Copy this file → graph/workflows/<new_type>_graph.py
  3. Rename `build_lead_graph` → `build_<new_type>_graph`
  4. Add one line to base_graph.py WORKFLOW_SUBGRAPHS
  That's it — all distribution nodes are shared, zero changes to node code.

GRAPH STRUCTURE:
  scraper → [retry_router] → enrichment → assigner → [scored_items_router] → analytics → report → persist
               ↓retry                                       ↓retry
             scraper                                      assigner
               ↓fail                                       ↓fail
              END                                          END
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import StateGraph, END

from app.state import AgentState
from app.graph.conditions import retry_router, scored_items_router
from app.agents.distribution.scraper_agent import scraper_agent
from app.agents.distribution.enrichment_agent import enrichment_agent
from app.agents.distribution.assigner_agent import assigner_agent
from app.agents.analytics.analytics_agent import analytics_agent
from app.agents.system.report_node import report_node
from app.agents.system.persist_node import persist_node


def build_lead_graph(checkpointer: Optional[Any] = None):
    """
    Builds and compiles the lead_gen subgraph.

    Called by base_graph.py when registering WORKFLOW_SUBGRAPHS.
    Returns a compiled LangGraph graph ready to use as a node in the meta-graph.
    """
    from app.checkpointer import get_memory_checkpointer

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()

    g = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    g.add_node("scraper",    scraper_agent)
    g.add_node("enrichment", enrichment_agent)
    g.add_node("assigner",   assigner_agent)
    g.add_node("analytics",  analytics_agent)
    g.add_node("report",     report_node)
    g.add_node("persist",    persist_node)

    # ── Entry point ─────────────────────────────────────────────────────────
    g.set_entry_point("scraper")

    # ── scraper → retry_router ───────────────────────────────────────────────
    # "continue" when items were fetched from the external source
    # "retry"    when scraper returned empty (increments iteration_count)
    # "fail"     when retry limit exceeded
    g.add_conditional_edges(
        "scraper",
        retry_router,
        {"continue": "enrichment", "retry": "scraper", "fail": END},
    )

    # ── enrichment is always safe to proceed ────────────────────────────────
    g.add_edge("enrichment", "assigner")

    # ── assigner → scored_items_router ─────────────────────────────────────
    # "pass"  when scored_items exist and quality_score meets threshold
    # "retry" when quality is low but retries remain
    # "fail"  when retry limit exceeded or no scored_items
    g.add_conditional_edges(
        "assigner",
        scored_items_router,
        {"pass": "analytics", "retry": "assigner", "fail": END},
    )

    # ── Linear tail: analytics → report → persist ───────────────────────────
    g.add_edge("analytics", "report")
    g.add_edge("report",    "persist")

    return g.compile(checkpointer=cp)
