"""
Prometheus metrics for CCMS.

Exposed at `GET /api/metrics` in the Prometheus text-exposition format.

Design notes:
  - We use a dedicated registry (not the default global one) so that unit
    tests can reset counters between cases without touching library globals.
  - The same counters are used by `core/cache.py`, `core/rate_limit.py`, and
    `core/db.py` — every in-process counter there also `.inc()`s the matching
    Prometheus counter. That way `/api/perf/stats` and `/api/metrics` never
    drift out of sync.
  - `redis_up` is a Gauge we refresh on each scrape (cheap ping + short
    timeout; falls back to 0 if Redis is unreachable).
  - In production you SHOULD restrict /api/metrics at the ingress layer to
    your Prometheus scraper's IP. The endpoint contains no PHI — only
    operational counters — but it is still internal-only by convention.
"""
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

registry = CollectorRegistry()

# ---------- Cache ----------
cache_hits_total = Counter(
    "ccms_cache_hits_total", "Number of Redis cache hits", registry=registry
)
cache_misses_total = Counter(
    "ccms_cache_misses_total", "Number of Redis cache misses", registry=registry
)
cache_sets_total = Counter(
    "ccms_cache_sets_total", "Number of Redis cache writes", registry=registry
)
cache_invalidations_total = Counter(
    "ccms_cache_invalidations_total",
    "Number of cache invalidation calls (per-prefix or explicit)",
    registry=registry,
)
cache_errors_total = Counter(
    "ccms_cache_errors_total",
    "Number of cache operations that failed (e.g. JSON parse)",
    registry=registry,
)

# ---------- DB routing ----------
db_queries_total = Counter(
    "ccms_db_queries_total",
    "Number of DB accesses by routing class",
    labelnames=("route",),
    registry=registry,
)

# ---------- Rate limit ----------
rate_limit_blocks_total = Counter(
    "ccms_rate_limit_blocks_total",
    "Number of requests blocked by the IP rate limiter",
    labelnames=("source",),  # "redis" or "local"
    registry=registry,
)

# ---------- Redis health ----------
redis_up = Gauge(
    "ccms_redis_up",
    "1 if Redis responded to PING on the last scrape, 0 otherwise",
    registry=registry,
)

# ---------- HTTP ----------
http_request_duration_seconds = Histogram(
    "ccms_http_request_duration_seconds",
    "HTTP request duration in seconds",
    labelnames=("method", "path_prefix", "status_class"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=registry,
)


async def render() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics response."""
    # Keep redis_up fresh.
    from core.redis_client import ping as redis_ping
    try:
        alive = bool(await redis_ping())
    except Exception:
        alive = False
    redis_up.set(1 if alive else 0)
    return generate_latest(registry), CONTENT_TYPE_LATEST
