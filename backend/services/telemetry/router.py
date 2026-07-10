"""
UI telemetry router — `/api/telemetry/*`

Lightweight, PHI-free surface for tracking product-usage signals emitted
by the frontend. Two endpoints:

  POST /telemetry/ui-event    — generic UX events (layout activation,
                                 navigation jumps, section-load errors).
  POST /telemetry/ui-action   — narrowly-scoped CTA selection events
                                 (`clinical_care_status_action_selected`).

Design rules (see SCHEMA.md next to this file for the full contract):
  - No PHI. Pydantic `extra="forbid"` rejects unknown keys so a
    misbehaving client cannot smuggle patient identifiers.
  - Every field is enum-restricted where possible; free-form strings
    are avoided.
  - Fire-and-forget from the client: these endpoints return 204 and
    never block user flow.
  - Rows are tenant + actor scoped so we can slice by clinic / role.
  - No audit-log entry — this is UX telemetry, not a PHI-access event.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from core.db import get_db_write
from core.deps import get_current_user

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


# --------------------------------------------------------------------
# /telemetry/ui-event  — generic UX signals
# --------------------------------------------------------------------

UIEventName = Literal[
    "clinical.layout.activated",
    "clinical.nav.jump",
    "clinical.section.load_failed",
]

SectionSlug = Literal[
    "summary",
    "history",
    "diagnoses",
    "encounters",
    "care-plan",
    "timeline",
    "imaging",
    "outcomes",
]

LayoutVersion = Literal["v1", "v2"]


class UIEventPayload(BaseModel):
    model_config = {"extra": "forbid"}

    event: UIEventName
    layout: Optional[LayoutVersion] = None
    section: Optional[SectionSlug] = None
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


# --------------------------------------------------------------------
# /telemetry/ui-action  — CTA selection events
# --------------------------------------------------------------------

# Vocabulary is deliberately narrow. Adding a new action slug requires a
# code review + SCHEMA.md update so the product side stays honest about
# what we're tracking.

ActionSlug = Literal[
    "open-encounter",
    "add-note",
    "record-outcome",
    "schedule-visit",
    "schedule-reexam",
    "review-billing-issues",
    "edit-missing-information",
]

# Section slugs allow-listed for CTA telemetry (distinct from the
# ui-event nav slugs above — this list only names surfaces that host
# tracked CTAs).
ActionSectionSlug = Literal[
    "current-care-status",
]

SourceSurface = Literal[
    "patient-clinical",
]

ActionEventName = Literal[
    "clinical_care_status_action_selected",
]


class UIActionPayload(BaseModel):
    model_config = {"extra": "forbid"}

    event_name: ActionEventName
    section_slug: ActionSectionSlug
    action_slug: ActionSlug
    source_surface: SourceSurface
    layout_version: LayoutVersion


@router.post("/ui-action", status_code=status.HTTP_204_NO_CONTENT)
async def record_ui_action(
    payload: UIActionPayload,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db = get_db_write()
    doc = {
        "tenant_id": user.get("tenant_id"),
        "actor_id": user.get("id") or user.get("user_id"),
        "actor_role": user.get("role"),
        "event_name": payload.event_name,
        "section_slug": payload.section_slug,
        "action_slug": payload.action_slug,
        "source_surface": payload.source_surface,
        "layout_version": payload.layout_version,
        "ts": datetime.now(timezone.utc).isoformat(),
        "ua": (request.headers.get("user-agent") or "")[:200] or None,
    }
    await db.ui_telemetry_actions.insert_one(doc)
    return None
