"""
mcp-server/tools/redis_cache.py
─────────────────────────────────────────────────────────────────────────────
Redis cache tool — agents can cache expensive results without importing Redis.

WHY CACHE IN AGENTS?
  LLM calls + DB queries are expensive (time + money). When 10 users ask
  similar questions in a session, re-running the full agent chain is wasteful.
  Cache strategy: check Redis first → on miss, run agent → store in Redis.

SUPPORTED OPERATIONS:
  get      — retrieve a value by key
  set      — store a value (with optional TTL in seconds)
  delete   — remove a key
  exists   — check if a key exists (returns bool)
  scan     — find keys matching a pattern (e.g. "leads:*")
  flush_prefix — delete all keys matching a prefix (for cache invalidation)

KEY NAMING CONVENTION:
  Use {namespace}:{entity}:{id} for predictable, invalidatable keys.
  Examples:
    "workflow:complaint:session-abc"
    "leads:enriched:user-42"
    "search:results:hash-of-query"

CALLED BY AGENTS LIKE:
  # Cache a result
  await call_tool("mcp.redis_cache", {
      "operation": "set",
      "key": "leads:summary:user-42",
      "value": {"count": 15, "hot": 3},
      "ttl": 300                         # 5 minutes
  })

  # Check cache before running expensive query
  result = await call_tool("mcp.redis_cache", {
      "operation": "get",
      "key": "leads:summary:user-42"
  })
  if result.data is not None:
      return result.data    # cache hit — skip the DB query
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from mcp_server.contracts import ToolResult

try:
    import redis.asyncio as aioredis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


_redis_pool: Any = None   # module-level connection pool (singleton)


def _get_redis() -> "aioredis.Redis":
    """
    Return the async Redis client, creating it once per process.
    Uses a connection pool so we don't reconnect on every tool call.
    """
    global _redis_pool
    if not _REDIS_AVAILABLE:
        raise RuntimeError("redis package not installed. Run: pip install redis[hiredis]")
    if _redis_pool is None:
        url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        _redis_pool = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,         # always get str back, not bytes
            max_connections=20,
        )
    return _redis_pool


async def run(params: dict[str, Any]) -> ToolResult:
    """Dispatcher — routes to the correct Redis operation."""
    operation = params.get("operation", "get")
    start = time.perf_counter()

    try:
        redis = _get_redis()
        data = await _dispatch(redis, operation, params)
        duration_ms = (time.perf_counter() - start) * 1000
        return ToolResult.ok(data, tool_name="mcp.redis_cache", duration_ms=duration_ms)

    except Exception as exc:
        return ToolResult.fail(
            f"Redis error ({operation}): {exc}", tool_name="mcp.redis_cache"
        )


async def _dispatch(redis: "aioredis.Redis", operation: str, params: dict) -> Any:
    if operation == "get":
        return await _get(redis, params)
    elif operation == "set":
        return await _set(redis, params)
    elif operation == "delete":
        return await _delete(redis, params)
    elif operation == "exists":
        return await _exists(redis, params)
    elif operation == "scan":
        return await _scan(redis, params)
    elif operation == "flush_prefix":
        return await _flush_prefix(redis, params)
    else:
        raise ValueError(
            f"Unknown operation: {operation}. "
            "Use: get, set, delete, exists, scan, flush_prefix"
        )


async def _get(redis: "aioredis.Redis", params: dict) -> Any:
    """
    Returns the cached value, or None on miss.
    Automatically deserializes JSON so agents receive Python objects.
    """
    key = params.get("key")
    if not key:
        raise ValueError("get requires: key")
    raw = await redis.get(key)
    if raw is None:
        return None                        # explicit cache miss signal
    try:
        return json.loads(raw)             # deserialize stored JSON
    except json.JSONDecodeError:
        return raw                         # plain string, return as-is


async def _set(redis: "aioredis.Redis", params: dict) -> bool:
    """
    Store value, serialized to JSON, with optional TTL.
    Returns True on success.
    """
    key = params.get("key")
    value = params.get("value")
    ttl = params.get("ttl")               # seconds; None = no expiry

    if not key:
        raise ValueError("set requires: key")
    if value is None:
        raise ValueError("set requires: value")

    serialized = json.dumps(value, default=str)  # default=str handles datetime etc.

    if ttl:
        await redis.setex(key, int(ttl), serialized)
    else:
        await redis.set(key, serialized)
    return True


async def _delete(redis: "aioredis.Redis", params: dict) -> int:
    """Returns number of keys deleted (0 or 1)."""
    key = params.get("key")
    if not key:
        raise ValueError("delete requires: key")
    return await redis.delete(key)


async def _exists(redis: "aioredis.Redis", params: dict) -> bool:
    key = params.get("key")
    if not key:
        raise ValueError("exists requires: key")
    return bool(await redis.exists(key))


async def _scan(redis: "aioredis.Redis", params: dict) -> list[str]:
    """
    Find all keys matching a glob pattern.
    Example: pattern="leads:*" returns ["leads:1", "leads:2", ...]

    WARNING: SCAN iterates the keyspace. Fine for dev/small datasets.
    For production with millions of keys, use a dedicated index.
    """
    pattern = params.get("pattern", "*")
    count = params.get("count", 100)       # hint to Redis, not a hard limit
    keys = []
    async for key in redis.scan_iter(pattern, count=count):
        keys.append(key)
    return keys


async def _flush_prefix(redis: "aioredis.Redis", params: dict) -> int:
    """
    Delete ALL keys matching a prefix. Used for cache invalidation.
    Example: flush "leads:*" when you update lead data.
    Returns number of deleted keys.
    """
    prefix = params.get("prefix")
    if not prefix:
        raise ValueError("flush_prefix requires: prefix")
    pattern = f"{prefix}*"
    keys = []
    async for key in redis.scan_iter(pattern):
        keys.append(key)
    if keys:
        return await redis.delete(*keys)
    return 0