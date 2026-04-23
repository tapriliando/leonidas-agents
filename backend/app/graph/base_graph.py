"""
base_graph.py — Meta-orchestrator: runs multiple workflows in one graph.invoke().

HOW IT WORKS:
  1. User sends: "research today's news, create a video, post to TikTok"
  2. intent_node + planner_node parse this into:
       workflow_plan = ["research_intelligence", "content_pipeline", "social_publishing"]
       current_workflow_index = 0
  3. The meta-orchestrator routes to each workflow subgraph in sequence.
  4. After each subgraph, workflow_transition_node adds 1 to current_workflow_index.
  5. workflow_progression_router reads the new index and routes to the next subgraph.
  6. When index >= len(workflow_plan), it routes to END.

STATE HANDOFF BETWEEN WORKFLOWS:
  artifacts.workflow_data accumulates across all workflows:
    After research:  {"summary": "...", "raw_sources": [...]}
    After content:   {"summary": "...", "script": "...", "video_url": "..."}
    After publish:   {"summary": "...", "script": "...", "video_url": "...", "publish_results": {...}}

  Each workflow reads from the shared workflow_data — content_pipeline reads
  research's "summary", social_publishing reads content_pipeline's "video_url".
  No extra wiring needed: it's just a growing dict.

HOW TO ADD A NEW WORKFLOW TO THE CHAIN:
  1. Register it in docs/registry/workflows/<name>.yaml  (YAML only — no code)
  2. Build the subgraph in graph/workflows/<name>.py     (developer)
  3. Add one line to WORKFLOW_SUBGRAPHS dict below       (developer)
  That's it. The planner automatically knows the new workflow exists
  because it reads the registry at runtime via registry.py.

PHASE NOTE:
  Subgraph implementations live in graph/workflows/ and are added in Phase 3+.
  This file defines the orchestration contract — the skeleton everything plugs into.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Workflow subgraph registry
# ---------------------------------------------------------------------------
# Maps workflow_type string → compiled subgraph or builder function.
#
# Phase 0/1: stubs only — real subgraphs are wired in Phase 3+.
# To add a new workflow: import its builder and add one entry here.
#
# Example (Phase 3+):
#   from app.graph.workflows.research_intelligence import build_research_graph
#   from app.graph.workflows.content_pipeline import build_content_graph
#   from app.graph.workflows.social_publishing import build_social_graph
#
#   WORKFLOW_SUBGRAPHS = {
#       "research_intelligence": build_research_graph,
#       "content_pipeline":      build_content_graph,
#       "social_publishing":     build_social_graph,
#       "lead_gen":              build_lead_gen_graph,
#       "supply_chain":          build_supply_chain_graph,
#       # ← add new workflows here, one line each
#   }

# WORKFLOW_SUBGRAPHS starts as None (sentinel = "not yet initialized").
# build_meta_graph() lazy-imports the workflow builders on first call so that
# importing this module never pulls in langgraph at module load time.
#
# This lets tests do:
#   import app.graph.base_graph as bgm   # safe — no langgraph import yet
#   bgm.WORKFLOW_SUBGRAPHS = {}           # override before building the graph
#   bgm.build_meta_graph()               # lazy init skipped; uses empty dict
#
# To add a new workflow (Phase 4+): add one entry inside _init_workflow_subgraphs().
WORKFLOW_SUBGRAPHS: dict | None = None


def _init_workflow_subgraphs() -> dict:
    """
    Lazy-loads workflow subgraph builders on first call.

    Returns the populated WORKFLOW_SUBGRAPHS dict. Skipped when the module-level
    variable is already set (either by a previous call or by test override).
    """
    global WORKFLOW_SUBGRAPHS
    if WORKFLOW_SUBGRAPHS is not None:
        return WORKFLOW_SUBGRAPHS  # already initialized or overridden by test

    from app.graph.workflows.complaint_graph import build_complaint_graph
    from app.graph.workflows.lead_graph import build_lead_graph
    from app.graph.workflows.markdown_chain_graph import build_markdown_chain_graph
    from app.graph.workflows.research_graph import build_research_graph
    from app.graph.workflows.content_generation_graph import build_content_generation_graph

    WORKFLOW_SUBGRAPHS = {
        "complaint_analysis":    build_complaint_graph,
        "lead_gen":              build_lead_graph,
        "research_intelligence": build_research_graph,
        "content_generation":    build_content_generation_graph,
        "markdown_chain":        build_markdown_chain_graph,
        # future workflows: one line each — no other changes needed
    }
    return WORKFLOW_SUBGRAPHS


# ---------------------------------------------------------------------------
# Workflow transition node
# ---------------------------------------------------------------------------

def workflow_transition_node(state: dict) -> dict:
    """
    Called after each workflow subgraph completes.

    Advances current_workflow_index by 1 (via operator.add reducer in state.py).
    The workflow_progression_router then reads the new index and decides
    which workflow runs next, or routes to END if the plan is complete.

    This is the "next chapter" signal — it never touches workflow_data
    or any other content field. It only advances the index counter.

    Node contract:
      Reads:  nothing (just signals completion)
      Writes: {"current_workflow_index": 1}  ← adds 1 via operator.add
    """
    return {"current_workflow_index": 1}


# ---------------------------------------------------------------------------
# Meta-orchestrator graph builder
# ---------------------------------------------------------------------------

def build_meta_graph(checkpointer=None):
    """
    Builds and compiles the meta-orchestrator graph.

    The compiled graph handles:
      - Single workflow:   workflow_plan = ["lead_gen"]
      - Multi-workflow:    workflow_plan = ["research_intelligence", "content_pipeline", "social_publishing"]
      - Any combination registered in WORKFLOW_SUBGRAPHS

    HOW TO ADD A NEW WORKFLOW (Phase 4+):
      1. Import its builder function above in WORKFLOW_SUBGRAPHS
      2. Add one entry: "my_workflow": build_my_graph
      That's it — this function picks it up automatically.

    PHASE NOTE:
      WORKFLOW_SUBGRAPHS is empty in Phase 3. The graph still compiles and
      intent_node + planner_node work correctly. Subgraph nodes are wired in
      Phase 4+ as each department workflow is implemented.
    """
    from langgraph.graph import StateGraph, END

    from app.state import AgentState
    from app.checkpointer import get_memory_checkpointer
    from app.graph.conditions import workflow_progression_router
    from app.agents.shared.intent_node import intent_node
    from app.agents.shared.planner_node import planner_node
    from app.agents.shared.direct_answer_node import direct_answer_node

    cp = checkpointer if checkpointer is not None else get_memory_checkpointer()

    # Resolve subgraphs — lazy-loads on first real call; uses override in tests.
    subgraphs = _init_workflow_subgraphs()

    g = StateGraph(AgentState)

    # --- Entry nodes: intent classification → execution planning ---
    g.add_node("intent", intent_node)
    g.add_node("planner", planner_node)
    g.add_node("transition", workflow_transition_node)

    # --- Direct-answer terminal node ---
    # Handles queries that don't route to a workflow subgraph.
    # Sets status = "completed" and writes artifacts.report, then routes to END.
    g.add_node("direct_answer", direct_answer_node)
    g.add_edge("direct_answer", END)

    # --- Register one node per compiled workflow subgraph ---
    # Each builder() call returns a compiled LangGraph subgraph.
    for wf_type, builder in subgraphs.items():
        g.add_node(wf_type, builder(checkpointer=cp))

    # --- Fixed edge: intent always flows to planner ---
    g.add_edge("intent", "planner")

    # --- After planner: route to first workflow in the plan ---
    # workflow_progression_router reads workflow_plan[current_workflow_index].
    # Returns the workflow_type string that maps to the subgraph node name,
    # or "end" when the plan is complete or empty.
    #
    # "direct_answer" routes to direct_answer_node (not directly to END) so the
    # LLM can answer the query and set status = "completed" properly.
    progression_mapping = {wf_type: wf_type for wf_type in subgraphs}
    progression_mapping["direct_answer"] = "direct_answer"
    progression_mapping["end"] = END

    g.add_conditional_edges("planner", workflow_progression_router, progression_mapping)

    # --- After each workflow subgraph: advance to the next ---
    for wf_type in subgraphs:
        g.add_edge(wf_type, "transition")

    # --- After transition: route to next workflow, direct_answer, or END ---
    g.add_conditional_edges("transition", workflow_progression_router, progression_mapping)

    g.set_entry_point("intent")
    return g.compile(checkpointer=cp)
