"""
heygen_video_agent.py — Calls HeyGen Video Agent via MCP; stores result in workflow_data.

NODE CONTRACT:
  Reads:  artifacts.workflow_data["content_prompt"]   — explicit video script / brief (highest priority)
          artifacts.workflow_data["summary"]        — uses overview as prompt if no content_prompt
          user_query, goal, constraints.filters      — fallbacks / overrides
  Writes: artifacts.workflow_data["content_generation"] — { provider, prompt_used, response }
          metrics.custom.heygen_status               — optional string from API
          errors                                     — on MCP failure
  Calls MCP: mcp.heygen_video_agent_generate

MULTI-WORKFLOW HANDOFF (research → video):
  research_node can set workflow_data["content_prompt"] from the synthesis, or this node
  builds a prompt from summary["overview"]. Same AgentState.workflow_data is carried
  across meta-graph steps, so no extra plumbing is required.

CONSTRAINTS (optional):
  constraints.filters.video_prompt     — overrides everything (exact HeyGen prompt)
  constraints.filters.video_prefix     — prepended to the resolved prompt
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool

if TYPE_CHECKING:
    from app.state import AgentState


def _resolve_prompt(state: "AgentState") -> str:
    artifacts = state.get("artifacts") or {}
    wd: dict = artifacts.get("workflow_data") or {}
    constraints = state.get("constraints") or {}
    filters: dict = constraints.get("filters") or {}

    feedback = wd.get("approval_feedback")
    feedback_prefix = ""
    if feedback and str(feedback).strip():
        feedback_prefix = f"Reviewer feedback (address this): {str(feedback).strip()}\n\n"

    explicit = filters.get("video_prompt")
    if explicit and str(explicit).strip():
        base = str(explicit).strip()
        return f"{feedback_prefix}{base}" if feedback_prefix else base

    cp = wd.get("content_prompt")
    if cp and str(cp).strip():
        base = str(cp).strip()
    else:
        summary = wd.get("summary") or {}
        overview = summary.get("overview") or summary.get("text") or ""
        if overview:
            base = (
                "A professional presenter delivers the following message clearly "
                f"in about 30–45 seconds: {overview}"
            )
        else:
            base = state.get("user_query") or state.get("goal") or "Short product update video."

    prefix = filters.get("video_prefix")
    if prefix and str(prefix).strip():
        base = f"{str(prefix).strip()} {base}"
    return f"{feedback_prefix}{base}" if feedback_prefix else base


async def heygen_video_agent(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: triggers HeyGen video generation and records the API response in state.
    """
    run_id: str = state.get("run_id", "")
    prompt = _resolve_prompt(state)
    artifacts = state.get("artifacts") or {}
    wd: dict = artifacts.get("workflow_data") or {}

    try:
        result = await call_tool(
            "mcp.heygen_video_agent_generate",
            {"prompt": prompt},
            meta={"run_id": run_id},
        )
    except Exception as exc:
        return {
            "errors": [f"heygen_video_agent: MCP call failed: {exc}"],
        }

    if not result.success:
        return {
            "errors": [f"heygen_video_agent: {result.error}"],
        }

    payload = result.data or {}
    inner = payload.get("response") if isinstance(payload, dict) else None
    status_hint = ""
    if isinstance(inner, dict):
        status_hint = str(inner.get("status") or inner.get("message") or "")[:200]

    content_generation = {
        "provider": "heygen_video_agent",
        "prompt_used": payload.get("prompt") if isinstance(payload, dict) else prompt,
        "response": inner if inner is not None else payload,
    }

    updated_wd = {**wd, "content_generation": content_generation}
    existing_metrics = state.get("metrics") or {}
    custom = dict(existing_metrics.get("custom") or {})
    if status_hint:
        custom["heygen_status"] = status_hint

    return {
        "artifacts": {**artifacts, "workflow_data": updated_wd},
        "metrics": {**existing_metrics, "custom": custom},
    }
