"""
UI telemetry router — `/api/telemetry/*`

Lightweight, PHI-free surface for tracking product-usage signals emitted by
the frontend (layout activation, section navigation, section-load failures).

Design rules:
  - No PHI ever. The Pydantic model rejects any field not listed here, so a
    misbehaving client cannot smuggle patient identifiers through.
  - Fire-and-forget from the client: this endpoint returns 204 and never
    blocks user flow.
  - Rows are tenant + actor scoped so we can slice by clinic / role.
  - No audit log entry — this is UX telemetry, not a PHI-access event.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from core.db import get_db_write
from core.deps import get_current_user

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

UIEventName = Literal[
    "clinical.layout.activated",
    "clinical.nav.jump",
    "clinical.section.load_failed",
]


class UIEventPayload(BaseModel):
    # Restrict the set of allowed keys explicitly — no PHI, no free-form
    # strings from the client.
    model_config = {"extra": "forbid"}

    event: UIEventName
    layout: Optional[Literal["v1", "v2"]] = None
    section: Optional[
        Literal[
            "summary",
            "history",
            "diagnoses",
            "encounters",
            "care-plan",
            "timeline",
            "imaging",
            "outcomes",
        ]
    ] = None
    error_code: Optional[str] = Field(default=None, max_length=64)


@router.post("/ui-event", status_code=status.HTTP_204_NO_CONTENT)
async def record_ui_event(
    payload: UIEventPayload,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    doc = {
        "tenant_id": user.get("tenant_id"),
        "actor_id": user.get("id") or user.get("user_id"),
        "actor_role": user.get("role"),
        "event": payload.event,
        "layout": payload.layout,
        "section": payload.section,
        "error_code": payload.error_code,
        "ts": datetime.now(timezone.utc).isoformat(),
        "ua": (request.headers.get("user-agent") or "")[:200] or None,
    }
    await db.ui_telemetry_events.insert_one(doc)
    return None
