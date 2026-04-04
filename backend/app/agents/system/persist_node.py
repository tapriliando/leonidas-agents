"""
persist_node.py — Terminal node that writes run metadata and artifacts to Supabase.

NODE CONTRACT:
  Reads:  run_id, workflow_type, status, metrics, artifacts, errors, iteration_count
  Writes: status  — "completed" if no errors, "failed" if errors are present
  Calls MCP: mcp.supabase_query (insert) — two calls: workflow_runs + workflow_artifacts

DESIGN:
  This node is ALWAYS the last node in every graph. It runs even when earlier nodes
  failed — that is intentional. A failed run must still be recorded in Supabase so
  dashboards, retries, and feedback loops have accurate history.

  It is completely workflow-agnostic:
    - workflow_type is stored as a metadata string, never branched on
    - artifacts.workflow_data is stored as-is, regardless of its structure
    - errors are stored verbatim for debugging

  MCP failures are handled gracefully: if the supabase insert fails, the error is
  appended to state["errors"] but the node still returns a terminal status so the
  graph ends cleanly rather than hanging.

SUPABASE TABLES:
  workflow_runs       — one row per graph.invoke() call (WorkflowRunRecord shape)
  workflow_artifacts  — one row per run with structured output (WorkflowArtifactRecord shape)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.mcp_client import call_tool
from app.memory.schemas import WorkflowRunRecord, WorkflowArtifactRecord

if TYPE_CHECKING:
    from app.state import AgentState


async def persist_node(state: "AgentState") -> dict[str, Any]:
    """
    LangGraph node: persists the run outcome and artifacts to Supabase.

    Always returns a terminal status update — the graph always ends after this node.
    On MCP failures, records the error and still sets a final status.
    """
    run_id: str = state.get("run_id", "")
    workflow_type: str = state.get("workflow_type") or "unknown"
    metrics = state.get("metrics") or {}
    artifacts = state.get("artifacts") or {}
    errors: list[str] = list(state.get("errors") or [])
    iteration_count: int = state.get("iteration_count", 0)

    # Determine final status from existing errors
    final_status = "failed" if errors else "completed"

    # Build the run record using the workflow-agnostic schema
    run_record = WorkflowRunRecord(
        run_id=run_id,
        workflow_type=workflow_type,
        status=final_status,
        user_id=state.get("user_id"),
        item_count=metrics.get("item_count"),
        quality_score=metrics.get("quality_score"),
        iteration_count=iteration_count,
        errors=errors,
        metadata={
            "goal": state.get("goal") or "",
            "constraints": _safe_json(state.get("constraints")),
            "custom_metrics": _safe_json(metrics.get("custom")),
            "report_preview": (artifacts.get("report") or "")[:500] or None,
        },
    )

    run_payload = run_record.model_dump()
    if run_payload.get("user_id") is None:
        run_payload.pop("user_id", None)

    # --- Insert 1: workflow run record ---
    try:
        result = await call_tool(
            "mcp.supabase_query",
            {
                "operation": "insert",
                "table": "workflow_runs",
                "data": run_payload,
            },
            meta={"run_id": run_id},
        )
        if not result.success:
            errors.append(f"persist_node: workflow_runs insert failed: {result.error}")
    except Exception as exc:
        errors.append(f"persist_node: workflow_runs insert error: {exc}")

    # --- Insert 2: workflow artifacts (only when data exists) ---
    workflow_data = artifacts.get("workflow_data")
    if workflow_data is not None:
        artifact_record = WorkflowArtifactRecord(
            run_id=run_id,
            artifact_type=workflow_type,
            data=workflow_data,
        )
        try:
            result = await call_tool(
                "mcp.supabase_query",
                {
                    "operation": "insert",
                    "table": "workflow_artifacts",
                    "data": artifact_record.model_dump(),
                },
                meta={"run_id": run_id},
            )
            if not result.success:
                errors.append(f"persist_node: workflow_artifacts insert failed: {result.error}")
        except Exception as exc:
            errors.append(f"persist_node: workflow_artifacts insert error: {exc}")

    # Recompute final status after inserts (MCP failures may have added new errors)
    final_status = "failed" if errors else "completed"

    update: dict[str, Any] = {"status": final_status}
    if errors:
        update["errors"] = errors
    return update


def _safe_json(value: Any) -> Any:
    """Converts a value to a JSON-serializable form. Falls back to str() on failure."""
    if value is None:
        return None
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)
