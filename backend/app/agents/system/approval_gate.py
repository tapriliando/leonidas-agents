"""
approval_gate.py — Human-in-the-loop pause via LangGraph interrupt() / Command(resume=...).

Skips when constraints.require_approval is false. Otherwise calls interrupt() with a
review payload; after resume, updates approval + routes via approval_after_gate_router.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from langgraph.types import interrupt

if TYPE_CHECKING:
    from app.state import AgentState


def _review_payload(state: "AgentState") -> dict[str, Any]:
    artifacts = state.get("artifacts") or {}
    wd = artifacts.get("workflow_data")
    report = artifacts.get("report")
    return {
        "run_id": state.get("run_id", ""),
        "content_to_review": wd if wd is not None else [],
        "summary": report or "",
        "user_query": state.get("user_query", ""),
    }


async def approval_gate(state: "AgentState") -> dict[str, Any]:
    constraints = state.get("constraints") or {}
    if not constraints.get("require_approval"):
        return {
            "approval": {
                "required": False,
                "status": "not_required",
                "approved_by": None,
                "approved_at": None,
                "rejection_reason": None,
            }
        }

    payload = _review_payload(state)
    decision = interrupt(payload)
    if not isinstance(decision, dict):
        decision = {"status": "rejected", "comment": str(decision), "user_id": None}

    status = str(decision.get("status") or "").lower()
    comment = decision.get("comment")
    uid = decision.get("user_id")
    now = datetime.now(timezone.utc).isoformat()

    artifacts = state.get("artifacts") or {}
    wd: dict = dict(artifacts.get("workflow_data") or {})

    if status == "approved":
        return {
            "approval": {
                "required": True,
                "status": "approved",
                "approved_by": str(uid) if uid else None,
                "approved_at": now,
                "rejection_reason": None,
            },
            "status": "running",
        }

    # rejected (default)
    feedback = str(comment or "").strip() or "Rejected — please revise."
    wd["approval_feedback"] = feedback
    return {
        "approval": {
            "required": True,
            "status": "rejected",
            "approved_by": None,
            "approved_at": None,
            "rejection_reason": feedback,
        },
        "artifacts": {**artifacts, "workflow_data": wd},
        "status": "running",
        "iteration_count": 1,
    }
