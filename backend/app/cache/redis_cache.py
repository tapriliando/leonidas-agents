"""
Application-level async Redis cache (cache-aside for API routes).

Degraded mode: if REDIS_URL is unset or init_redis was not called, all ops are no-ops.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore[misc, assignment]

_client: Any = None


async def init_redis(url: str | None = None) -> None:
    """Create the async Redis client once (call from FastAPI lifespan)."""
    global _client
    _client = None
    if aioredis is None:
        return
    u = url or os.getenv("REDIS_URL")
    if not u:
        return
    _client = aioredis.from_url(
        u,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def redis_enabled() -> bool:
    return _client is not None


async def get_json(key: str) -> Any | None:
    if _client is None:
        return None
    raw = await _client.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


async def set_json(key: str, value: Any, ttl_seconds: int | None = 300) -> None:
    if _client is None:
        return
    payload = json.dumps(value, default=str)
    if ttl_seconds:
        await _client.setex(key, int(ttl_seconds), payload)
    else:
        await _client.set(key, payload)


async def get_cached_result(run_id: str) -> dict | None:
    from app.cache.cache_keys import workflow_result

    data = await get_json(workflow_result(run_id))
    return data if isinstance(data, dict) else None


async def get_cached_intent(query_cache_key: str) -> dict | None:
    data = await get_json(query_cache_key)
    return data if isinstance(data, dict) else None


async def cache_result(run_id: str, result: dict, ttl: int = 300) -> None:
    from app.cache.cache_keys import workflow_result

    await set_json(workflow_result(run_id), result, ttl_seconds=ttl)


async def cache_intent_result(query_cache_key: str, result: dict, ttl: int = 300) -> None:
    await set_json(query_cache_key, result, ttl_seconds=ttl)


async def invalidate_user_cache(user_id: str) -> int:
    """Delete keys under mas:user:{user_id}: prefix (best-effort SCAN)."""
    if _client is None:
        return 0
    from app.cache import cache_keys

    prefix = cache_keys.user_results_prefix(user_id)
    keys: list[str] = []
    async for k in _client.scan_iter(f"{prefix}*", count=100):
        keys.append(k)
    if keys:
        return int(await _client.delete(*keys))
    return 0
