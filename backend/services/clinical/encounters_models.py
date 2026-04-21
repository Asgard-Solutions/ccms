"""Clinical encounter models — Phase 3 appointment-launched encounter shell.

`clinical_encounters` is the richer, appointment-bound encounter shell.
One encounter is created per appointment launch (the convenience POST on
`/appointments/{aid}/clinical/encounters` redirects to the existing
encounter if one already exists and is not cancelled, so retry clicks
are safe). The encounter itself is patient-owned — the authoritative
routes live under `/patients/{pid}/clinical/encounters/*`.

Exception workflow:
  When a provider needs to document against a cancelled or no-showed
  appointment (same-day documentation despite schedule changes), the
  launch must include a structured `exception_reason`. The resulting
  encounter carries `is_exception=True` plus the launcher's id / time
  and the original appointment status at launch, so chart review can
  see exactly what rule was bent and why.

Relational mirror:
    clinical_encounters (
      id                         UUID PRIMARY KEY,
      tenant_id                  UUID NOT NULL,
      location_id                UUID,
      patient_id                 UUID NOT NULL,
      appointment_id             UUID NOT NULL,
      provider_id                UUID,
      episode_id                 UUID,
      encounter_type             VARCHAR(32) NOT NULL,
      status                     VARCHAR(32) NOT NULL,
      date_of_service            TIMESTAMPTZ NOT NULL,
      scheduled_start            TIMESTAMPTZ NOT NULL,
      scheduled_end              TIMESTAMPTZ NOT NULL,
      scheduled_duration_min     INT,
      actual_start               TIMESTAMPTZ,
      actual_end                 TIMESTAMPTZ,
      appointment_snapshot       JSONB NOT NULL,   -- frozen at launch
      appointment_status_at_launch VARCHAR(32) NOT NULL,
      is_exception               BOOLEAN NOT NULL DEFAULT false,
      exception_reason           VARCHAR(1000),
      exception_invoked_by       UUID,
      exception_invoked_at       TIMESTAMPTZ,
      notes                      TEXT,
      completed_at               TIMESTAMPTZ,
      completed_by               UUID,
      cancelled_at               TIMESTAMPTZ,
      cancelled_reason           VARCHAR(500),
      created_at, updated_at, created_by, updated_by
    );
    UNIQUE (tenant_id, appointment_id) WHERE status != 'cancelled';
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EncounterType = Literal[
    "new_patient_exam",
    "follow_up",
    "re_evaluation",
    "treatment_visit",
]

EncounterStatus = Literal["in_progress", "completed", "cancelled"]


class EncounterLaunchRequest(BaseModel):
    """POST /api/appointments/{aid}/clinical/encounters

    `exception_reason` is REQUIRED when launching against a cancelled
    appointment; ignored otherwise. The endpoint re-uses an existing
    non-cancelled encounter for the same appointment so retry clicks
    are idempotent from the user's perspective."""

    model_config = ConfigDict(extra="forbid")

    encounter_type: EncounterType
    episode_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)
    exception_reason: str | None = Field(default=None, max_length=1000)


class EncounterUpdate(BaseModel):
    """PATCH /api/patients/{pid}/clinical/encounters/{eid}"""

    model_config = ConfigDict(extra="forbid")
    encounter_type: EncounterType | None = None
    episode_id: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class EncounterComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actual_start: str | None = None  # ISO
    actual_end: str | None = None    # ISO
    notes: str | None = Field(default=None, max_length=2000)


class EncounterCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


class AppointmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    appointment_id: str
    patient_id: str
    provider_id: str | None = None
    location_id: str | None = None
    start_time: str
    end_time: str
    status: str
    reason: str | None = None


class EncounterPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    appointment_id: str
    provider_id: str | None = None
    provider_name: str | None = None
    episode_id: str | None = None
    episode_title: str | None = None
    encounter_type: EncounterType
    status: EncounterStatus
    date_of_service: str
    scheduled_start: str
    scheduled_end: str
    scheduled_duration_min: int | None = None
    actual_start: str | None = None
    actual_end: str | None = None
    appointment_snapshot: AppointmentSnapshot
    appointment_status_at_launch: str
    is_exception: bool = False
    exception_reason: str | None = None
    exception_invoked_by: str | None = None
    exception_invoked_at: str | None = None
    notes: str | None = None
    completed_at: str | None = None
    completed_by: str | None = None
    cancelled_at: str | None = None
    cancelled_reason: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class EncounterLaunchResult(BaseModel):
    """Wraps the encounter with an `existed` flag so the frontend can route
    identically whether the click resulted in a brand-new encounter or a
    retry that reused the in-progress one."""

    model_config = ConfigDict(extra="ignore")
    encounter: EncounterPublic
    existed: bool
