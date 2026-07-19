"""Background loop — periodically processes due payment schedules
across every tenant.

Runs as an asyncio task started in `server.on_startup` and cancelled
in `server.on_shutdown`. Tick interval defaults to 60s; configurable
via `HELCIM_SCHEDULER_INTERVAL_SECONDS` env var (1-3600). The loop
is best-effort — exceptions are logged and the next tick still
fires on schedule.
"""
from __future__ import annotations

import asyncio
import logging
import os

from core.tenancy import tenant_db
from services.billing.helcim.scheduler import process_due_schedules

logger = logging.getLogger("ccms.billing.helcim.worker")

DEFAULT_INTERVAL = 60


def _interval_seconds() -> int:
    try:
        v = int(os.environ.get("HELCIM_SCHEDULER_INTERVAL_SECONDS") or DEFAULT_INTERVAL)
    except ValueError:
        return DEFAULT_INTERVAL
    return max(1, min(3600, v))


async def _all_tenant_ids() -> list[str]:
    # `tenants` lives in the shared admin DB — `tenant_db(None)`.
    db = tenant_db(None)
    rows = await db.tenants.find(
        {"status": {"$ne": "archived"}}, {"_id": 0, "id": 1},
    ).to_list(length=10_000)
    return [r["id"] for r in rows if r.get("id")]


async def _tick_once() -> None:
    try:
        tenants = await _all_tenant_ids()
    except Exception as e:
        logger.warning("scheduler.worker failed to enumerate tenants: %s", e)
        return
    for tenant_id in tenants:
        try:
            outcomes = await process_due_schedules(tenant_id)
            if outcomes:
                logger.info(
                    "scheduler.worker tenant=%s processed=%d",
                    tenant_id, len(outcomes),
                )
        except Exception as e:
            logger.exception("scheduler.worker tenant=%s err=%s", tenant_id, e)


async def run_forever() -> None:
    interval = _interval_seconds()
    logger.info("scheduler.worker started (interval=%ds)", interval)
    while True:
        try:
            await _tick_once()
        except asyncio.CancelledError:
            logger.info("scheduler.worker cancelled")
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("scheduler.worker tick failed: %s", e)
        await asyncio.sleep(interval)
