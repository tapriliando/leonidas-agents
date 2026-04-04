"""
conditions.py — All conditional edge router functions.

RULE: Every router takes (state: AgentState) and returns a STRING key.
      That key is mapped to the next node name in add_conditional_edges().
      Routers never call other nodes — they only read state and return a key.

HOW TO ADD A NEW ROUTER:
  1. Write a function here: def my_router(state: AgentState) -> str
  2. Use it in a graph file: graph.add_conditional_edges("node", my_router, {...})
  No changes needed anywhere else.

HOW TO ADD A NEW WORKFLOW TO THE PROGRESSION ROUTER:
  Add one line to the mapping dict in the graph that uses workflow_progression_router.
  Example: {"my_new_workflow": "my_new_subgraph_node", ...}
"""

from __future__ import annotations

# AgentState is imported at runtime (not under TYPE_CHECKING) because LangGraph's
# add_conditional_edges calls get_type_hints() on router functions to infer their
# input schema. With from __future__ import annotations, all annotations become
# lazy strings — get_type_hints() must resolve 'AgentState' in this module's globals,
# which requires it to be present at runtime, not just under TYPE_CHECKING.
from app.state import AgentState


# ---------------------------------------------------------------------------
# Safety limits — change these to adjust system behaviour
# ---------------------------------------------------------------------------

MAX_ITERATIONS = 5       # max retry loops within a single workflow
MAX_WORKFLOW_STEPS = 10  # max workflows in a single multi-workflow run


# ---------------------------------------------------------------------------
# 1. Workflow progression router
#    Controls the top-level multi-workflow pipeline.
#    "research → content → publish" runs through this router between workflows.
# ---------------------------------------------------------------------------

def workflow_progression_router(state: AgentState) -> str:
    """
    Decides which workflow subgraph runs next, or "end" when all are done.

    Called by the meta-orchestrator after each workflow completes.
    Reads workflow_plan[current_workflow_index] to get the next workflow_type.

    Example:
      workflow_plan = ["research_intelligence", "content_pipeline", "social_publishing"]
      current_workflow_index = 1
      → returns "content_pipeline"

      current_workflow_index = 3
      → returns "end" (all workflows done)

    To add a new chainable workflow:
      Register it in docs/registry/workflows/ (YAML only).
      Then add it to the mapping dict in base_graph.py where this router is used.
    """
    plan: list[str] = state.get("workflow_plan") or []
    index: int = state.get("current_workflow_index", 0)

    if not plan or index >= len(plan) or index >= MAX_WORKFLOW_STEPS:
        return "end"

    return plan[index]  # e.g. "content_pipeline"


# ---------------------------------------------------------------------------
# 2. Department router
#    Routes to the correct department subgraph for a single workflow.
# ---------------------------------------------------------------------------

def department_router(state: AgentState) -> str:
    """
    Routes to the correct department graph based on state["department"].

    Called after planner_node sets the department.
    Maps to department subgraphs registered in the meta-orchestrator.

    Returns the department string directly — it must match a key in the
    add_conditional_edges() mapping dict in the graph that calls this.

    To add a new department: just handle the new department string in the
    mapping dict. No code change here.
    """
    department = state.get("department")
    if not department:
        return "fail"
    return department  # "analytics" | "content" | "distribution" | "research" | ...


# ---------------------------------------------------------------------------
# 3. Quality gate router
#    Generic pass/fail check used in any workflow with quality thresholds.
# ---------------------------------------------------------------------------

def quality_gate_router(state: AgentState) -> str:
    """
    Routes to "pass" if quality_score meets threshold, otherwise "retry".

    Used in any workflow where output quality must be checked before
    continuing. The threshold is read from constraints.filters or defaults to 0.7.

    Example usage in a graph:
      graph.add_conditional_edges(
          "analytics_node",
          quality_gate_router,
          {"pass": "report_node", "retry": "analytics_node", "fail": END},
      )
    """
    metrics = state.get("metrics") or {}
    quality_score: float = metrics.get("quality_score") or 0.0
    iteration_count: int = state.get("iteration_count", 0)

    # Read threshold from constraints, default to 0.7
    constraints = state.get("constraints") or {}
    filters = constraints.get("filters") or {}
    threshold: float = float(filters.get("quality_threshold", 0.7))

    if quality_score >= threshold:
        return "pass"

    if iteration_count >= MAX_ITERATIONS:
        return "fail"  # exceeded retries — give up

    return "retry"


# ---------------------------------------------------------------------------
# 4. Retry router
#    Generic retry/continue/fail pattern used in any workflow.
# ---------------------------------------------------------------------------

def retry_router(state: AgentState) -> str:
    """
    Routes to "continue" if data is present, "retry" if empty but under limit,
    or "fail" if retries are exhausted.

    Used after fetch nodes that might return empty results.

    Example usage:
      graph.add_conditional_edges(
          "fetch_node",
          retry_router,
          {"continue": "analyze_node", "retry": "fetch_node", "fail": END},
      )
    """
    artifacts = state.get("artifacts") or {}
    workflow_data = artifacts.get("workflow_data") or {}
    iteration_count: int = state.get("iteration_count", 0)

    if workflow_data:
        return "continue"

    if iteration_count >= MAX_ITERATIONS:
        return "fail"

    return "retry"


# ---------------------------------------------------------------------------
# 5. Error check router
#    Routes to "fail" if any errors exist, otherwise "continue".
# ---------------------------------------------------------------------------

def error_check_router(state: AgentState) -> str:
    """
    Routes to "fail" if state["errors"] is non-empty, otherwise "continue".

    Use this after critical nodes where any error should halt the workflow
    instead of proceeding with bad data.
    """
    if state.get("errors"):
        return "fail"
    return "continue"


# ---------------------------------------------------------------------------
# 6. A2A message bus helpers + routers
#    Power the agent-to-agent message dispatch pattern.
# ---------------------------------------------------------------------------

def has_pending_for(messages: list, agent_id: str) -> bool:
    """
    Pure helper — no state access. Returns True if any message in the list
    is addressed to agent_id and still has status="pending".

    Used by message_router and by agents themselves to check their inbox.

    Example:
      has_pending_for(state["messages"], "analytics_agent")  → True / False
    """
    return any(
        m.get("to_agent") == agent_id and m.get("status") == "pending"
        for m in messages
    )


def message_router(state: AgentState) -> str:
    """
    The central A2A dispatch point. Scans state["messages"] for the first
    pending message and returns its to_agent as the routing key.

    The graph maps agent_id strings to node names:
      graph.add_conditional_edges(
          "dispatcher",
          message_router,
          {
              "analytics_agent":    "analytics_node",
              "content_agent":      "content_node",
              "distribution_agent": "distribution_node",
              "end":                END,
              # ← add one line per new agent that can receive messages
          },
      )

    Returns "end" when no pending messages remain — signals that all
    inter-agent work is complete and the workflow can continue.

    RULE: message_router never decides workflow logic. It only reads
    pending message status and returns to_agent. The graph decides what
    to actually do with that agent.
    """
    messages: list = state.get("messages") or []
    for msg in messages:
        if msg.get("status") == "pending":
            return msg["to_agent"]  # e.g. "analytics_agent"
    return "end"


# ---------------------------------------------------------------------------
# 7. Scored items router
#    Generic quality + data check used in any pipeline that produces scored items
#    (lead_gen, talent_pipeline, vendor_scoring, etc.).
# ---------------------------------------------------------------------------

def scored_items_router(state: AgentState) -> str:
    """
    Routes to "pass" if scored_items exist and quality_score meets threshold.

    Stricter than quality_gate_router because it also requires the "scored_items"
    key to be present and non-empty. Used after assigner_agent in any pipeline
    that produces scored/ranked items.

    Thresholds:
      - Presence check:  len(scored_items) > 0
      - Quality check:   metrics.quality_score >= threshold
        (threshold from constraints.filters.quality_threshold, default 0.6)

    Routes:
      "pass"  — scored_items present AND quality_score meets threshold
      "retry" — quality_score below threshold but iterations remain
      "fail"  — no scored_items OR iteration limit exceeded

    Example usage:
      graph.add_conditional_edges(
          "assigner",
          scored_items_router,
          {"pass": "analytics", "retry": "assigner", "fail": END},
      )
    """
    artifacts = state.get("artifacts") or {}
    workflow_data = artifacts.get("workflow_data") or {}
    scored_items: list = workflow_data.get("scored_items") or []

    if not scored_items:
        iteration_count: int = state.get("iteration_count", 0)
        if iteration_count >= MAX_ITERATIONS:
            return "fail"
        return "retry"

    metrics = state.get("metrics") or {}
    quality_score: float = metrics.get("quality_score") or 0.0
    iteration_count = state.get("iteration_count", 0)

    constraints = state.get("constraints") or {}
    filters = constraints.get("filters") or {}
    threshold: float = float(filters.get("quality_threshold", 0.6))

    if quality_score >= threshold:
        return "pass"

    if iteration_count >= MAX_ITERATIONS:
        return "fail"

    return "retry"


def approval_after_gate_router(state: AgentState) -> str:
    """
    Routes after approval_gate completes (no interrupt pending).

    - not_required / approved → persist
    - rejected → heygen (regenerate with approval_feedback in workflow_data)
    """
    approval = state.get("approval") or {}
    st = str(approval.get("status") or "")
    if st in ("approved", "not_required"):
        return "persist"
    if st == "rejected":
        return "heygen"
    return "persist"


def spawn_router(state: AgentState) -> str:
    """
    Detects a spawn request in state["spawn"] and routes to the named agent.

    When a node calls request_spawn(), it sets state["spawn"] = SpawnRequest.
    This router picks it up and routes to the spawn target.

    After the spawned agent finishes, it should clear spawn by returning
    {"spawn": None} so this router doesn't re-trigger.

    Example wiring in a graph:
      graph.add_conditional_edges(
          "content_agent",
          spawn_router,
          {
              "research_agent": "research_node",  # spawned agent nodes
              "continue":       "next_node",       # no spawn — continue normally
          },
      )
    """
    spawn = state.get("spawn")
    if spawn and spawn.get("agent"):
        return spawn["agent"]  # e.g. "research_agent"
    return "continue"
