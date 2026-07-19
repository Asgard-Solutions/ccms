"""Sandbox clearinghouse ack simulator.

After a sandbox-mode submission lands (`adapter_status="queued"`,
`sandbox=True`), this module fires a delayed asyncio task that walks
the submission through synthetic 999 / 277CA / outcome events. The
events flow through the same `emit_claim_event` plumbing as a real
clearinghouse, so the ClaimDetail timeline + status pill update in
real time without any real network traffic.

Timeline (defaults — tuned for a snappy demo, not for realism):
    +5s  : ack_999_accepted
    +10s : ack_277ca_accepted
    +15s : outcome_recorded (accepted)
    +20s : era_posted (paid)

Production-mode submissions are routed through the real ack pollers
(`ack_poller.py`) and skipped here entirely.

This module is intentionally fire-and-forget per submission. Restarts
abort any in-flight simulation; a real production system would
persist a job ledger, but for sandbox demo this is fine.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from core.tenancy import TenantContext, tenant_db
from services.billing.events import emit_claim_event

log = logging.getLogger("ccms.billing.sandbox_simulator")

# Step delays in seconds. Adjustable for tests via `_DEFAULT_DELAYS`
# below — the real-world cycle would be days, not seconds.
_DEFAULT_DELAYS = {
    "ack_999": 5,
    "ack_277ca": 10,
    "outcome": 15,
    "era": 20,
}


def schedule_sandbox_simulation(
    *,
    tenant_id: str,
    location_id: str | None,
    claim_id: str,
    submission_id: str,
    adapter_route: str,
    delays: dict[str, int] | None = None,
) -> asyncio.Task:
    """Schedule the synthetic ack walk. Returns the asyncio.Task so
    tests can `await` completion. Caller never blocks."""
    return asyncio.create_task(
        _run(
            tenant_id=tenant_id,
            location_id=location_id,
            claim_id=claim_id,
            submission_id=submission_id,
            adapter_route=adapter_route,
            delays=delays or _DEFAULT_DELAYS,
        ),
        name=f"sandbox_acks:{submission_id[:8]}",
    )


async def _run(
    *,
    tenant_id: str,
    location_id: str | None,
    claim_id: str,
    submission_id: str,
    adapter_route: str,
    delays: dict[str, int],
) -> None:
    db = tenant_db(tenant_id)
    ctx = TenantContext.for_background(tenant_id, actor="sandbox-simulator")

    base_meta = {
        "claim_id": claim_id, "submission_id": submission_id,
        "adapter_route": adapter_route, "sandbox": True,
    }

    try:
        # 999 — functional ack from the receiver
        await asyncio.sleep(delays["ack_999"])
        await _emit(
            db, ctx, claim_id=claim_id, submission_id=submission_id,
            adapter_route=adapter_route,
            event_type="ack_999_accepted",
            payload={"synthetic": True,
                     "ack_kind": "999",
                     "received_at": _now_iso()},
            location_id=location_id,
        )

        # 277CA — claim acknowledgment
        await asyncio.sleep(delays["ack_277ca"] - delays["ack_999"])
        await _emit(
            db, ctx, claim_id=claim_id, submission_id=submission_id,
            adapter_route=adapter_route,
            event_type="ack_277ca_accepted",
            payload={"synthetic": True,
                     "ack_kind": "277ca",
                     "received_at": _now_iso()},
            location_id=location_id,
        )

        # Outcome — payer adjudication
        await asyncio.sleep(delays["outcome"] - delays["ack_277ca"])
        # Update the submission row so the UI's "latest outcome" pill
        # reflects what actually happened (accepted / paid).
        await db.claim_submissions.update_one(
            {"id": submission_id, "tenant_id": tenant_id},
            {"$set": {
                "outcome": "accepted",
                "outcome_at": _now_iso(),
                "outcome_by": "sandbox-simulator",
                "updated_at": _now_iso(),
            }},
        )
        await _emit(
            db, ctx, claim_id=claim_id, submission_id=submission_id,
            adapter_route=adapter_route,
            event_type="outcome_recorded",
            payload={"synthetic": True,
                     "outcome": "accepted",
                     "received_at": _now_iso()},
            location_id=location_id,
        )

        # ERA — payment posted
        await asyncio.sleep(delays["era"] - delays["outcome"])
        await _emit(
            db, ctx, claim_id=claim_id, submission_id=submission_id,
            adapter_route=adapter_route,
            event_type="era_posted",
            payload={"synthetic": True,
                     "received_at": _now_iso()},
            location_id=location_id,
        )
        # Mirror the payment-posted onto the canonical claim row so
        # ClaimDetail's status pill flips to "paid".
        await db.claims.update_one(
            {"id": claim_id, "tenant_id": tenant_id},
            {"$set": {"status": "paid", "updated_at": _now_iso()}},
        )
    except asyncio.CancelledError:
        # App shutdown — let it propagate so the loop can exit cleanly.
        raise
    except Exception:
        # Any failure here is best-effort; we never want a sandbox
        # simulator to take down the API.
        log.exception(
            "billing.sandbox_simulator.failed",
            extra=base_meta,
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _emit(db, ctx, **kw):
    """Tiny shim so we can swap the writer in tests."""
    await emit_claim_event(db, ctx, **kw)
