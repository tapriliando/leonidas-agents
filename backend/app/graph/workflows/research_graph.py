"""
research_graph.py — Research Intelligence workflow.

WHAT THIS FILE IS:
  Pure graph wiring for the research pipeline. Nodes do the actual work.

GRAPH STRUCTURE:
  research_node → [retry_router] → suggest_node → report_node → persist_node
       ↓retry                          (optional recommendations based on findings)
  research_node
       ↓fail
      END

  research_node does both web-fetch AND LLM synthesis in one step, so
  quality_gate_router is not needed here — confidence is checked inside
  research_node itself, and retry_router handles empty search results.

HOW TO USE:
  Send any informational or trend-research query:
    "what trending news is good for TikTok content?"
    "research the latest AI model releases"
    "find competitor pricing strategies in SaaS"

  The intent_node routes to research_intelligence when the query is
  research/information-retrieval in nature and no domain workflow matches better.

HOW TO CREATE ANOTHER RESEARCH-TYPE WORKFLOW:
  1. Register docs/registry/workflows/<new_type>.yaml with department: research
  2. Copy this file → research/<new_type>_graph.py
  3. Add one line to base_graph.py WORKFLOW_SUBGRAPHS
  Zero node code changes.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import StateGraph, END

from app.state import AgentState
from app.graph.conditions import retry_router
from app.agents.research.research_node import research_node
from app.agents.analytics.suggest_node import suggest_node
from app.agents.system.report_node import report_node
from app.agents.system.persist_node import persist_node


def build_research_graph(checkpointer: Optional[Any] = None):
    """
    Builds and compiles the research_intelligence subgraph.

    Called by base_graph.py when registering WORKFLOW_SUBGRAPHS.
    """
    from app.checkpointer import get_memory_checkpointer

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()

    g = StateGraph(AgentState)

    # ── Nodes ──────────────────────────────────────────────────────────────
    g.add_node("research", research_node)
    g.add_node("suggest",  suggest_node)
    g.add_node("report",   report_node)
    g.add_node("persist",  persist_node)

    # ── Entry point ─────────────────────────────────────────────────────────
    g.set_entry_point("research")

    # ── research → retry_router ──────────────────────────────────────────────
    # "continue" when web results were fetched and summary was written
    # "retry"    when the search returned nothing (increments iteration_count)
    # "fail"     when retry limit exceeded
    g.add_conditional_edges(
        "research",
        retry_router,
        {"continue": "suggest", "retry": "research", "fail": END},
    )

    # ── Linear tail: suggest → report → persist ──────────────────────────────
    g.add_edge("suggest", "report")
    g.add_edge("report",  "persist")

    return g.compile(checkpointer=cp)
