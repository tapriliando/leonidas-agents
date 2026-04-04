"""
analytics_agent.py — Generic pure-computation stats node for any scored dataset.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["scored_items"]  — or falls back to ["items"]
          artifacts.workflow_data["item_scores"]   — optional score lookup dict
          constraints.limit                        — for top-N cutoff
  Writes: artifacts.workflow_data["analytics"]     — structured stats dict
  Calls:  nothing — pure computation, no LLM, no MCP

DESIGN:
  This node is completely workflow-agnostic. It never knows whether the items are
  leads, complaints, suppliers, candidates, or any other domain.

  It reads from standardized keys ("scored_items" or "items") and writes to "analytics".

  ANALYTICS COMPUTED:
    score_distribution:  count of items per 0.2-width score bucket (0.0–0.2, 0.2–0.4, ...)
    top_items:           top-N items sorted by score descending (N = min(10, limit or 10))
    priority_counts:     {"high": int, "medium": int, "low": int}
    avg_score:           float — arithmetic mean of all item scores
    total_count:         int — total items processed

  FALLBACK FOR UNSCORED DATA:
    If only "items" is available (no "scored_items"), analytics runs on the raw items
    with default scores of 0.5 (no priority info). This lets analytics_agent work even
    when placed in a graph without an assigner_agent upstream.

  OUTPUT SHAPE:
    {
      "score_distribution": {"0.0-0.2": 3, "0.2-0.4": 7, ...},
      "top_items":          [{"id": ..., "name": ..., "score": ..., ...}, ...],
      "priority_counts":    {"high": 5, "medium": 12, "low": 3},
      "avg_score":          0.67,
      "total_count":        20
    }
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.state import AgentState


_SCORE_BUCKETS = [
    (0.0, 0.2, "0.0-0.2"),
    (0.2, 0.4, "0.2-0.4"),
    (0.4, 0.6, "0.4-0.6"),
    (0.6, 0.8, "0.6-0.8"),
    (0.8, 1.001, "0.8-1.0"),
]


def analytics_agent(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: computes distribution statistics for any scored dataset.

    Synchronous — pure Python computation, no I/O.
    Returns a partial state update with "analytics" added to workflow_data.
    """
    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}
    constraints = state.get("constraints") or {}

    # Prefer scored_items; fall back to raw items
    items: list[dict] = workflow_data.get("scored_items") or workflow_data.get("items") or []
    item_scores: dict[str, float] = workflow_data.get("item_scores") or {}

    top_n = constraints.get("limit") or 10

    if not items:
        analytics = _empty_analytics()
    else:
        analytics = _compute_analytics(items, item_scores, top_n)

    updated_workflow_data = {**workflow_data, "analytics": analytics}
    updated_artifacts = {**artifacts, "workflow_data": updated_workflow_data}

    existing_metrics = state.get("metrics") or {}
    return {
        "artifacts": updated_artifacts,
        "metrics": {
            **existing_metrics,
            "custom": {
                **(existing_metrics.get("custom") or {}),
                "top_item_count": len(analytics.get("top_items") or []),
                "avg_score": analytics.get("avg_score"),
            },
        },
    }


# ── Computation helpers ───────────────────────────────────────────────────────

def _compute_analytics(
    items: list[dict],
    item_scores: dict[str, float],
    top_n: int,
) -> dict[str, Any]:
    # Resolve scores — prefer item_scores lookup, fall back to item["score"] field, then 0.5
    scored: list[tuple[dict, float]] = []
    for item in items:
        item_id = str(item.get("id", ""))
        score = (
            item_scores.get(item_id)
            or _to_float(item.get("score"))
            or 0.5
        )
        scored.append((item, score))

    all_scores = [s for _, s in scored]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

    # Score distribution bucketing
    score_distribution: dict[str, int] = {label: 0 for _, _, label in _SCORE_BUCKETS}
    for score in all_scores:
        for lo, hi, label in _SCORE_BUCKETS:
            if lo <= score < hi:
                score_distribution[label] += 1
                break

    # Priority counts
    priority_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for item, _ in scored:
        prio = (item.get("priority") or "").lower()
        if prio in priority_counts:
            priority_counts[prio] += 1
        else:
            priority_counts["low"] += 1  # unclassified → low

    # Top-N items sorted by score descending
    sorted_scored = sorted(scored, key=lambda x: x[1], reverse=True)
    top_items = [
        {
            "id":       item.get("id"),
            "name":     item.get("name"),
            "score":    score,
            "priority": item.get("priority"),
            "address":  item.get("address"),
        }
        for item, score in sorted_scored[:top_n]
    ]

    return {
        "score_distribution": score_distribution,
        "top_items": top_items,
        "priority_counts": priority_counts,
        "avg_score": round(avg_score, 4),
        "total_count": len(items),
    }


def _empty_analytics() -> dict[str, Any]:
    return {
        "score_distribution": {label: 0 for _, _, label in _SCORE_BUCKETS},
        "top_items": [],
        "priority_counts": {"high": 0, "medium": 0, "low": 0},
        "avg_score": 0.0,
        "total_count": 0,
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
