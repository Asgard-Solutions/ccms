"""Real-mode clearinghouse ack poller.

Periodically polls every production-mode `claim_submissions` row for
999 / 277CA acknowledgments via the resolved adapter. Today the
adapters return `None` from `fetch_ack_999` / `fetch_ack_277ca`
(Phase 2c stubs) so this is effectively a no-op — but the scaffolding
runs in the background so live HTTPS transport can plug in without
touching any other code.

The poller is intentionally cheap:
  * Only runs on submissions where `sandbox = False`.
  * Only fetches acks that haven't already been recorded
    (`ack_999_received_at` / `ack_277ca_received_at`).
  * Caps each pass at 25 submissions; over-quota submissions roll to
    the next tick.
  * Exponential back-off when an adapter raises, capped at 5 minutes.

Sandbox submissions are simulated entirely by
`sandbox_ack_simulator.py`; they are skipped here.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from core.tenancy import TenantContext, tenant_db
from services.billing.clearinghouse import get_adapter_for_payer
from services.billing.events import emit_claim_event

log = logging.getLogger("ccms.billing.ack_poller")

# Tickrate. Real clearinghouses publish acks within minutes — 60 s is
# plenty for an interactive UI without overloading the upstream.
_DEFAULT_INTERVAL_SECONDS = 60
_BATCH_LIMIT = 25
_MAX_BACKOFF = 300


_running = False
_task: asyncio.Task | None = None


def start_poller(*, interval: int | None = None) -> asyncio.Task:
    """Boot the poller as a long-running asyncio task. Idempotent —
    safe to call twice. Returns the running task so the app can
    cancel it on shutdown."""
    global _running, _task
    if _task and not _task.done():
        return _task
    _running = True
    _task = asyncio.create_task(
        _loop(interval or _interval_seconds()), name="ack_poller",
    )
    return _task


def stop_poller() -> None:
    global _running, _task
    _running = False
    if _task and not _task.done():
        _task.cancel()


def _interval_seconds() -> int:
    raw = os.environ.get("CLEARINGHOUSE_ACK_POLL_INTERVAL_SECONDS")
    if raw and raw.isdigit():
        return max(10, int(raw))
    return _DEFAULT_INTERVAL_SECONDS


async def _loop(interval: int) -> None:
    backoff = interval
    while _running:
        try:
            await _tick()
            backoff = interval
        except asyncio.CancelledError:
            break
        except Exception:
            log.exception("billing.ack_poller.tick_failed")
            backoff = min(backoff * 2, _MAX_BACKOFF)
        await asyncio.sleep(backoff)


async def _tick() -> None:
    """Single pass: walk every tenant that has open production
    submissions and ask the adapter for acks."""
    # We sweep in tenant_id order so logging is deterministic. There's
    # a single Mongo client behind tenant_db, so this is O(N) over
    # active tenants — fine for small/medium clinics.
    from core.db import get_db
    db_global = get_db()
    cursor = db_global.claim_submissions.find(
        {"sandbox": {"$ne": True},
         "adapter_status": {"$in": ["queued", "accepted"]},
         "outcome": None},
        {"_id": 0, "id": 1, "tenant_id": 1, "claim_id": 1,
         "adapter_route": 1, "adapter_external_id": 1,
         "ack_999_received_at": 1, "ack_277ca_received_at": 1,
         "location_id": 1},
    ).limit(_BATCH_LIMIT)

    submissions = [s async for s in cursor]
    if not submissions:
        return

    log.info(
        "billing.ack_poller.tick_started",
        extra={"submission_count": len(submissions)},
    )

    for sub in submissions:
        await _poll_one(sub)


async def _poll_one(sub: dict) -> None:
    tenant_id = sub.get("tenant_id")
    if not tenant_id:
        return
    db = tenant_db(tenant_id)
    payer = None
    claim = await db.claims.find_one(
        {"id": sub["claim_id"], "tenant_id": tenant_id},
        {"_id": 0, "payer_id": 1},
    )
    if claim and claim.get("payer_id"):
        payer = await db.billing_payers.find_one(
            {"id": claim["payer_id"], "tenant_id": tenant_id},
            {"_id": 0},
        )
    adapter = get_adapter_for_payer(payer)
    ext_id = sub.get("adapter_external_id")
    if not ext_id:
        return

    ctx = TenantContext.for_background(tenant_id, actor="ack-poller")
    now = datetime.now(timezone.utc).isoformat()

    # 999 functional ack.
    if not sub.get("ack_999_received_at"):
        try:
            ack = await adapter.fetch_ack_999(ext_id)
        except Exception:
            log.exception(
                "billing.ack_poller.fetch_999_failed",
                extra={"submission_id": sub["id"]},
            )
            ack = None
        if ack is not None:
            await db.claim_submissions.update_one(
                {"id": sub["id"], "tenant_id": tenant_id},
                {"$set": {"ack_999_received_at": now,
                          "updated_at": now}},
            )
            await emit_claim_event(
                db, ctx,
                claim_id=sub["claim_id"],
                submission_id=sub["id"],
                adapter_route=sub.get("adapter_route"),
                event_type=(
                    "ack_999_accepted" if ack.accepted
                    else "ack_999_rejected"
                ),
                payload={"received_at": now,
                         "external_id": ext_id,
                         "denial_code": ack.denial_code,
                         "message": ack.message},
                location_id=sub.get("location_id"),
            )

    # 277CA claim ack.
    if not sub.get("ack_277ca_received_at"):
        try:
            ack = await adapter.fetch_ack_277ca(ext_id)
        except Exception:
            log.exception(
                "billing.ack_poller.fetch_277ca_failed",
                extra={"submission_id": sub["id"]},
            )
            ack = None
        if ack is not None:
            await db.claim_submissions.update_one(
                {"id": sub["id"], "tenant_id": tenant_id},
                {"$set": {"ack_277ca_received_at": now,
                          "updated_at": now}},
            )
            await emit_claim_event(
                db, ctx,
                claim_id=sub["claim_id"],
                submission_id=sub["id"],
                adapter_route=sub.get("adapter_route"),
                event_type=(
                    "ack_277ca_accepted" if ack.accepted
                    else "ack_277ca_rejected"
                ),
                payload={"received_at": now,
                         "external_id": ext_id,
                         "denial_code": ack.denial_code,
                         "message": ack.message},
                location_id=sub.get("location_id"),
            )
