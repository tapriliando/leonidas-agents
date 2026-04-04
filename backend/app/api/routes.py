"""
Primary API routes: POST /run (graph + cache + memory), health checks.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import get_graph
from app.cache import cache_keys
from app.cache.redis_cache import (
    cache_intent_result,
    cache_result,
    get_cached_intent,
    redis_enabled,
)
from app.graph.interrupts import interrupt_values_from_result, result_has_interrupt
from app.memory.loader import load_user_context
from app.state import make_initial_state

router = APIRouter(tags=["run"])


class RunBody(BaseModel):
    user_query: str = Field(..., min_length=1)
    user_id: str | None = Field(default=None)


def _serialize_state(result: dict) -> dict:
    return json.loads(json.dumps(result, default=str))


def _intent_key(user_id: str | None, user_query: str) -> str:
    h = hashlib.sha256(f"{user_id or ''}|{user_query}".encode("utf-8")).hexdigest()
    if user_id:
        return f"{cache_keys.user_results_prefix(user_id)}intent:{h}"
    return cache_keys.intent_cache(h)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/run")
async def run_workflow(
    body: RunBody,
    graph: Annotated[Any, Depends(get_graph)],
):
    ctx = await load_user_context(body.user_id or "")
    qk = _intent_key(body.user_id, body.user_query)

    if redis_enabled():
        cached = await get_cached_intent(qk)
        if cached is not None:
            return JSONResponse(status_code=200, content=cached)

    run_id = str(uuid.uuid4())
    initial = make_initial_state(
        user_query=body.user_query,
        run_id=run_id,
        context=ctx,
        user_id=body.user_id,
    )
    config = {"configurable": {"thread_id": run_id}}

    result = await graph.ainvoke(initial, config)

    if result_has_interrupt(result):
        return JSONResponse(
            status_code=202,
            content={
                "run_id": run_id,
                "status": "paused_for_approval",
                "interrupts": interrupt_values_from_result(result),
            },
        )

    dto = _serialize_state(result)
    dto["run_id"] = run_id

    if redis_enabled():
        await cache_result(run_id, dto)
        await cache_intent_result(qk, dto)

    return JSONResponse(status_code=200, content=dto)
