"""
Sliding-window IP rate limit, Redis-backed with in-process fallback.

Used as an outer guard for /api/auth/login (the per-email brute-force lockout
in MongoDB stays as the durable, audit-friendly source of truth). When Redis
is unavailable the fallback uses an in-process dict — fine for a single pod,
not a security control on its own at scale. That is acceptable because the
Mongo lockout still applies.
"""
import logging
import time
from collections import defaultdict, deque

from core.redis_client import get_redis, safe_call

logger = logging.getLogger("ccms.rate_limit")

_local_buckets: dict[str, deque] = defaultdict(deque)
_local_blocks = 0


def local_blocks() -> int:
    return _local_blocks


async def is_allowed(key: str, *, limit: int, window_seconds: int) -> bool:
    """Returns True if the call is allowed; False if it should be blocked.

    Implementation: increment a Redis counter scoped to a fixed window bucket
    so that we never need server-side scripts. With LRU eviction and short
    TTLs this is bounded in memory.
    """
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
            return count <= limit

    # Fallback: in-process bucket
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
        return False
    q.append(now)
    return True
