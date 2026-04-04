"""
memory/schemas.py — Pydantic models bridging persistent storage and AgentState.

DESIGN PHILOSOPHY:
  These models are WORKFLOW-AGNOSTIC. The same schema persists runs and artifacts
  for any workflow type: lead gen, supply chain, marketing, agriculture, etc.

  WorkflowRunRecord       → one row per graph.invoke() call (any workflow)
  WorkflowArtifactRecord  → stores structured output from any workflow
  MemoryContextLoader     → converts DB/Redis data into AgentState.context
  UserSessionRecord       → user profile and session data

  To add a new workflow: you do NOT change these schemas.
  Use `metadata` (in WorkflowRunRecord) and `data` (in WorkflowArtifactRecord)
  to store any workflow-specific fields without schema migrations.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class MemoryContextLoader(BaseModel):
    """
    Converts DB/Redis query results into the MemoryContext shape that
    AgentState["context"] expects.

    Called by the API route handler BEFORE graph.invoke(), not inside a node.
    All fields are optional — a first-time user has no history and that's fine.

    Fields mirror MemoryContext in state.py:
      recent_summaries → past_run_summaries
      benchmark_score  → benchmark_score
      user_preferences → user_preferences
      domain_context   → domain_context (workflow-specific knowledge)
    """

    recent_summaries: Optional[list[str]] = None
    benchmark_score: Optional[float] = None
    user_preferences: Optional[dict[str, Any]] = None
    domain_context: Optional[dict[str, Any]] = None

    def to_agent_context(self) -> dict[str, Any]:
        """
        Produce the dict that plugs directly into AgentState["context"].
        Keys match MemoryContext TypedDict in state.py exactly.
        """
        return {
            "past_run_summaries": self.recent_summaries,
            "benchmark_score": self.benchmark_score,
            "user_preferences": self.user_preferences,
            "domain_context": self.domain_context,
        }


class WorkflowRunRecord(BaseModel):
    """
    One row in the `workflow_runs` Supabase table.

    Written by persist_node at the end of every graph run, regardless of
    workflow type. `metadata` holds any workflow-specific data that doesn't
    fit the typed fields — no schema migration needed for new workflows.

    Examples of metadata by workflow:
      lead_gen:       {"target_location": "Jakarta", "category": "pharma"}
      supply_chain:   {"region": "East Java", "risk_threshold": 0.7}
      marketing:      {"campaign_id": "c123", "channel": "email"}
    """

    run_id: str
    workflow_type: str              # open-ended: "lead_gen", "supply_chain", etc.
    status: str                     # "completed" | "failed" | "paused"
    user_id: Optional[str] = None   # API identity; enables per-user memory loader queries
    item_count: Optional[int] = None
    quality_score: Optional[float] = None
    iteration_count: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowArtifactRecord(BaseModel):
    """
    Stores the structured output from a workflow run for long-term retrieval.

    artifact_type is a string label for the kind of output:
      lead_gen:       "leads_batch", "outreach_drafts"
      supply_chain:   "risk_assessment", "supplier_report"
      marketing:      "campaign_brief", "ab_test_results"
      agriculture:    "field_analysis", "harvest_plan"

    data holds the actual payload — any dict structure for any workflow.
    embedding is reserved for pgvector semantic search (later phase).
    """

    run_id: str
    artifact_type: str              # label describing what this data is
    data: dict[str, Any]            # the actual structured output
    embedding: Optional[list[float]] = None


class UserSessionRecord(BaseModel):
    """
    User session and profile data from Supabase.

    Loaded before graph runs and passed into MemoryContextLoader
    to populate AgentState["context"]["user_preferences"].
    """

    user_id: str
    session_id: str
    preferences: Optional[dict[str, Any]] = None


class ExecutionPlan(BaseModel):
    """
    The structured output of planner_node.

    The planner LLM produces this JSON. It captures not just WHAT to run
    but WHY — so you can read the reasoning when debugging a bad plan.

    complexity tells you which path the planner chose:
      "direct"           → one agent answers directly (simple factual question)
      "single_workflow"  → one workflow, one department
      "multi_workflow"   → multiple workflows chained across departments

    workflow_plan maps directly into AgentState.workflow_plan.
    department_sequence maps directly into AgentState.department (per-step).
    """

    complexity: str
    # "direct" | "single_workflow" | "multi_workflow"

    workflow_plan: list[str]
    # Ordered list of workflow_types to execute.
    # single:  ["lead_gen"]
    # multi:   ["research_intelligence", "content_pipeline", "social_publishing"]
    # direct:  ["direct_answer"]

    department_sequence: list[str]
    # Department for each step in workflow_plan (same length).
    # single:  ["distribution"]
    # multi:   ["research", "content", "distribution"]

    reasoning: str
    # One sentence explaining why this plan was chosen.
    # Used for logging, debugging, and displaying to the user.
    # Example: "User wants research + content + publishing — 3 separate departments."

    estimated_steps: int
    # Rough total number of agent nodes that will run across all workflows.
    # Used to show a progress indicator in the UI.


__all__ = [
    "MemoryContextLoader",
    "WorkflowRunRecord",
    "WorkflowArtifactRecord",
    "UserSessionRecord",
    "ExecutionPlan",
]
