"""
intent_node.py — Classifies user intent and extracts structured execution parameters.

NODE CONTRACT:
  Reads:  state["user_query"]   — raw natural language from the user
          state["context"]      — optional memory context (user preferences, history)
  Writes: goal                  — 2-5 word slug, e.g. "find_pharmaceutical_leads"
          workflow_type         — first matched workflow from the registry (or None)
          constraints           — limit, require_approval, filters dict
          workflow_plan         — set ONLY when complexity == "multi_workflow"
          errors                — appended only on LLM/parse failure

HOW IT WORKS:
  1. Loads intent_node.txt from backend/app/prompts/
  2. Renders the template with: user_query, user_context (preferences), available_workflows
  3. Calls the LLM — expects a strict JSON response
  4. Validates the JSON: unknown workflow types dropped, constraints normalized
  5. Returns a partial state update (never touches artifacts, metrics, or messages)

FAILURE HANDLING:
  If the LLM call fails or returns malformed JSON, this node falls back to a safe
  "direct" classification and appends the error to state["errors"].
  The system degrades gracefully — planner_node applies its own fallback too.

OUTPUT WRITTEN TO STATE:
  goal           — structured goal slug
  workflow_type  — first valid suggested workflow (or None for direct)
  constraints    — Constraints TypedDict: limit, require_approval, filters
  workflow_plan  — only when complexity == "multi_workflow" and >1 valid workflows
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from app.registry import format_workflows_for_intent, get_all_workflow_types
from app.agents.shared.llm_client import load_prompt, render_prompt, call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState


_PROMPT_FILE = "intent_node.txt"
_VALID_COMPLEXITIES = frozenset({"direct", "single_workflow", "multi_workflow"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_goal(user_query: str) -> str:
    """Derives a short snake_case goal slug from the raw user_query as a fallback."""
    words = re.sub(r"[^a-z0-9 ]", "", user_query.lower()).split()
    return "_".join(words[:5]) or "unknown_goal"


def _normalize_constraints(raw: Any) -> dict[str, Any]:
    """
    Coerces the LLM constraints output into the Constraints TypedDict shape.

    Handles JSON nulls, missing keys, and unexpected types without raising.
    """
    if not isinstance(raw, dict):
        raw = {}

    limit = raw.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None

    return {
        "limit": limit,
        "require_approval": bool(raw.get("require_approval", False)),
        "filters": raw.get("filters") or {},
    }


def _fallback_intent(user_query: str) -> dict[str, Any]:
    """Returns safe defaults when the LLM call or JSON parsing fails."""
    return {
        "goal": _safe_goal(user_query),
        "complexity": "direct",
        "suggested_workflows": [],
        "constraints": {"limit": None, "require_approval": False, "filters": {}},
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------

async def intent_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: classifies user intent and extracts structured parameters.

    Returns a partial state update containing only the fields this node sets.
    Never writes to artifacts, metrics, messages, or execution-layer state fields.
    """
    user_query: str = state.get("user_query", "")
    ctx = state.get("context") or {}

    # Stringify user preferences for the prompt — empty profile when no context
    prefs = ctx.get("user_preferences") or {}
    user_context_str = json.dumps(prefs, ensure_ascii=False) if prefs else "(no user profile)"

    # Fetch available workflows from the YAML registry
    available_workflows = format_workflows_for_intent()

    # Load and render the prompt template
    template = load_prompt(_PROMPT_FILE)
    prompt = render_prompt(
        template,
        user_query=user_query,
        user_context=user_context_str,
        available_workflows=available_workflows,
    )

    # Call LLM — fallback to safe defaults on any failure
    intent: dict[str, Any] = {}
    errors: list[str] = []
    try:
        raw_response = await call_llm(prompt)
        intent = parse_json_response(raw_response, context="intent_node")
    except Exception as exc:
        errors.append(f"intent_node: {exc}")
        intent = _fallback_intent(user_query)

    # --- Validate and extract ---

    complexity: str = intent.get("complexity", "direct")
    if complexity not in _VALID_COMPLEXITIES:
        complexity = "direct"

    goal: str = str(intent.get("goal") or _safe_goal(user_query))

    # Drop any workflow types that aren't registered
    suggested: list[str] = intent.get("suggested_workflows") or []
    known = set(get_all_workflow_types())
    valid_suggested = [wf for wf in suggested if wf in known]

    constraints = _normalize_constraints(intent.get("constraints"))

    # workflow_type = first valid workflow from the LLM's suggestions (or None for direct)
    workflow_type: str | None = valid_suggested[0] if valid_suggested else None

    # --- Build the partial state update ---

    update: dict[str, Any] = {
        "goal": goal,
        "workflow_type": workflow_type,
        "constraints": constraints,
    }

    # Seed workflow_plan only for multi-workflow so planner_node can refine it.
    # Single-workflow plans are built by planner_node from workflow_type alone.
    if complexity == "multi_workflow" and len(valid_suggested) > 1:
        update["workflow_plan"] = valid_suggested

    if errors:
        update["errors"] = errors

    return update
