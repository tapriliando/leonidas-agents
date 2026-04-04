"""
Human-in-the-loop endpoints: pending interrupt payload, approve, reject (resume graph).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.api.deps import get_graph
from app.graph.interrupts import interrupts_from_snapshot

router = APIRouter(prefix="/run", tags=["workflows"])


class ApproveBody(BaseModel):
    user_id: str | None = Field(default=None, description="Reviewer identity for audit trail")


class RejectBody(BaseModel):
    comment: str = Field(default="", description="Why content was rejected")
    user_id: str | None = Field(default=None)


def _config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


@router.get("/{run_id}/pending")
async def get_pending(
    run_id: str,
    graph: Annotated[Any, Depends(get_graph)],
):
    snap = await graph.aget_state(_config(run_id))
    pending = interrupts_from_snapshot(snap)
    if not pending:
        return {"run_id": run_id, "pending": False, "interrupts": []}
    return {"run_id": run_id, "pending": True, "interrupts": pending}


@router.post("/{run_id}/approve")
async def approve_run(
    run_id: str,
    graph: Annotated[Any, Depends(get_graph)],
    body: ApproveBody | None = Body(default=None),
):
    uid = body.user_id if body else None
    payload = {"status": "approved", "user_id": uid, "comment": None}
    result = await graph.ainvoke(Command(resume=payload), _config(run_id))
    return {"run_id": run_id, "result": result}


@router.post("/{run_id}/reject")
async def reject_run(
    run_id: str,
    graph: Annotated[Any, Depends(get_graph)],
    body: RejectBody | None = Body(default=None),
):
    b = body or RejectBody()
    payload = {
        "status": "rejected",
        "comment": b.comment or "Rejected",
        "user_id": b.user_id,
    }
    result = await graph.ainvoke(Command(resume=payload), _config(run_id))
    return {"run_id": run_id, "result": result}
