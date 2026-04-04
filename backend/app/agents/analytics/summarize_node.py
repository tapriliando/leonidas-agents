"""
summarize_node.py — Generic LLM summarizer for any list of structured items.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["items"]  — list of dicts from fetch_node
          workflow_type, goal               — injected into LLM prompt as context
          context.domain_context            — optional domain knowledge (read-only)
  Writes: artifacts.workflow_data["summary"]  — structured summary dict
          metrics.quality_score              — set from summary["confidence"]
          metrics.confidence                 — same value, explicit field
          errors                             — appended on LLM/parse failure
  Calls:  LLM via call_llm()

DESIGN:
  This node never hardcodes a domain. It receives `workflow_type` and `goal` as
  prompt context strings, letting the LLM produce domain-appropriate output without
  the node itself knowing anything about the data domain.

  PROMPT CONTEXT:
    - workflow_type and goal tell the LLM what domain it is operating in
    - domain_context (from memory) provides additional background if available
    - items are serialized as a JSON sample (max 50 items for token efficiency)

  EXPECTED LLM OUTPUT (strict JSON):
    {
      "overview":      "<paragraph summarizing the data>",
      "key_findings":  ["<finding 1>", "<finding 2>", ...],
      "confidence":    <float 0.0–1.0>
    }

  quality_score is set from "confidence" so the existing quality_gate_router
  in conditions.py can evaluate it without any modification.

  FAILURE HANDLING:
    On LLM/parse failure, falls back to a minimal summary derived from item count.
    Sets confidence = 0.3 (below the default 0.7 threshold) so quality_gate_router
    routes to "retry", giving the node another attempt.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.agents.shared.llm_client import call_llm, parse_json_response

if TYPE_CHECKING:
    from app.state import AgentState


_MAX_ITEMS_IN_PROMPT = 50


async def summarize_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: summarizes items using an LLM with workflow context.

    Returns a partial state update with summary and updated quality metrics.
    """
    workflow_type: str = state.get("workflow_type") or "unknown"
    goal: str = state.get("goal") or "analyze data"
    artifacts = state.get("artifacts") or {}
    workflow_data: dict = artifacts.get("workflow_data") or {}
    items: list = workflow_data.get("items") or []
    ctx = state.get("context") or {}
    domain_context = ctx.get("domain_context") or {}

    # Serialize items for the prompt — cap at max to stay within token limits
    items_sample = items[:_MAX_ITEMS_IN_PROMPT]
    items_json = json.dumps(items_sample, ensure_ascii=False, default=str)

    domain_context_str = (
        json.dumps(domain_context, ensure_ascii=False) if domain_context else "(none)"
    )

    prompt = f"""You are a data analyst. Your task is to summarize a list of items retrieved
during a "{workflow_type}" workflow (goal: "{goal}").

DOMAIN CONTEXT (from memory):
{domain_context_str}

ITEMS ({len(items_sample)} shown, {len(items)} total):
{items_json}

INSTRUCTIONS:
1. Read the items carefully and identify the most important patterns, trends, or issues.
2. Write a clear overview paragraph (2-4 sentences).
3. List 3-7 specific key findings as concise bullet points.
4. Rate your confidence in this summary (0.0 = very uncertain, 1.0 = very confident).

OUTPUT FORMAT (respond ONLY with valid JSON, no extra text):
{{
  "overview":     "<2-4 sentence paragraph>",
  "key_findings": ["<finding>", ...],
  "confidence":   <float 0.0-1.0>
}}"""

    errors: list[str] = []
    summary: dict[str, Any] = {}

    try:
        raw = await call_llm(prompt)
        summary = parse_json_response(raw, context="summarize_node")
    except Exception as exc:
        errors.append(f"summarize_node: {exc}")
        # Minimal fallback summary — low confidence triggers a retry
        summary = {
            "overview": f"Processed {len(items)} items from the {workflow_type} workflow.",
            "key_findings": [f"Total items: {len(items)}"],
            "confidence": 0.3,
        }

    confidence = float(summary.get("confidence") or 0.3)
    existing_metrics = state.get("metrics") or {}

    updated_workflow_data = {**workflow_data, "summary": summary}
    updated_artifacts = {**artifacts, "workflow_data": updated_workflow_data}

    update: dict[str, Any] = {
        "artifacts": updated_artifacts,
        "metrics": {
            **existing_metrics,
            "quality_score": confidence,
            "confidence": confidence,
        },
    }
    if errors:
        update["errors"] = errors
    return update
