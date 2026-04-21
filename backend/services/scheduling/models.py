"""
Scheduling Service — Appointment domain model.

Future relational schema (PostgreSQL-ready):
  appointments (
    id                              UUID PRIMARY KEY,
    patient_id                      UUID NOT NULL REFERENCES patients(id),
    provider_id                     UUID NOT NULL REFERENCES users(id),
    start_time                      TIMESTAMPTZ NOT NULL,
    end_time                        TIMESTAMPTZ NOT NULL,
    reason                          VARCHAR(255),
    status                          VARCHAR(32)  NOT NULL,  -- lifecycle
    current_location_type           VARCHAR(24),            -- physical loc.
    location_updated_at             TIMESTAMPTZ,
    location_updated_by_user_id     UUID REFERENCES users(id),
    notes                           TEXT,

    -- Workflow lifecycle stamps (who + when per transition)
    checked_in_at                   TIMESTAMPTZ,
    checked_in_by_user_id           UUID REFERENCES users(id),
    ready_for_provider_at           TIMESTAMPTZ,
    ready_for_provider_by_user_id   UUID REFERENCES users(id),
    visit_started_at                TIMESTAMPTZ,
    visit_started_by_user_id        UUID REFERENCES users(id),
    ready_for_checkout_at           TIMESTAMPTZ,
    ready_for_checkout_by_user_id   UUID REFERENCES users(id),
    completed_at                    TIMESTAMPTZ,
    completed_by_user_id            UUID REFERENCES users(id),
    checked_out_at                  TIMESTAMPTZ,
    checked_out_by_user_id          UUID REFERENCES users(id),
    no_show_at                      TIMESTAMPTZ,
    no_show_by_user_id              UUID REFERENCES users(id),

    created_by  UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    CHECK (end_time > start_time)
  );
  CREATE INDEX ON appointments (provider_id, start_time);
  CREATE INDEX ON appointments (tenant_id, location_id, status);

Lifecycle status and patient physical location are intentionally separate
concepts. `status` drives the operational/clinical workflow; the patient's
physical location (`current_location_type`) tracks where they are inside the
clinic (waiting room / room / checkout counter / departed).

Backwards compatibility: existing rows may carry the legacy `cancelled`
spelling — we accept and preserve it, while all new transitions emit
`canceled` per the current naming contract.
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

# Full lifecycle status set.
AppointmentStatus = Literal[
    "scheduled",
    "confirmed",
    "checked_in",
    "ready_for_provider",
    "in_progress",
    "ready_for_checkout",
    "completed",
    "checked_out",
    "no_show",
    "canceled",
    "cancelled",  # legacy spelling retained for back-compat
]

# Patient physical location within the clinic (separate from lifecycle).
PatientLocationType = Literal[
    "not_arrived",
    "waiting_room",
    "roomed",
    "checkout",
    "departed",
]


class AppointmentCreate(BaseModel):
    patient_id: str
    provider_id: str
    start_time: datetime
    end_time: datetime
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    location_id: str | None = None
    appointment_type_id: str | None = None


class AppointmentUpdate(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    status: AppointmentStatus | None = None
    location_id: str | None = None
    appointment_type_id: str | None = None


class WorkflowTransitionRequest(BaseModel):
    """Optional payload for workflow transition endpoints.

    `reason` is audited verbatim (no PHI). `override` bypasses a handful of
    explicit soft-guards (e.g. checkout before completion) — each override is
    audited separately with the transition row.
    """
    reason: str | None = Field(default=None, max_length=255)
    override: bool = False
    # When set, also moves the patient's physical location in the same call.
    location: PatientLocationType | None = None


class CheckoutRequest(WorkflowTransitionRequest):
    """Payload for POST /api/appointments/{id}/checkout.

    Extends the generic transition request with optional operational
    checkout fields. Both `checkout_notes` and `checkout_summary` are
    stored encrypted at rest (same field-level AES-256-GCM used by the
    rest of the PHI-adjacent appointment data).
    """
    checkout_notes: str | None = Field(default=None, max_length=2000)
    checkout_summary: str | None = Field(default=None, max_length=4000)


class PatientLocationChangeRequest(BaseModel):
    location: PatientLocationType
    reason: str | None = Field(default=None, max_length=255)


class AppointmentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str | None = None
    location_id: str | None = None
    appointment_type_id: str | None = None
    appointment_type_name: str | None = None
    patient_id: str
    patient_name: str | None = None
    patient_phone: str | None = None
    provider_id: str
    provider_name: str | None = None
    start_time: str
    end_time: str
    reason: str | None = None
    notes: str | None = None
    status: AppointmentStatus
    created_by: str
    created_at: str
    updated_at: str

    # Patient physical location (separate from lifecycle).
    current_location_type: PatientLocationType | None = None
    location_updated_at: str | None = None
    location_updated_by_user_id: str | None = None

    # Workflow metadata — who + when for each transition.
    checked_in_at: str | None = None
    checked_in_by_user_id: str | None = None
    ready_for_provider_at: str | None = None
    ready_for_provider_by_user_id: str | None = None
    visit_started_at: str | None = None
    visit_started_by_user_id: str | None = None
    ready_for_checkout_at: str | None = None
    ready_for_checkout_by_user_id: str | None = None
    completed_at: str | None = None
    completed_by_user_id: str | None = None
    checked_out_at: str | None = None
    checked_out_by_user_id: str | None = None
    no_show_at: str | None = None
    no_show_by_user_id: str | None = None

    # Phase 3 — populated by GET /appointments/{id} when an encounter exists.
    clinical_encounter_id: str | None = None
    clinical_encounter_status: str | None = None

    # Intake integration — computed by hydrator from `patient_intake_forms`.
    # intake_status: not_started | in_progress | completed
    intake_status: str | None = None
    intake_completed_at: str | None = None
    intake_completed_by_name: str | None = None
    intake_form_id: str | None = None

    # Room assignment (see services/rooms). `current_room_id` is the active
    # assignment; stamps are populated on any assign/change transition.
    # `current_room_name` / `current_room_type` are hydrated for convenience.
    current_room_id: str | None = None
    current_room_name: str | None = None
    current_room_type: str | None = None
    room_assigned_at: str | None = None
    room_assigned_by_user_id: str | None = None

    # Checkout metadata (Phase 6). Notes / summary are encrypted at rest by
    # the exporter — only surfaced in response when the caller can see PHI.
    checkout_started_at: str | None = None
    checkout_started_by_user_id: str | None = None
    checkout_notes: str | None = None
    checkout_summary: str | None = None
