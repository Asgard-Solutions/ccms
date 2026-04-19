"""
Communication Service router — /api/notifications/*
"""
from fastapi import APIRouter, Depends, Query

from core.db import get_db
from core.deps import require_role
from services.communication.models import NotificationPublic

router = APIRouter(prefix="/notifications", tags=["communication"])


@router.get("", response_model=list[NotificationPublic])
async def list_notifications(
    event_type: str | None = None,
    patient_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    _actor: dict = Depends(require_role("admin", "staff")),
):
    db = get_db()
    q: dict = {}
    if event_type:
        q["event_type"] = event_type
    if patient_id:
        q["patient_id"] = patient_id
    cursor = db.notifications.find(q, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [n async for n in cursor]
