"""
services/billing/events.py — Claim event stream emitter.

A narrow write-side helper around the `claim_events` collection.
Callers use it instead of writing documents directly so:

  * Tenant stamping goes through `stamp_for_write` consistently.
  * `event_type` is validated against the canonical Literal in
    `models.ClaimEventType`.
  * Schema evolution (indexes, retention) happens in one place.

Read path is plain `db.claim_events.find({...}, {"_id": 0})` — no
need for a reader helper yet.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, get_args

from core.tenancy import TenantContext
from core.tenant_scope import stamp_for_write
from services.billing.models import ClaimEventType

log = logging.getLogger("ccms.billing.events")

# Accept-list derived from the Literal so we fail fast on typos instead
# of silently writing an unsupported event type.
_ALLOWED_EVENTS = set(get_args(ClaimEventType))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def emit_claim_event(
    db,
    ctx: TenantContext,
    *,
    claim_id: str,
    event_type: str,
    actor_id: str | None = None,
    submission_id: str | None = None,
    remittance_id: str | None = None,
    adapter_route: str | None = None,
    denial_code: str | None = None,
    payload: dict[str, Any] | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    occurred_at: str | None = None,
    location_id: str | None = None,
) -> dict:
    """Append one `claim_events` row. Returns the persisted document.

    Never raises for caller mistakes in the payload dict (we just
    store it); DOES raise ValueError on an unknown `event_type` so
    callers hit the error in tests instead of production.
    """
    if event_type not in _ALLOWED_EVENTS:
        raise ValueError(
            f"Unknown claim event type: {event_type!r}. "
            f"Allowed: {sorted(_ALLOWED_EVENTS)}"
        )

    now = _now_iso()
    doc = stamp_for_write({
        "id": str(uuid.uuid4()),
        "claim_id": claim_id,
        "event_type": event_type,
        "submission_id": submission_id,
        "remittance_id": remittance_id,
        "adapter_route": adapter_route,
        "denial_code": denial_code,
        "payload": payload or None,
        "from_status": from_status,
        "to_status": to_status,
        "occurred_at": occurred_at or now,
        "recorded_by": actor_id,
        "created_at": now,
    }, ctx, location_id=location_id)
    try:
        await db.claim_events.insert_one(doc)
    except Exception:
        # The event stream is advisory — do NOT let an event failure
        # block a mutation. We still log loud so ops sees it.
        log.exception(
            "billing.claim_event.write_failed",
            extra={"claim_id": claim_id, "event_type": event_type},
        )
    return {k: v for k, v in doc.items() if k != "_id"}
