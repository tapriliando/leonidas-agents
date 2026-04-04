"""
state.py — Generic runtime memory for any workflow type.

DESIGN PHILOSOPHY:
  This AgentState is WORKFLOW-AGNOSTIC. The same schema powers every use case.
  You never need to change this file to add a new workflow — you only add nodes.

  Examples of what can run on this system without any schema change:
    - Lead generation       → workflow_type = "lead_gen"
    - Supply chain          → workflow_type = "supply_chain"
    - Marketing campaigns   → workflow_type = "marketing"
    - Agriculture ops       → workflow_type = "agriculture"
    - Customer analytics    → workflow_type = "customer_analytics"
    - Content publishing    → workflow_type = "content_pipeline"
    - Document processing   → workflow_type = "document_processing"
    - HR & recruitment      → workflow_type = "hr_pipeline"

  To add a new workflow:
    1. Register a workflow_type string (any string — no enum, no schema change)
    2. Write agent nodes that read/write artifacts["workflow_data"]
    3. Add a graph file under graph/workflows/<name>.py
    4. Register tools in mcp-server/registry.yaml
    That's it. AgentState never changes.

MEMORY LAYER REMINDER:
  AgentState  = RAM        → lives only during one graph.invoke() call
  Redis       = Working    → hot cache, survives across requests, TTL-based
  Supabase    = Database   → permanent history, feedback loop, long-term knowledge

  Rule: if data needs to survive after invoke() → write it to Supabase in a
        persist_node at the end of the graph. Never store long-term data here.

NODE CONTRACT (mandatory for every agent):
  def my_node(state: AgentState) -> dict:
      ...
      return {"artifacts": updated_artifacts}  # return ONLY what changed

  LangGraph merges the returned partial dict into the current state.
  Nodes that don't touch a field simply omit it from the return dict.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional
from typing_extensions import TypedDict
import operator


# ---------------------------------------------------------------------------
# Sub-structs — typed dicts for nested state fields
# All sub-structs are intentionally generic.
# ---------------------------------------------------------------------------

class Constraints(TypedDict):
    """
    Workflow constraints parsed from user_query by intent_node.

    Intentionally generic — `filters` holds any workflow-specific parameters.
    intent_node fills this from natural language; agents read it to scope work.

    Examples by workflow:
      "find 50 leads in Jakarta"       → limit=50, filters={"location": "Jakarta"}
      "analyze top 20 suppliers"       → limit=20, filters={"tier": "A"}
      "summarize last 10 complaints"   → limit=10, filters={"category": "billing"}
      "audit Q1 crop reports"          → limit=None, filters={"period": "Q1"}
    """
    limit: Optional[int]              # how many items to process (None = no limit)
    require_approval: bool            # human gate before final action (Phase 6)
    filters: Optional[dict[str, Any]] # all workflow-specific filters go here


class Artifacts(TypedDict):
    """
    Generic artifact store. Every workflow writes its outputs here.

    RULE: All nodes write into workflow_data — do NOT add new top-level
    fields to this TypedDict for each new workflow. Use workflow_data instead.

    Why? Because adding fields here would require schema migration every time
    a new client use case comes in. workflow_data is the escape hatch that
    keeps the schema stable.

    workflow_data examples by workflow type:
      lead_gen:
        {
          "leads": [...],
          "lead_scores": {"lead_001": 0.87},
          "outreach_drafts": [...]
        }

      supply_chain:
        {
          "suppliers": [...],
          "risk_map": {"supplier_A": "high"},
          "recommendations": [...]
        }

      marketing:
        {
          "campaigns": [...],
          "ab_test_results": {"variant_B_ctr": 0.12},
          "targeting_segments": [...]
        }

      agriculture:
        {
          "field_reports": [...],
          "soil_analysis": {...},
          "harvest_schedule": [...]
        }

      content_pipeline:
        {
          "script": "...",
          "video_url": "https://...",
          "caption": "..."
        }

    report: final human-readable output (optional, any workflow can set this).
    """
    workflow_data: Optional[dict]    # primary: structured output for any workflow
    report: Optional[str]            # final: human-readable summary or report


class Metrics(TypedDict):
    """
    Generic quality signals produced during the run.

    After the run, persist_node copies these to Supabase so future runs can
    compare current quality against historical benchmarks (feedback loop).

    Nodes write only the fields that apply to their workflow — unused fields
    stay None and are ignored.

    custom: use this for any workflow-specific metrics that don't fit the
            three typed fields above, without changing this schema.

    Examples:
      lead_gen:       item_count=50, quality_score=0.82 (avg lead score)
      supply_chain:   item_count=120, quality_score=0.91, custom={"high_risk": 7}
      marketing:      item_count=5, confidence=0.76, custom={"avg_ctr": 0.09}
    """
    item_count: Optional[int]         # how many items were processed
    quality_score: Optional[float]    # 0.0 → 1.0, workflow-defined quality
    confidence: Optional[float]       # model confidence on the primary output
    custom: Optional[dict[str, Any]]  # any workflow-specific metrics


class ApprovalGate(TypedDict):
    """
    Human-in-the-loop pause/resume gate (Phase 6).

    Workflow-agnostic: any workflow can require approval before a final action
    (e.g. before sending outreach emails, before publishing content, before
    executing a supply chain reorder).

    When status == "pending", the graph halts and waits for a
    POST /workflows/{run_id}/approve API call to resume.
    """
    required: bool
    status: str               # "not_required" | "pending" | "approved" | "rejected"
    approved_by: Optional[str]
    approved_at: Optional[str]
    rejection_reason: Optional[str]


class Message(TypedDict):
    """
    A single agent-to-agent message on the shared message bus.

    Agents NEVER call each other directly. Instead they write a Message into
    state["messages"] and the message_router in conditions.py dispatches to
    the target agent based on pending messages.

    Lifecycle:
      content_agent sends → status="pending"
      analytics_agent picks up → status="processing"
      analytics_agent finishes → status="done",  result={...}
      (or fails)              → status="failed", result={"error": "..."}

    provenance distinguishes internal routing from user-initiated messages:
      "inter_session" — sent by one agent to another (A2A)
      "user"          — originated from the end user
      "scheduler"     — triggered by a cron/heartbeat run

    from_agent / to_agent map directly to agent_id strings in the registry.
    """
    id: str                           # unique message ID (uuid4)
    from_agent: str                   # sender agent_id, e.g. "content_agent"
    to_agent: str                     # target agent_id, e.g. "analytics_agent"
    task: str                         # what the target must do, e.g. "analyze_video"
    payload: dict                     # input data for the task
    status: str                       # "pending" | "processing" | "done" | "failed"
    result: Optional[dict]            # filled by target agent when done/failed
    provenance: str                   # "inter_session" | "user" | "scheduler"
    created_at: str                   # ISO timestamp when message was created


class SpawnRequest(TypedDict):
    """
    Requests that the graph create an isolated sub-agent branch.

    When a node sets state["spawn"], the spawn_router in conditions.py
    routes to the named agent node. After that agent completes, the result
    is merged back into state["messages"] as a "done" message.

    This is the "background work" pattern — the spawning agent continues
    with its current task while the spawned agent runs in a separate branch.

    In production (Phase 4+) this maps to LangGraph's Send API which can
    fan-out to multiple sub-agents in parallel.
    """
    agent: str          # agent_id to spawn, e.g. "research_agent"
    task: str           # task description for the spawned agent
    payload: dict       # input data
    run_id: Optional[str]  # filled by the graph when spawn is handled


class MemoryContext(TypedDict):
    """
    A small window of mid/long-term memory injected at run START.

    Fetched from Redis/Supabase BEFORE graph.invoke() — gives agents
    historical awareness without bloating state with full DB rows.

    Rule: agents READ from context, they NEVER write to it.
          Writing back to Supabase happens in persist_node AFTER the run.

    past_run_summaries: text summaries of recent runs (trend awareness)
    benchmark_score:    historical quality baseline for comparing this run
    user_preferences:   user profile data from Supabase
    domain_context:     workflow-specific knowledge — e.g. for supply_chain:
                        {"approved_suppliers": [...], "blocked_countries": [...]}
                        for lead_gen: {"icp": {...}, "blacklist": [...]}
                        for agriculture: {"crop_calendar": {...}, "region": "..."}
    """
    past_run_summaries: Optional[list[str]]
    benchmark_score: Optional[float]
    user_preferences: Optional[dict[str, Any]]
    domain_context: Optional[dict[str, Any]]


# ---------------------------------------------------------------------------
# Reducers — control HOW partial state updates from nodes get merged
# ---------------------------------------------------------------------------

def append_errors(existing: list, new: list) -> list:
    """
    Errors accumulate across all nodes — we never overwrite the error list.

    Without this reducer:
      - node_A sets errors=["db timeout"]
      - node_B sets errors=["parse error"]
      → node B's update REPLACES node A's (you lose the first error)

    With this reducer:
      → both errors are preserved: ["db timeout", "parse error"]

    Usage in any node:
      return {"errors": ["my_node: description of what went wrong"]}
    """
    return existing + new


def append_messages(existing: list, new: list) -> list:
    """
    New messages are appended to the bus — we never lose a message in flight.

    Same pattern as append_errors but for the A2A message bus.

    Without this reducer: if content_agent and analytics_agent both send
    messages in the same graph step, one would overwrite the other.

    With this reducer: both messages are preserved on the bus.

    Usage — sending a new message from a node:
      return {"messages": [new_message_dict]}   ← only the NEW one in the list
      LangGraph appends it to existing messages automatically.

    Usage — updating a message (mark done/failed):
    DO NOT use this reducer for status updates. Use message_bus.mark_done()
    which returns {"messages": full_updated_list} replacing via keep_latest-style.
    For status updates, agents write the complete updated messages list directly
    to state (the full replacement bypasses the append reducer because
    mark_done returns the entire list, not just the changed message).

    See agents/shared/message_bus.py for the helper functions.
    """
    return existing + new


def keep_latest(existing: Any, new: Any) -> Any:
    """
    Latest write wins — but ONLY if the new value is not None.

    Without this: a node returning {"metrics": None} would wipe good data.
    With this: None updates are silently ignored, preserving the existing value.

    This means nodes can safely return {} (nothing) without touching fields
    they didn't work on. It also means intent_node cannot accidentally
    overwrite analytics results by returning {"metrics": None}.
    """
    return new if new is not None else existing


# ---------------------------------------------------------------------------
# AgentState — the complete runtime memory for one graph run
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """
    The universal state bus for every workflow type.

    LAYOUT:
      [1] Request layer   — what the user asked for and what type of work to do
      [2] Execution layer — plan + intermediate artifacts
      [3] Quality layer   — metrics and approval gate
      [4] System layer    — status, iteration tracking, errors, identity
      [5] Memory layer    — injected historical context (read-only for nodes)

    NODE CONTRACT — every node follows this exact signature:
      def my_node(state: AgentState) -> dict:
          data = state["artifacts"]["workflow_data"]   # read
          result = do_work(data)
          updated = {**state["artifacts"], "workflow_data": {**data, "my_key": result}}
          return {"artifacts": updated}                # return only what changed

    ADDING A NEW WORKFLOW — no schema changes needed:
      1. intent_node sets workflow_type to your new string
      2. planner_node sets department to the responsible department
      3. Your new agent nodes write outputs into artifacts["workflow_data"]
      4. conditions.py adds routing logic for your new workflow_type
    """

    # ------------------------------------------------------------------
    # [1] Request layer — set by intent_node + planner_node at run start
    # ------------------------------------------------------------------
    user_query: str
    # Raw natural language input. E.g. "find 50 suppliers in Surabaya at risk"

    goal: Annotated[Optional[str], keep_latest]
    # Structured goal extracted from user_query by intent_node.
    # E.g. "find_suppliers", "analyze_leads", "generate_content"

    workflow_type: Annotated[Optional[str], keep_latest]
    # The workflow category — an open-ended string, not an enum.
    # intent_node sets this. planner_node and conditional edges read it.
    # Any string is valid: "lead_gen", "supply_chain", "marketing",
    # "agriculture", "hr_pipeline", "document_processing", etc.
    # To add a new workflow: just use a new string — no code change here.

    department: Annotated[Optional[str], keep_latest]
    # Which department runs this workflow — set by planner_node.
    # Maps to: agents/analytics/, agents/distribution/, agents/content/
    # Also an open-ended string. Add new departments by adding new agent folders.
    # E.g. "analytics", "distribution", "content", "operations", "research"

    constraints: Annotated[Optional[Constraints], keep_latest]
    # Parsed scope and filters from user_query. Agents read this to know
    # what limits and filters to apply during their work.

    # ------------------------------------------------------------------
    # [2] Execution layer — filled progressively as nodes run
    # ------------------------------------------------------------------
    workflow_plan: Annotated[Optional[list[str]], keep_latest]
    # Ordered list of workflow_types to execute in sequence.
    # Set by planner_node when user intent spans multiple workflows.
    #
    # Single workflow:   ["lead_gen"]
    # Multi-workflow:    ["research_intelligence", "content_pipeline", "social_publishing"]
    #
    # The meta-orchestrator reads this to know which subgraph runs next.
    # Adding a new chainable workflow = register it in the registry YAML.
    # No schema change here — it's just a list of strings.

    current_workflow_index: Annotated[int, operator.add]
    # Tracks which workflow in workflow_plan is currently active.
    # Uses operator.add: each workflow_transition_node returns
    #   {"current_workflow_index": 1}
    # which ADDS 1, advancing to the next workflow in the plan.
    #
    # Example sequence for "research → content → publish":
    #   index=0 → runs research_intelligence subgraph
    #   index=1 → runs content_pipeline subgraph
    #   index=2 → runs social_publishing subgraph
    #   index=3 → len(plan) reached → route to END
    #
    # This uses the same reducer pattern as iteration_count.
    # operator.add is the key: it prevents overwriting, only incrementing.

    plan: Annotated[Optional[list[str]], keep_latest]
    # Ordered list of node names within the CURRENT workflow.
    # Informational / for logging — graph structure is the real execution order.
    # E.g. ["fetch_node", "score_node", "draft_node", "persist_node"]

    artifacts: Annotated[Optional[Artifacts], keep_latest]
    # The data pipeline — shared across ALL workflows in a multi-workflow run.
    # workflow_data accumulates as each workflow adds its output:
    #   After research:  {"summary": "...", "raw_sources": [...]}
    #   After content:   {"summary": "...", "raw_sources": [...], "script": "...", "video_url": "..."}
    #   After publish:   {"summary": "...", ..., "publish_results": {...}}
    # This natural accumulation is how workflows hand off data to each other.

    # ------------------------------------------------------------------
    # [3] Quality layer
    # ------------------------------------------------------------------
    metrics: Annotated[Optional[Metrics], keep_latest]
    # Quality signals produced during the run.
    # After the run, persist_node writes these to Supabase for historical
    # benchmarking and feedback loops.

    approval: Annotated[Optional[ApprovalGate], keep_latest]
    # Human-in-the-loop gate (Phase 6). When required=True and the workflow
    # reaches a decision point, it pauses here until the user approves.

    # ------------------------------------------------------------------
    # [4] System layer — managed by the graph infrastructure
    # ------------------------------------------------------------------
    status: Annotated[str, keep_latest]
    # "running" | "paused_for_approval" | "completed" | "failed"

    iteration_count: Annotated[int, operator.add]
    # Loop counter. Each retry node returns {"iteration_count": 1} to ADD 1.
    # Conditional edges check: if state["iteration_count"] >= MAX → fail.
    # operator.add means nodes increment, not overwrite. Starting at 0
    # ensures the first iteration sets count to 1 (0 + 1).

    next_node: Annotated[Optional[str], keep_latest]
    # Optional routing hint from a node. Conditional edge functions CAN
    # read this, but the graph conditions are always the final authority.

    errors: Annotated[list[str], append_errors]
    # Accumulated error messages from all nodes. Format: "node_name: detail"
    # Multiple nodes can fail — all errors are preserved via append_errors.

    run_id: str
    # UUID generated per run. Used as: LangGraph thread_id,
    # API workflow identifier, and Supabase row key for persistence.

    user_id: Annotated[Optional[str], keep_latest]
    # Optional end-user id (API / scheduler). Threaded into persist_node for
    # Supabase workflow_runs.user_id and memory loader queries. Nodes do not set this.

    # ------------------------------------------------------------------
    # [5] Memory layer — injected BEFORE graph starts, never written by nodes
    # ------------------------------------------------------------------
    context: Annotated[Optional[MemoryContext], keep_latest]
    # Historical context from Redis/Supabase, injected by the API route handler
    # before graph.invoke(). Gives agents trend awareness and domain knowledge.
    # Nodes READ this — they never write new data into it.
    # Writing back to storage happens in persist_node after the run ends.

    # ------------------------------------------------------------------
    # [6] Message bus — A2A communication layer
    # ------------------------------------------------------------------
    messages: Annotated[list, append_messages]
    # The agent-to-agent message bus. Agents NEVER call each other directly.
    # Instead, agent A writes a Message here; message_router reads pending
    # messages and dispatches to the correct agent node.
    #
    # How to SEND a message (from any agent node):
    #   from app.agents.shared.message_bus import send_message
    #   return send_message(state, from_agent="me", to_agent="analytics_agent",
    #                       task="analyze_video", payload={"url": "..."})
    #
    # How to RECEIVE + COMPLETE a message (inside the target agent node):
    #   from app.agents.shared.message_bus import get_pending, mark_done
    #   pending = get_pending(state, for_agent="analytics_agent")
    #   result = do_work(pending[0]["payload"])
    #   return mark_done(state, msg_id=pending[0]["id"], result=result)
    #
    # Starts as [] — no messages in flight at run start.
    # Uses append_messages reducer so concurrent sends don't overwrite each other.

    spawn: Annotated[Optional[SpawnRequest], keep_latest]
    # Request to create an isolated sub-agent branch.
    # Set by a node that needs background work done without blocking.
    # spawn_router in conditions.py detects this and routes to the named agent.
    # Cleared (set to None) after the spawned agent completes.
    #
    # How to spawn a sub-agent:
    #   from app.agents.shared.message_bus import request_spawn
    #   return request_spawn("research_agent", task="find trends", payload={...})


# ---------------------------------------------------------------------------
# Factory — creates a clean initial state for a new graph run
# ---------------------------------------------------------------------------

def make_initial_state(
    user_query: str,
    run_id: str,
    context: Optional[MemoryContext] = None,
    user_id: Optional[str] = None,
) -> AgentState:
    """
    Creates a valid initial AgentState for any workflow type.

    Called by the API route handler (or scheduler for autonomous runs)
    before graph.invoke(). All optional fields start as None so nodes can
    detect "has the previous node run yet?" by checking for None.

    Usage (complaint analysis, lead gen, supply chain — same call):
        state = make_initial_state(
            user_query="find 50 high-risk suppliers in East Java",
            run_id=str(uuid4()),
            context=await load_memory_context(user_id),  # from Redis/Supabase
        )
        result = graph.invoke(state)

    The intent_node will then set workflow_type and department from user_query.
    """
    return AgentState(
        # [1] Request layer
        user_query=user_query,
        goal=None,
        workflow_type=None,      # set by intent_node
        department=None,         # set by planner_node
        constraints=None,        # set by intent_node
        # [2] Execution layer
        workflow_plan=None,          # set by planner_node (list of workflow_types)
        current_workflow_index=0,    # starts at 0, advances +1 per completed workflow
        plan=None,
        artifacts=Artifacts(
            workflow_data=None,  # primary output store for all workflow types
            report=None,         # final human-readable output (optional)
        ),
        # [3] Quality layer
        metrics=Metrics(
            item_count=None,
            quality_score=None,
            confidence=None,
            custom=None,
        ),
        approval=ApprovalGate(
            required=False,
            status="not_required",
            approved_by=None,
            approved_at=None,
            rejection_reason=None,
        ),
        # [4] System layer
        status="running",
        iteration_count=0,
        next_node=None,
        errors=[],
        run_id=run_id,
        user_id=user_id,
        # [5] Memory layer
        context=context,
        # [6] Message bus — A2A
        messages=[],    # no messages in flight at start
        spawn=None,     # no spawn request at start
    )
