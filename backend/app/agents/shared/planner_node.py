"""
planner_node.py — Builds a validated execution plan from intent_node output.

NODE CONTRACT:
  Reads:  user_query, goal, constraints, workflow_type, workflow_plan  (from intent_node)
  Writes: workflow_plan  — validated + normalized list of workflow_type strings
          department     — first department in the sequence (string)
          errors         — appended only on LLM/parse failure

HOW IT WORKS:
  1. Derives complexity from current state (multi if workflow_plan has >1 item)
  2. Fetches agent descriptions from the registry for each candidate workflow
  3. Loads planner_node.txt from backend/app/prompts/ and renders the prompt
  4. Calls the LLM — expects JSON: complexity, workflow_plan, department_sequence,
     reasoning, estimated_steps  (matching the ExecutionPlan Pydantic model)
  5. Normalizes the response:
     - Drops unknown workflow types (not in registry)
     - Enforces complexity rules (direct → ["direct_answer"], single → one item)
     - Pads / trims department_sequence to match workflow_plan length
     - Caps workflow_plan length at MAX_WORKFLOW_STEPS from conditions.py
  6. Returns a partial state dict

FAILURE HANDLING:
  On any LLM or parse failure, falls back to the simplest valid plan using
  whatever workflow_type was set by intent_node. Errors go to state["errors"].

IMPORTANT — what this node does NOT touch:
  current_workflow_index — advanced exclusively by workflow_transition_node
  artifacts, metrics, messages — untouched
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.registry import (
    format_workflows_for_intent,
    format_agents_for_planner,
    get_all_workflow_types,
)
from app.graph.conditions import MAX_WORKFLOW_STEPS
from app.memory.schemas import ExecutionPlan
from app.agents.shared.llm_client import load_prompt, render_prompt, call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState


_PROMPT_FILE = "planner_node.txt"
_VALID_COMPLEXITIES = frozenset({"direct", "single_workflow", "multi_workflow"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fallback_plan(workflow_type: str | None) -> dict[str, Any]:
    """
    Minimal safe plan when the LLM call or parsing fails.

    Uses the workflow_type already set by intent_node, or falls back
    to "direct_answer" when nothing is known.
    """
    if not workflow_type:
        return {
            "complexity": "direct",
            "workflow_plan": ["direct_answer"],
            "department_sequence": ["shared"],
            "reasoning": "Fallback: could not parse planner response.",
            "estimated_steps": 1,
        }
    return {
        "complexity": "single_workflow",
        "workflow_plan": [workflow_type],
        "department_sequence": ["shared"],
        "reasoning": "Fallback: using intent-detected workflow type.",
        "estimated_steps": 3,
    }


def _build_available_agents(suggested_workflows: list[str]) -> str:
    """
    Builds the {{ available_agents }} block for the planner prompt.

    Groups agent descriptions by workflow type so the LLM can estimate
    step counts and understand what each workflow involves.
    """
    if not suggested_workflows:
        return "(no agents registered yet)"
    sections = []
    for wf_type in suggested_workflows:
        agent_text = format_agents_for_planner(wf_type)
        sections.append(f"[{wf_type}]\n{agent_text}")
    return "\n\n".join(sections)


def _normalize_plan(raw: dict[str, Any], known_workflow_types: set[str]) -> dict[str, Any]:
    """
    Validates and normalizes the LLM planner response.

    Rules applied in order:
    1. complexity must be one of the three valid strings; fallback to "direct"
    2. workflow_plan entries not in registry (or "direct_answer") are dropped
    3. Complexity rules: direct → ["direct_answer"], single → first entry only,
       multi → up to MAX_WORKFLOW_STEPS entries
    4. department_sequence is padded (with last entry or "shared") or trimmed
       so len(department_sequence) == len(workflow_plan)
    """
    complexity: str = raw.get("complexity", "direct")
    if complexity not in _VALID_COMPLEXITIES:
        complexity = "direct"

    # "direct_answer" is a valid pseudo-workflow that's not in the registry
    allowed = known_workflow_types | {"direct_answer"}
    raw_plan: list[str] = raw.get("workflow_plan") or []
    raw_depts: list[str] = raw.get("department_sequence") or []

    # Filter out unknown workflow types
    filtered_plan = [wf for wf in raw_plan if wf in allowed]

    # Enforce complexity rules
    if complexity == "direct":
        workflow_plan = ["direct_answer"]
    elif complexity == "single_workflow":
        workflow_plan = filtered_plan[:1] if filtered_plan else ["direct_answer"]
    else:  # multi_workflow
        workflow_plan = filtered_plan[:MAX_WORKFLOW_STEPS] if filtered_plan else ["direct_answer"]

    # Pad or trim department_sequence to match workflow_plan length
    if len(raw_depts) < len(workflow_plan):
        pad = raw_depts[-1] if raw_depts else "shared"
        raw_depts = raw_depts + [pad] * (len(workflow_plan) - len(raw_depts))
    department_sequence = raw_depts[: len(workflow_plan)]

    return {
        "complexity": complexity,
        "workflow_plan": workflow_plan,
        "department_sequence": department_sequence,
        "reasoning": str(raw.get("reasoning", "")),
        "estimated_steps": int(raw.get("estimated_steps", len(workflow_plan))),
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

async def planner_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: builds a validated execution plan from intent_node output.

    Returns a partial state update. Never touches current_workflow_index —
    that counter is advanced exclusively by workflow_transition_node.
    """
    user_query: str = state.get("user_query", "")
    goal: str = str(state.get("goal") or "")
    constraints = state.get("constraints") or {}
    workflow_type: str | None = state.get("workflow_type")

    # Determine complexity from what intent_node wrote to state
    intent_plan: list[str] = state.get("workflow_plan") or []
    if len(intent_plan) > 1:
        complexity_hint = "multi_workflow"
    elif workflow_type:
        complexity_hint = "single_workflow"
    else:
        complexity_hint = "direct"

    # Candidate workflows for the prompt context
    suggested_workflows: list[str] = intent_plan or ([workflow_type] if workflow_type else [])

    # Build registry context for the prompt
    available_workflows = format_workflows_for_intent()
    available_agents = _build_available_agents(suggested_workflows)
    known_workflow_types = set(get_all_workflow_types())

    # Load and render the prompt template
    template = load_prompt(_PROMPT_FILE)
    prompt = render_prompt(
        template,
        user_query=user_query,
        goal=goal,
        complexity=complexity_hint,
        suggested_workflows=json.dumps(suggested_workflows),
        constraints=json.dumps(constraints, ensure_ascii=False),
        available_workflows=available_workflows,
        available_agents=available_agents,
    )

    # Call LLM — fallback to safe defaults on any failure
    plan_raw: dict[str, Any] = {}
    errors: list[str] = []
    try:
        raw_response = await call_llm(prompt)
        plan_raw = parse_json_response(raw_response, context="planner_node")
    except Exception as exc:
        errors.append(f"planner_node: {exc}")
        plan_raw = _fallback_plan(workflow_type)

    # Validate and normalize
    plan = _normalize_plan(plan_raw, known_workflow_types)

    # Optional: validate the full plan against the Pydantic ExecutionPlan model.
    # This catches structural issues early (e.g. mismatched list lengths).
    try:
        ExecutionPlan(**plan)
    except Exception as exc:
        errors.append(f"planner_node: ExecutionPlan validation: {exc}")

    workflow_plan: list[str] = plan["workflow_plan"]
    department_sequence: list[str] = plan["department_sequence"]

    # --- Build the partial state update ---

    update: dict[str, Any] = {
        "workflow_plan": workflow_plan,
        # department = first department in the sequence; subgraph routing reads this
        "department": department_sequence[0] if department_sequence else None,
        # plan (per-workflow node list) is informational and set by workflow subgraphs in Phase 4+
    }

    if errors:
        update["errors"] = errors

    return update
