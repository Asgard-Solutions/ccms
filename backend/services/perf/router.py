"""
Performance + observability endpoints.

  - GET  /api/perf/stats            admin, process-local stats JSON
  - GET  /api/perf/connection-info  admin, read/write host verification for
                                    replica-set deployments
  - POST /api/perf/cache/reset-stats admin, zeroes the in-process counters
  - GET  /api/metrics               OPEN (no auth) Prometheus scrape endpoint
                                    — restrict at the ingress layer in prod

None of these endpoints ever return PHI; they only surface operational
counters and host/config identifiers.
"""
from fastapi import APIRouter, Depends, Response

from core import cache, metrics, rate_limit
from core.db import routing_stats, get_read_client, get_write_client
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
        round(
            db_stats["reads"]
            / (db_stats["reads"] + db_stats["writes"] + db_stats["read_after_write"]),
            4,
        )
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


@router.get("/connection-info")
async def connection_info(_admin: dict = Depends(require_role("admin"))):
    """Returns the Mongo topology the write + read clients are pointing at.

    Use this to verify a real primary+replica deployment is actually routing
    reads to a secondary:
      - `write.nodes` should include only the primary
      - `read.nodes` should list secondaries (or the replica-set members)
      - `read.read_preference` should read `secondaryPreferred` or similar

    In the local single-instance dev environment both will show one node and
    primary as the preferred read — which is the *expected* degenerate case.
    """
    write = get_write_client()
    read = get_read_client()

    def _topology(client):
        desc = client.topology_description
        return {
            "topology_type": str(desc.topology_type_name),
            "nodes": sorted(
                f"{s.address[0]}:{s.address[1]}" for s in desc.server_descriptions().values()
            ),
            "read_preference": str(client.read_preference),
        }

    return {
        "write": _topology(write),
        "read": _topology(read),
        "same_client": write is read,
    }


# ---------------- Prometheus ----------------
# Mounted at /api/metrics (no auth — Prometheus scrapers don't carry cookies).
# Restrict to the scrape subnet in production via ingress rules.

metrics_router = APIRouter(tags=["perf"])


@metrics_router.get("/metrics")
async def prometheus_metrics():
    body, content_type = await metrics.render()
    return Response(content=body, media_type=content_type)
