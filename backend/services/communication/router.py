"""
Communication Service router — /api/notifications/* (HIPAA-hardened + cached).

Default masked responses are cached for 15s in Redis. The unmask=true branch
is NEVER cached and always reads live — both because it contains cleartext
PHI, and because each unmask event must be individually audited.
"""
from fastapi import APIRouter, Depends, Query, Request

from core import cache, cache_keys
from core.audit import audit_emergency, audit_success
from core.db import get_db_read
from core.deps import require_role
from core.masking import mask_notification
from services.communication.models import NotificationPublic

router = APIRouter(prefix="/notifications", tags=["communication"])

CACHE_TTL_SECONDS = 15


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
    unmask_permitted = unmask and actor["role"] == "admin"

    async def _fetch_masked() -> list[dict]:
        db = get_db_read()
        q: dict = {}
        if event_type:
            q["event_type"] = event_type
        if patient_id:
            q["patient_id"] = patient_id
        cursor = db.notifications.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        rows = [n async for n in cursor]
        return [mask_notification(n) for n in rows]

    if unmask_permitted:
        # Always hit the DB live — the cleartext must never sit in cache.
        db = get_db_read()
        q: dict = {}
        if event_type:
            q["event_type"] = event_type
        if patient_id:
            q["patient_id"] = patient_id
        cursor = db.notifications.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
        rows = [n async for n in cursor]
        for row in rows:
            row.setdefault("unmasked", True)
        await audit_emergency(
            actor,
            action="notification.unmasked",
            entity_type="notification",
            entity_id="list",
            reason=(reason or "admin review"),
            request=request,
            metadata={"count": len(rows)},
        )
        return rows

    key = cache_keys.notifications_list(event_type, patient_id, limit)
    shaped = await cache.get_or_set(key, CACHE_TTL_SECONDS, _fetch_masked)
    for row in shaped:
        row.setdefault("unmasked", False)

    await audit_success(
        actor,
        "notification.list_viewed",
        request,
        metadata={"count": len(shaped), "unmasked": False},
    )
    return shaped
