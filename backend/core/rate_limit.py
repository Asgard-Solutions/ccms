"""
Sliding-window IP rate limit, Redis-backed with in-process fallback.
"""
import logging
import time
from collections import defaultdict, deque

from core import metrics, security_logger
from core.redis_client import get_redis, safe_call

logger = logging.getLogger("ccms.rate_limit")

_local_buckets: dict[str, deque] = defaultdict(deque)
_local_blocks = 0


def local_blocks() -> int:
    return _local_blocks


def reset_local_state() -> int:
    """Wipe the in-process buckets and block counter.

    Returned integer is the number of buckets cleared — used by the
    dev/test reset endpoint for observability. This helper is only
    invoked by a non-production `/api/_debug/rate-limit/reset` route
    and by the pytest conftest; it never runs in production flows."""
    global _local_blocks
    cleared = len(_local_buckets)
    _local_buckets.clear()
    _local_blocks = 0
    return cleared


async def reset_redis_state() -> int:
    """Best-effort purge of `rl:` and `rlfail:` keys in Redis.

    Only used by the dev/test reset endpoint so the per-IP login /
    change-password / PIN failure counters do not leak between test
    runs. Returns the number of keys deleted (0 if Redis is down)."""
    client = get_redis()
    if client is None:
        return 0

    async def _scan_and_del():
        deleted = 0
        async for key in client.scan_iter(match="rl:*", count=500):
            await client.delete(key)
            deleted += 1
        async for key in client.scan_iter(match="rlfail:*", count=500):
            await client.delete(key)
            deleted += 1
        return deleted

    return await safe_call(_scan_and_del, default=0) or 0


async def is_allowed(key: str, *, limit: int, window_seconds: int) -> bool:
    bucket = int(time.time() // window_seconds)
    redis_key = f"rl:{key}:{window_seconds}:{bucket}"

    client = get_redis()
    if client is not None:
        async def _bump():
            pipe = client.pipeline(transaction=False)
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            return await pipe.execute()

        result = await safe_call(_bump, default=None)
        if result is not None:
            count = int(result[0])
            allowed = count <= limit
            if not allowed:
                try:
                    metrics.rate_limit_blocks_total.labels(source="redis").inc()
                except Exception:
                    pass
                security_logger.suspicious(
                    "rate_limit.block",
                    component="rate_limit",
                    key=key,
                    window_seconds=window_seconds,
                    limit=limit,
                    source="redis",
                )
            return allowed

    return _local_check(key, limit, window_seconds)


def _local_check(key: str, limit: int, window_seconds: int) -> bool:
    global _local_blocks
    now = time.time()
    cutoff = now - window_seconds
    q = _local_buckets[key]
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= limit:
        _local_blocks += 1
        try:
            metrics.rate_limit_blocks_total.labels(source="local").inc()
        except Exception:
            pass
        security_logger.suspicious(
            "rate_limit.block",
            component="rate_limit",
            key=key,
            window_seconds=window_seconds,
            limit=limit,
            source="local",
        )
        return False
    q.append(now)
    return True


async def failure_count(key: str, *, window_seconds: int) -> int:
    """Read the current failure counter for `key` without incrementing.

    Used by flows (e.g. `/change-password`) that want to gate requests
    on past failures without counting successful calls against the
    limit. Falls back to the in-process bucket length when Redis is
    unavailable."""
    bucket = int(time.time() // window_seconds)
    redis_key = f"rlfail:{key}:{window_seconds}:{bucket}"
    client = get_redis()
    if client is not None:
        result = await safe_call(lambda: client.get(redis_key), default=None)
        if result is not None:
            try:
                return int(result)
            except (TypeError, ValueError):
                return 0
    q = _local_buckets.get(f"rlfail:{key}")
    if not q:
        return 0
    now = time.time()
    cutoff = now - window_seconds
    while q and q[0] < cutoff:
        q.popleft()
    return len(q)


async def record_failure(key: str, *, window_seconds: int) -> int:
    """Bump the failure counter for `key` and return the new count.

    Uses a distinct Redis namespace (`rlfail:`) from the volume limiter
    (`rl:`) so the two never interfere. On Redis outages falls through
    to the in-process bucket."""
    bucket = int(time.time() // window_seconds)
    redis_key = f"rlfail:{key}:{window_seconds}:{bucket}"
    client = get_redis()
    if client is not None:
        async def _bump():
            pipe = client.pipeline(transaction=False)
            pipe.incr(redis_key)
            pipe.expire(redis_key, window_seconds + 1)
            return await pipe.execute()
        result = await safe_call(_bump, default=None)
        if result is not None:
            try:
                return int(result[0])
            except (TypeError, ValueError, IndexError):
                return 0
    q = _local_buckets[f"rlfail:{key}"]
    q.append(time.time())
    return len(q)
