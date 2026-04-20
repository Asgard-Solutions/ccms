"""
Application cache with metrics + safe invalidation.
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable

from core import metrics
from core.redis_client import get_redis, safe_call

logger = logging.getLogger("ccms.cache")

# Process-local stats (per worker). Mirrored to Prometheus in core.metrics.
_stats: dict[str, int] = {
    "hits": 0,
    "misses": 0,
    "sets": 0,
    "invalidations": 0,
    "errors": 0,
}
_lock = asyncio.Lock()


def stats() -> dict:
    return dict(_stats)


def reset_stats() -> None:
    for k in _stats:
        _stats[k] = 0


async def _bump(key: str, prom_counter=None) -> None:
    async with _lock:
        _stats[key] = _stats.get(key, 0) + 1
    if prom_counter is not None:
        try:
            prom_counter.inc()
        except Exception:
            pass


async def get(key: str):
    raw = await safe_call(lambda: get_redis().get(key))
    if raw is None:
        await _bump("misses", metrics.cache_misses_total)
        return None
    await _bump("hits", metrics.cache_hits_total)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        await _bump("errors", metrics.cache_errors_total)
        return None


async def set_(key: str, value, ttl: int) -> None:
    if ttl <= 0:
        return
    try:
        payload = json.dumps(value, default=str)
    except (TypeError, ValueError):
        await _bump("errors", metrics.cache_errors_total)
        return
    await safe_call(lambda: get_redis().set(key, payload, ex=ttl))
    await _bump("sets", metrics.cache_sets_total)


async def delete(*keys: str) -> None:
    if not keys:
        return
    await safe_call(lambda: get_redis().delete(*keys))
    await _bump("invalidations", metrics.cache_invalidations_total)


async def invalidate_prefix(prefix: str, batch: int = 200) -> int:
    client = get_redis()
    if client is None:
        return 0
    count = 0
    try:
        cursor = 0
        while True:
            cursor, keys = await client.scan(cursor=cursor, match=f"{prefix}*", count=batch)
            if keys:
                await client.delete(*keys)
                count += len(keys)
            if cursor == 0:
                break
        if count:
            await _bump("invalidations", metrics.cache_invalidations_total)
        return count
    except Exception as exc:
        logger.warning("invalidate_prefix(%s) failed: %s", prefix, exc)
        await _bump("errors", metrics.cache_errors_total)
        return 0


async def get_or_set(key: str, ttl: int, fetch: Callable[[], Awaitable]) -> object:
    cached = await get(key)
    if cached is not None:
        return cached
    fresh = await fetch()
    if fresh is not None:
        await set_(key, fresh, ttl)
    return fresh
