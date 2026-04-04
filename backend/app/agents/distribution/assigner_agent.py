"""
assigner_agent.py — Generic LLM-based scorer and prioritizer for enriched items.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["enriched_items"]  — from enrichment_agent
          workflow_type, goal                        — injected into LLM prompt as context
          constraints                                — scoring scope / criteria
          context.domain_context                     — optional scoring criteria from memory
  Writes: artifacts.workflow_data["scored_items"]    — items with score + priority fields
          artifacts.workflow_data["item_scores"]     — {item_id: float} score lookup dict
          metrics.quality_score                      — average score (for scored_items_router)
          errors                                     — appended on LLM/parse failure
  Calls:  LLM via call_llm()

DESIGN:
  This node is completely domain-agnostic. The LLM performs the scoring — the node
  only constructs the prompt (injecting workflow_type, goal, and domain_context), parses
  the response, and normalizes the output.

  SCORING APPROACH:
    The LLM receives a list of items (name + context + available metadata) and returns
    a scored/prioritized list. Scores are 0.0–1.0 floats; priority is "high"/"medium"/"low".

    By passing workflow_type and goal, the LLM understands the domain without the node
    containing any domain-specific code. A "talent_pipeline" and a "lead_gen" run of the
    same node produce domain-appropriate scores.

  EXPECTED LLM OUTPUT (JSON array):
    [
      {
        "id":       "<item id from input>",
        "score":    <float 0.0–1.0>,
        "priority": "high" | "medium" | "low",
        "reason":   "<one sentence>"
      },
      ...
    ]

  FALLBACK:
    On parse failure, each item gets score = 0.5, priority = "medium".
    metrics.quality_score = 0.3 (below threshold) triggers scored_items_router → "retry".

  METRICS:
    metrics.quality_score = average score across all scored items.
    This is what scored_items_router reads to decide "pass" | "retry" | "fail".
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.shared.llm_client import call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState

_MAX_ITEMS_IN_PROMPT = 30


async def assigner_agent(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: scores and prioritizes enriched items using an LLM.

    Returns a partial state update with scored_items, item_scores, and quality_score.
    """
    workflow_type: str = state.get("workflow_type") or "unknown"
    goal: str = state.get("goal") or "score items"
    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}
    enriched_items: list = workflow_data.get("enriched_items") or []
    constraints = state.get("constraints") or {}
    ctx = state.get("context") or {}
    domain_context = ctx.get("domain_context") or {}

    # Prepare items for the prompt — include relevant fields, cap for token budget
    items_for_prompt = []
    for item in enriched_items[:_MAX_ITEMS_IN_PROMPT]:
        items_for_prompt.append({
            "id":      item.get("id", ""),
            "name":    item.get("name", ""),
            "address": item.get("address"),
            "rating":  item.get("rating"),
            "context": item.get("context"),
        })

    items_json = json.dumps(items_for_prompt, ensure_ascii=False, default=str)
    domain_ctx_str = json.dumps(domain_context, ensure_ascii=False) if domain_context else "(none)"
    constraints_str = json.dumps(
        {"limit": constraints.get("limit"), "filters": constraints.get("filters")},
        ensure_ascii=False,
    )

    prompt = f"""You are a scoring specialist in a "{workflow_type}" workflow (goal: "{goal}").
Score each item in the list below on a scale of 0.0 to 1.0 and assign a priority.

DOMAIN CONTEXT (from memory):
{domain_ctx_str}

CONSTRAINTS / SCOPE:
{constraints_str}

ITEMS TO SCORE ({len(items_for_prompt)} of {len(enriched_items)} total):
{items_json}

SCORING CRITERIA:
- Score 0.8–1.0 = high priority: excellent fit, strong signals, high potential
- Score 0.5–0.79 = medium priority: moderate fit, some positive signals
- Score 0.0–0.49 = low priority: weak fit or insufficient information

OUTPUT FORMAT (respond ONLY with a valid JSON array, no extra text):
[
  {{
    "id":       "<same id as in input>",
    "score":    <float 0.0-1.0>,
    "priority": "high" | "medium" | "low",
    "reason":   "<one sentence explaining the score>"
  }}
]"""

    errors: list[str] = []
    scored_map: dict[str, dict] = {}

    try:
        raw = await call_llm(prompt)
        parsed = parse_json_response(raw, context="assigner_agent")
        if isinstance(parsed, list):
            for entry in parsed:
                item_id = str(entry.get("id", ""))
                if item_id:
                    scored_map[item_id] = entry
        elif isinstance(parsed, dict):
            for entry in (parsed.get("items") or []):
                item_id = str(entry.get("id", ""))
                if item_id:
                    scored_map[item_id] = entry
    except Exception as exc:
        errors.append(f"assigner_agent: {exc}")

    # Merge scores back into items; fallback for any item not in LLM response
    scored_items: list[dict] = []
    item_scores: dict[str, float] = {}

    for item in enriched_items:
        item_id = str(item.get("id", ""))
        entry = scored_map.get(item_id) or {}

        score = _to_float(entry.get("score"), default=0.5 if errors else 0.5)
        priority = entry.get("priority") or (_derive_priority(score))
        reason = entry.get("reason") or ""

        scored_item = {
            **item,
            "score": score,
            "priority": priority,
            "score_reason": reason,
        }
        scored_items.append(scored_item)
        item_scores[item_id] = score

    avg_score = (
        sum(item_scores.values()) / len(item_scores) if item_scores else 0.3
    )

    existing_metrics = state.get("metrics") or {}
    updated_workflow_data = {
        **workflow_data,
        "scored_items": scored_items,
        "item_scores": item_scores,
    }
    updated_artifacts = {**artifacts, "workflow_data": updated_workflow_data}

    update: dict[str, Any] = {
        "artifacts": updated_artifacts,
        "metrics": {
            **existing_metrics,
            "quality_score": avg_score,
        },
    }
    if errors:
        update["errors"] = errors
    return update


def _to_float(value: Any, default: float = 0.5) -> float:
    if value is None:
        return default
    try:
        result = float(value)
        return max(0.0, min(1.0, result))
    except (TypeError, ValueError):
        return default


def _derive_priority(score: float) -> str:
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"
