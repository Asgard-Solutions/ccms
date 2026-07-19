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
    "next-actions",
    "outcomes",
]

SourceSurface = Literal[
    "patient-clinical",
]

ActionEventName = Literal[
    "clinical_care_status_action_selected",
    "clinical_next_action_interaction",
    "clinical_outcome_suggestion_interaction",
]

# Phase 3 Slice 1 — deterministic next-action rule ids. Every id here
# maps 1:1 to a rule in `frontend/src/pages/clinical/nextActionsEngine.js`.
# Widening the list requires updating BOTH files + SCHEMA.md +
# test_next_action_telemetry.py in the same change.
NextActionId = Literal[
    "sign-unsigned-note",
    "complete-missing-documentation",
    "attach-or-link-diagnosis",
    "open-blocked-billing-readiness",
    "review-billing-warning",
    "schedule-due-or-overdue-reexam",
    "schedule-remaining-planned-visits",
    "review-missing-required-intake",
    "record-configured-outcome-measure",
]

NextActionInteraction = Literal["opened", "dismissed"]

# Phase 3 Slice 3 — configured outcome-instrument keys the app may
# surface as an optional suggestion. MUST stay in lockstep with
# `frontend/src/pages/clinical/outcomeSeriesHelpers.js`
# `SUPPORTED_INSTRUMENTS`. Widening the list requires updates to
# BOTH files + this SCHEMA.md + test_outcome_suggestion_telemetry.py.
OutcomeInstrumentKey = Literal[
    "ndi",
    "oswestry",
    "pain_vas",
    "pain_scale",
    "functional_index",
    "bournemouth_neck",
]

OutcomeSuggestionInteraction = Literal["opened", "dismissed"]


class UIActionPayload(BaseModel):
    """Union payload for the `/telemetry/ui-action` endpoint. Two shapes:

    * Care-status action — legacy shape (`action_slug` required, no
      next-action fields).
    * Next-action interaction — Phase 3 shape (`action_id` + `interaction`
      required, no `action_slug`).

    We keep the two shapes on one endpoint with `extra="forbid"` and
    validate cross-field consistency in the validator below so a payload
    that mixes fields (e.g., action_slug + action_id) is 422.
    """

    model_config = {"extra": "forbid"}

    event_name: ActionEventName
    section_slug: ActionSectionSlug
    source_surface: SourceSurface
    layout_version: LayoutVersion

    # Care-status shape:
    action_slug: Optional[ActionSlug] = None

    # Next-action shape:
    action_id: Optional[NextActionId] = None
    interaction: Optional[NextActionInteraction] = None

    # Outcome-suggestion shape (Phase 3 Slice 3):
    instrument_key: Optional[OutcomeInstrumentKey] = None

    def model_post_init(self, __context) -> None:  # type: ignore[override]
        # Enforce shape ↔ event_name coupling. Reject cross-field mixes.
        if self.event_name == "clinical_care_status_action_selected":
            if self.action_slug is None:
                raise ValueError("action_slug is required for care-status events")
            if (
                self.action_id is not None
                or self.interaction is not None
                or self.instrument_key is not None
            ):
                raise ValueError("next-action / outcome fields not allowed for care-status events")
            if self.section_slug != "current-care-status":
                raise ValueError("section_slug must be 'current-care-status' for care-status events")
        elif self.event_name == "clinical_next_action_interaction":
            if self.action_id is None or self.interaction is None:
                raise ValueError("action_id and interaction are required for next-action events")
            if self.action_slug is not None or self.instrument_key is not None:
                raise ValueError("action_slug / instrument_key not allowed for next-action events")
            if self.section_slug != "next-actions":
                raise ValueError("section_slug must be 'next-actions' for next-action events")
        elif self.event_name == "clinical_outcome_suggestion_interaction":
            if self.instrument_key is None or self.interaction is None:
                raise ValueError("instrument_key and interaction are required for outcome-suggestion events")
            if self.action_slug is not None or self.action_id is not None:
                raise ValueError("action_slug / action_id not allowed for outcome-suggestion events")
            if self.section_slug != "outcomes":
                raise ValueError("section_slug must be 'outcomes' for outcome-suggestion events")


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
        "source_surface": payload.source_surface,
        "layout_version": payload.layout_version,
        "ts": datetime.now(timezone.utc).isoformat(),
        "ua": (request.headers.get("user-agent") or "")[:200] or None,
    }
    if payload.action_slug is not None:
        doc["action_slug"] = payload.action_slug
    if payload.action_id is not None:
        doc["action_id"] = payload.action_id
    if payload.interaction is not None:
        doc["interaction"] = payload.interaction
    if payload.instrument_key is not None:
        doc["instrument_key"] = payload.instrument_key
    await db.ui_telemetry_actions.insert_one(doc)
    return None
