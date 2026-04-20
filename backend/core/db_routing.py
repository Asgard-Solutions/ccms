"""
Database routing classification + replica lag guardrails.

Purpose
-------
`core.db` already exposes `get_db_write()` and `get_db_read()`, but they
rely on *developer discipline* — nothing prevents a new route from calling
`get_db_read()` for a security-sensitive read-after-write path. This
module turns that discipline into enforced policy.

The mental model
----------------
Every read has a `ReadPurpose`. Sensitive purposes are pinned to primary;
everything else may use the replica (when configured and healthy).

    WRITES_ONLY            — auth, permissions, audit_log, billing, tenants,
                             secrets, jobs queue — always primary
    READ_AFTER_WRITE       — reads within 5s of a write by same actor
    REPLICA_OK             — listings, reports, analytics, exports source data
    REPLICA_PREFERRED      — long-running / analytical workloads

Operations
----------
`route_for(purpose)` returns the `AsyncIOMotorClient` to use.
If the read replica is configured (`MONGO_READ_URL`) AND healthy AND
`READ_REPLICAS_ENABLED=true` (default true), REPLICA_OK / REPLICA_PREFERRED
go to the replica; otherwise they fall back to primary.

Health is measured from periodic probes stashed in `_replica_health`:
    - is_alive            (boolean; set by the probe loop)
    - lag_seconds         (replica lag relative to primary; -1 if unknown)
Thresholds are read from env:
    REPLICA_MAX_LAG_SECONDS   (default 5)
    REPLICA_HEALTH_TIMEOUT_S  (default 30)

When lag > threshold for N consecutive probes, `_replica_disabled_until`
is set and reads fall back to primary until the cool-off expires.

NOTE
----
This is a classification layer — the actual physical clients remain in
`core.db`. Code should NOT hold onto a client past a single call; always
ask `route_for()` so replica-lag circuit-breaks apply.
"""
from __future__ import annotations

import asyncio
import enum
import logging
import os
import time
from dataclasses import dataclass, field

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from core.db import get_read_client, get_write_client, _name

logger = logging.getLogger("ccms.db_routing")


class ReadPurpose(enum.Enum):
    WRITES_ONLY = "writes_only"
    READ_AFTER_WRITE = "read_after_write"
    REPLICA_OK = "replica_ok"
    REPLICA_PREFERRED = "replica_preferred"


# Security-sensitive collections that MUST use primary for every read.
# Encoded here so a developer cannot route these to a replica by accident.
PRIMARY_ONLY_COLLECTIONS: frozenset[str] = frozenset({
    "users", "user_roles", "role_permissions", "permission_scopes",
    "elevation_requests", "login_attempts", "password_reset_tokens",
    "audit_logs", "tenants", "jobs", "exports",
    "consent_records", "privacy_requests",
})


@dataclass
class ReplicaHealth:
    is_alive: bool = True
    lag_seconds: float = 0.0
    last_probe_at: float = 0.0
    consecutive_bad: int = 0
    disabled_until: float = 0.0


_replica_health = ReplicaHealth()


def _replicas_enabled() -> bool:
    return os.environ.get("READ_REPLICAS_ENABLED", "true").lower() != "false"


def _max_lag() -> float:
    return float(os.environ.get("REPLICA_MAX_LAG_SECONDS", "5"))


def _has_replica() -> bool:
    return bool(os.environ.get("MONGO_READ_URL"))


def _replica_is_usable() -> bool:
    if not _has_replica() or not _replicas_enabled():
        return False
    if _replica_health.disabled_until > time.time():
        return False
    if not _replica_health.is_alive:
        return False
    return _replica_health.lag_seconds <= _max_lag()


def route_for(purpose: ReadPurpose) -> AsyncIOMotorClient:
    """Return the Motor client appropriate for this read purpose."""
    if purpose in (ReadPurpose.WRITES_ONLY, ReadPurpose.READ_AFTER_WRITE):
        return get_write_client()
    if _replica_is_usable():
        return get_read_client()
    return get_write_client()


def route_db(purpose: ReadPurpose) -> AsyncIOMotorDatabase:
    return route_for(purpose)[_name()]


def safe_read(collection_name: str, purpose: ReadPurpose) -> AsyncIOMotorDatabase:
    """Higher-level helper: refuses to route a primary-only collection to a replica.

    Raises ValueError to fail loud in dev; in prod this same error 500s,
    which is the correct behaviour (broken deploy > silent security drift)."""
    if collection_name in PRIMARY_ONLY_COLLECTIONS and purpose not in (
        ReadPurpose.WRITES_ONLY, ReadPurpose.READ_AFTER_WRITE,
    ):
        raise ValueError(
            f"Collection {collection_name!r} is primary-only; use ReadPurpose.WRITES_ONLY or READ_AFTER_WRITE",
        )
    return route_db(purpose)


# ---------------------------------------------------------------------------
# Health probing — called by a background task (or Prometheus pull).
# ---------------------------------------------------------------------------

async def probe_replica_once() -> ReplicaHealth:
    """Single replica health check. Measures lag via `isMaster` / `hello`.

    The lag approximation here is simple: we read the primary's write time
    and the replica's applied optime and diff them. Motor's `admin.command`
    returns a dict from the `replSetGetStatus` command if we're on a replica
    set; otherwise we just mark lag_seconds=0 and alive=True.
    """
    global _replica_health
    _replica_health.last_probe_at = time.time()

    if not _has_replica():
        _replica_health.is_alive = False
        _replica_health.lag_seconds = -1
        return _replica_health

    try:
        client = get_read_client()
        await asyncio.wait_for(client.admin.command("ping"), timeout=2.0)
        _replica_health.is_alive = True

        try:
            status = await asyncio.wait_for(
                client.admin.command("replSetGetStatus"), timeout=2.0,
            )
            primary = next((m for m in status.get("members", []) if m.get("stateStr") == "PRIMARY"), None)
            replica = next((m for m in status.get("members", []) if m.get("stateStr") == "SECONDARY"), None)
            if primary and replica:
                p_ts = primary["optime"]["ts"].time if hasattr(primary["optime"]["ts"], "time") else None
                r_ts = replica["optime"]["ts"].time if hasattr(replica["optime"]["ts"], "time") else None
                if p_ts and r_ts:
                    _replica_health.lag_seconds = max(0.0, float(p_ts - r_ts))
        except Exception:  # noqa: BLE001
            # Standalone server or command not supported — lag is unknown.
            _replica_health.lag_seconds = 0.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("replica probe failed: %s", exc)
        _replica_health.is_alive = False
        _replica_health.lag_seconds = -1

    # Circuit breaker: flap into disabled state after 3 consecutive bad probes.
    if (not _replica_health.is_alive) or _replica_health.lag_seconds > _max_lag():
        _replica_health.consecutive_bad += 1
        if _replica_health.consecutive_bad >= 3:
            _replica_health.disabled_until = time.time() + 60
    else:
        _replica_health.consecutive_bad = 0

    return _replica_health


def replica_health() -> dict:
    return {
        "has_replica": _has_replica(),
        "enabled": _replicas_enabled(),
        "alive": _replica_health.is_alive,
        "lag_seconds": _replica_health.lag_seconds,
        "disabled_until": _replica_health.disabled_until,
        "usable": _replica_is_usable(),
        "max_lag_seconds": _max_lag(),
    }


def force_disable_replica(seconds: int = 300) -> None:
    """Operator hook — runbook step 'cut over to primary'."""
    _replica_health.disabled_until = time.time() + seconds
    logger.warning("replica forcibly disabled for %ds", seconds)
