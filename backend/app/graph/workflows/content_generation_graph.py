"""
content_generation_graph.py — HeyGen (or future providers) without prior research.

For research → video in one user turn, use a multi-workflow plan from the planner, e.g.:
  workflow_plan: ["research_intelligence", "content_generation"]

This subgraph only runs the generation + report + persist leg. It expects either:
  - workflow_data.content_prompt set by an upstream workflow, or
  - workflow_data.summary.overview, or
  - user_query

Phase 6: optional human approval after report (interrupt) with retry loop back to heygen.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import StateGraph, END

from app.state import AgentState
from app.graph.conditions import approval_after_gate_router
from app.agents.content_generation.heygen_video_agent import heygen_video_agent
from app.agents.system.report_node import report_node
from app.agents.system.persist_node import persist_node
from app.agents.system.approval_gate import approval_gate


def build_content_generation_graph(checkpointer: Optional[Any] = None):
    from app.checkpointer import get_memory_checkpointer

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()

    g = StateGraph(AgentState)
    g.add_node("heygen", heygen_video_agent)
    g.add_node("report", report_node)
    g.add_node("approval_gate", approval_gate)
    g.add_node("persist", persist_node)
    g.set_entry_point("heygen")
    g.add_edge("heygen", "report")
    g.add_edge("report", "approval_gate")
    g.add_conditional_edges(
        "approval_gate",
        approval_after_gate_router,
        {"persist": "persist", "heygen": "heygen"},
    )
    g.add_edge("persist", END)
    return g.compile(checkpointer=cp)
