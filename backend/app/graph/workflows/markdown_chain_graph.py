"""
markdown_chain_graph.py — Linear LangGraph built from declarative agent_steps.

Steps are agent_id strings resolved via Markdown/YAML registry and executed by
the generic markdown_agent_executor.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, StateGraph

from app.agents.shared.markdown_agent_executor import make_markdown_agent_node
from app.agents.system.persist_node import persist_node
from app.state import AgentState


def _read_agent_steps() -> list[str]:
    from app.registry import WORKFLOW_REGISTRY

    wf = WORKFLOW_REGISTRY.get("markdown_chain") or {}
    steps = wf.get("agent_steps") or []
    if isinstance(steps, list) and steps:
        return [str(s) for s in steps]
    return ["research_assistant_md"]


def build_markdown_chain_graph(checkpointer: Optional[Any] = None):
    from app.checkpointer import get_memory_checkpointer

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()
    steps = _read_agent_steps()

    g = StateGraph(AgentState)
    for i, agent_id in enumerate(steps):
        node_name = f"md_{i}_{agent_id}"
        g.add_node(node_name, make_markdown_agent_node(agent_id))
    g.add_node("persist", persist_node)

    g.set_entry_point(f"md_0_{steps[0]}")
    for i in range(len(steps) - 1):
        cur = f"md_{i}_{steps[i]}"
        nxt = f"md_{i + 1}_{steps[i + 1]}"
        g.add_edge(cur, nxt)
    g.add_edge(f"md_{len(steps) - 1}_{steps[-1]}", "persist")
    g.add_edge("persist", END)

    return g.compile(checkpointer=cp)
