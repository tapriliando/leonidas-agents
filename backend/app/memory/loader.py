"""
load_user_context — Hydrate MemoryContext from Supabase workflow_runs (Phase 7).

Uses sync Supabase client in asyncio.to_thread so the FastAPI event loop stays responsive.
If credentials are missing or supabase is not installed, returns an empty MemoryContext shape.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from app.state import MemoryContext


def _empty_context() -> MemoryContext:
    return MemoryContext(
        past_run_summaries=None,
        benchmark_score=None,
        user_preferences=None,
        domain_context=None,
    )


def _select_runs_sync(user_id: str, limit: int) -> list[dict[str, Any]]:
    from supabase import create_client

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        return []
    client = create_client(url, key)
    resp = (
        client.table("workflow_runs")
        .select("workflow_type,status,quality_score,metadata,created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(resp.data or [])


async def load_user_context(user_id: str, limit: int = 3) -> MemoryContext:
    if not user_id:
        return _empty_context()

    try:
        rows = await asyncio.to_thread(_select_runs_sync, user_id, limit)
    except Exception:
        return _empty_context()

    if not rows:
        return _empty_context()

    summaries: list[str] = []
    scores: list[float] = []
    for row in rows:
        meta = row.get("metadata") or {}
        prev = meta.get("report_preview") or meta.get("goal") or ""
        wf = row.get("workflow_type") or "workflow"
        st = row.get("status") or ""
        line = f"{wf} ({st})"
        if prev:
            line = f"{line}: {str(prev)[:200]}"
        summaries.append(line)
        qs = row.get("quality_score")
        if isinstance(qs, (int, float)):
            scores.append(float(qs))

    benchmark = sum(scores) / len(scores) if scores else None

    return MemoryContext(
        past_run_summaries=summaries or None,
        benchmark_score=benchmark,
        user_preferences=None,
        domain_context={"recent_run_count": len(rows)},
    )
