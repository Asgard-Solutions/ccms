"""
Performance + observability endpoints (admin-only).

These give operators a quick read on cache health, DB routing balance, and
Redis reachability without needing to attach Prometheus first. They are
intentionally dependency-free; in production you would point Prometheus at a
proper /metrics endpoint instead.
"""
from fastapi import APIRouter, Depends

from core import cache, rate_limit
from core.db import routing_stats
from core.deps import require_role
from core.redis_client import ping as redis_ping

router = APIRouter(prefix="/perf", tags=["perf"])


@router.get("/stats")
async def stats(_admin: dict = Depends(require_role("admin"))):
    cache_stats = cache.stats()
    db_stats = routing_stats()
    hit_ratio = (
        round(cache_stats["hits"] / (cache_stats["hits"] + cache_stats["misses"]), 4)
        if (cache_stats["hits"] + cache_stats["misses"]) > 0
        else None
    )
    read_ratio = (
        round(db_stats["reads"] / (db_stats["reads"] + db_stats["writes"] + db_stats["read_after_write"]), 4)
        if sum(db_stats.values()) > 0
        else None
    )
    return {
        "cache": {**cache_stats, "hit_ratio": hit_ratio},
        "db": {**db_stats, "read_ratio_overall": read_ratio},
        "rate_limit": {"local_blocks": rate_limit.local_blocks()},
        "redis_alive": await redis_ping(),
    }


@router.post("/cache/reset-stats")
async def reset_stats(_admin: dict = Depends(require_role("admin"))):
    cache.reset_stats()
    return {"message": "Cache stats reset"}
