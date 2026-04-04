"""Centralized Redis key builders — avoid string typos in routes and cache layer."""

from __future__ import annotations


def workflow_result(run_id: str) -> str:
    return f"mas:workflow:result:{run_id}"


def user_session(user_id: str) -> str:
    return f"mas:session:active:{user_id}"


def intent_cache(query_hash: str) -> str:
    return f"mas:intent:{query_hash}"


def user_results_prefix(user_id: str) -> str:
    """Prefix for keys related to a user (invalidation scans)."""
    return f"mas:user:{user_id}:"
