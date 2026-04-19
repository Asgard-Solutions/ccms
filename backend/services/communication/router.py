"""
Communication Service router — /api/notifications/* (HIPAA-hardened).

Admin + staff can view. By default notification rows are masked (to_address
and body obscured); passing `unmask=true` returns the full content but is
always audited.
"""
from fastapi import APIRouter, Depends, Query, Request

from core.audit import audit_emergency, audit_success
from core.db import get_db
from core.deps import require_role
from core.masking import mask_notification
from services.communication.models import NotificationPublic

router = APIRouter(prefix="/notifications", tags=["communication"])


@router.get("")
async def list_notifications(
    request: Request,
    event_type: str | None = None,
    patient_id: str | None = None,
    unmask: bool = False,
    reason: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    actor: dict = Depends(require_role("admin", "staff")),
):
    db = get_db()
    q: dict = {}
    if event_type:
        q["event_type"] = event_type
    if patient_id:
        q["patient_id"] = patient_id
    cursor = db.notifications.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    rows = [n async for n in cursor]

    if unmask and actor["role"] == "admin":
        shaped = rows
        await audit_emergency(
            actor, action="notification.unmasked",
            entity_type="notification", entity_id="list",
            reason=(reason or "admin review"),
            request=request,
            metadata={"count": len(rows)},
        )
    else:
        shaped = [mask_notification(n) for n in rows]
        await audit_success(
            actor, "notification.list_viewed", request,
            metadata={"count": len(rows), "unmasked": False},
        )
    # Always include `unmasked` flag so the UI can show an indicator.
    for row in shaped:
        row.setdefault("unmasked", unmask and actor["role"] == "admin")
    return shaped
