"""
suggest_node.py — Generic LLM recommendation generator from any summary.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["summary"]  — produced by summarize_node
          workflow_type, goal                 — injected as LLM prompt context
          constraints                         — passed to LLM for scoping
          errors                              — appended on failure
  Writes: artifacts.workflow_data["suggestions"]  — list of actionable recommendations
  Calls:  LLM via call_llm()

DESIGN:
  Like summarize_node, this node contains zero domain knowledge. It injects
  workflow_type and goal into the prompt so the LLM generates domain-appropriate
  recommendations, while the node code remains workflow-agnostic.

  EXPECTED LLM OUTPUT (strict JSON array):
    [
      {
        "action":    "<imperative sentence — what to do>",
        "priority":  "high" | "medium" | "low",
        "rationale": "<one sentence explaining why>"
      },
      ...
    ]

  FAILURE HANDLING:
    On LLM/parse failure, returns a single fallback suggestion asking the user
    to review the summary manually. This ensures report_node always has something
    in the "suggestions" key rather than encountering None.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.shared.llm_client import call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState


async def suggest_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: generates actionable recommendations from the workflow summary.

    Returns a partial state update with suggestions added to workflow_data.
    """
    workflow_type: str = state.get("workflow_type") or "unknown"
    goal: str = state.get("goal") or "analyze data"
    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}
    summary: dict = workflow_data.get("summary") or {}
    constraints = state.get("constraints") or {}
    filters = constraints.get("filters") or {}

    # Serialize summary for the prompt
    summary_text = json.dumps(summary, ensure_ascii=False, default=str)
    constraints_text = json.dumps(
        {"limit": constraints.get("limit"), "filters": filters},
        ensure_ascii=False,
    )

    prompt = f"""You are a strategic advisor. Based on the summary below from a "{workflow_type}"
workflow (goal: "{goal}"), generate actionable recommendations.

CONSTRAINTS / SCOPE:
{constraints_text}

SUMMARY:
{summary_text}

INSTRUCTIONS:
1. Generate 3-6 specific, actionable recommendations.
2. Each recommendation must be a concrete action (start with a verb).
3. Assign a priority: "high" (urgent / high impact), "medium", or "low".
4. Provide a one-sentence rationale for each.
5. Tailor recommendations to the {workflow_type} domain.

OUTPUT FORMAT (respond ONLY with a valid JSON array, no extra text):
[
  {{
    "action":    "<imperative verb phrase>",
    "priority":  "high" | "medium" | "low",
    "rationale": "<one sentence>"
  }}
]"""

    errors: list[str] = []
    suggestions: list[dict] = []

    try:
        raw = await call_llm(prompt)
        parsed = parse_json_response(raw, context="suggest_node")
        # The LLM returns a JSON array — handle both list and dict wrapping
        if isinstance(parsed, list):
            suggestions = parsed
        elif isinstance(parsed, dict):
            suggestions = parsed.get("suggestions") or list(parsed.values())[0] if parsed else []
    except Exception as exc:
        errors.append(f"suggest_node: {exc}")
        suggestions = [
            {
                "action": f"Review the {workflow_type} summary manually",
                "priority": "medium",
                "rationale": "Automated suggestion generation failed; human review required.",
            }
        ]

    updated_workflow_data = {**workflow_data, "suggestions": suggestions}
    updated_artifacts = {**artifacts, "workflow_data": updated_workflow_data}

    update: dict[str, Any] = {"artifacts": updated_artifacts}
    if errors:
        update["errors"] = errors
    return update
