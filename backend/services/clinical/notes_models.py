"""Follow-up / Daily Visit Note models — Phase 5.

Clinical follow-up notes are daily-charting records written against
`clinical_encounters` of type `follow_up` or `treatment_visit`. Structure
first — narrative rendering (SOAP) is a projection of the structured data,
not the source of truth. One note per encounter; signed notes are immutable
in Phase 5 (amendments come in a later phase).

Storage shape (MongoDB `clinical_follow_up_notes`, PostgreSQL-ready):

    clinical_follow_up_notes (
      id                 UUID PRIMARY KEY,
      tenant_id          UUID NOT NULL,
      location_id        UUID,
      patient_id         UUID NOT NULL,
      encounter_id       UUID NOT NULL UNIQUE,      -- one per encounter
      appointment_id     UUID,
      provider_id        UUID,
      episode_id         UUID,
      treatment_plan_id  UUID,                      -- nullable (Phase 6)
      date_of_service    TIMESTAMPTZ NOT NULL,
      status             VARCHAR(16) NOT NULL DEFAULT 'draft',
      visit_number       INT,                       -- assigned at sign time
      subjective         JSONB NOT NULL DEFAULT '{}',
      objective          JSONB NOT NULL DEFAULT '{}',
      assessment         JSONB NOT NULL DEFAULT '{}',
      plan               JSONB NOT NULL DEFAULT '{}',
      copied_from_note_id UUID,
      copied_fields      JSONB NOT NULL DEFAULT '[]',
      marked_sign_ready_at TIMESTAMPTZ,
      marked_sign_ready_by UUID,
      signed_at          TIMESTAMPTZ,
      signed_by          UUID,
      created_at/updated_at/created_by/updated_by
    );
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.clinical.exams_models import Vitals

NOTE_STATUS = Literal["draft", "sign_ready", "signed"]
PAIN_CHANGE = Literal["better", "worse", "same", "fluctuating"]
ADHERENCE = Literal["yes", "partial", "no"]
RESPONSE_TO_CARE = Literal["improving", "plateau", "regressing", "new_complaint"]

# Minimum "complete" field set used by the completeness scorer. Keys map to
# dot-paths into the structured sections.
REQUIRED_FIELDS: list[str] = [
    "subjective.interval_history",
    "subjective.pain_scale_0_10",
    "assessment.response_to_care",
    "plan.treatment_rendered",
    "plan.next_visit_plan",
]


# ---------------------------------------------------------------------------
# Subjective
# ---------------------------------------------------------------------------
class NoteSubjective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    interval_history: str | None = Field(default=None, max_length=4000)
    pain_scale_0_10: int | None = Field(default=None, ge=0, le=10)
    pain_change: PAIN_CHANGE | None = None
    functional_change: str | None = Field(default=None, max_length=2000)
    adherence_home_care: ADHERENCE | None = None
    adherence_notes: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------
class RegionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body_region: str = Field(min_length=1, max_length=80)
    palpation: str | None = Field(default=None, max_length=500)
    rom_summary: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=500)


class NoteObjective(BaseModel):
    model_config = ConfigDict(extra="forbid")
    region_findings: list[RegionFinding] = Field(default_factory=list)
    reassessment_summary: str | None = Field(default=None, max_length=3000)
    vitals: Vitals | None = None


# ---------------------------------------------------------------------------
# Assessment
# ---------------------------------------------------------------------------
class NoteAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_to_care: RESPONSE_TO_CARE | None = None
    clinical_impression: str | None = Field(default=None, max_length=3000)


# ---------------------------------------------------------------------------
# Plan — structured treatment-rendered entries
# ---------------------------------------------------------------------------
class TreatmentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["adjustment", "modality", "soft_tissue", "exercise", "other"]
    description: str | None = Field(default=None, max_length=300)
    # Adjustment-specific
    segments: list[str] = Field(default_factory=list)
    technique: str | None = Field(default=None, max_length=120)
    # Modality-specific
    modality: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=80)
    duration_min: int | None = Field(default=None, ge=0, le=120)
    notes: str | None = Field(default=None, max_length=500)


class NotePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    treatment_rendered: list[TreatmentEntry] = Field(default_factory=list)
    regions_treated: list[str] = Field(default_factory=list)
    home_care_reinforcement: str | None = Field(default=None, max_length=2000)
    next_visit_plan: str | None = Field(default=None, max_length=2000)
    recommended_interval_days: int | None = Field(default=None, ge=0, le=365)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class FollowUpNoteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encounter_id: str
    copy_forward_from_note_id: str | None = None


class FollowUpNoteUpdate(BaseModel):
    """PATCH — every field optional. Only supplied sections are applied."""
    model_config = ConfigDict(extra="forbid")
    subjective: NoteSubjective | None = None
    objective: NoteObjective | None = None
    assessment: NoteAssessment | None = None
    plan: NotePlan | None = None
    treatment_plan_id: str | None = None


class CopyForwardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_note_id: str
    force: bool = False  # when False, empty fields only


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class FollowUpNoteCompleteness(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: int = 0
    filled: int = 0
    total: int = 0
    missing_fields: list[str] = Field(default_factory=list)


class FollowUpNotePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    encounter_id: str
    appointment_id: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    episode_id: str | None = None
    episode_title: str | None = None
    treatment_plan_id: str | None = None
    date_of_service: str
    status: NOTE_STATUS
    visit_number: int | None = None
    subjective: dict = Field(default_factory=dict)
    objective: dict = Field(default_factory=dict)
    assessment: dict = Field(default_factory=dict)
    plan: dict = Field(default_factory=dict)
    copied_from_note_id: str | None = None
    copied_fields: list[str] = Field(default_factory=list)
    completeness: FollowUpNoteCompleteness | None = None
    active_plan_summary: dict | None = None
    marked_sign_ready_at: str | None = None
    marked_sign_ready_by: str | None = None
    signed_at: str | None = None
    signed_by: str | None = None
    signed_by_name: str | None = None
    has_addenda: bool = False
    addendum_count: int = 0
    latest_addendum_at: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class FollowUpNoteNarrative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    note_id: str
    patient_id: str
    narrative: str
    generated_at: str


class CareTimelineEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal[
        "encounter", "initial_exam", "follow_up_note", "re_exam", "treatment_plan",
        "clinical_media", "outcome_entry", "diagnosis_change", "intake_submission",
        "addendum",
    ]
    id: str
    date_of_service: str | None = None
    status: str
    title: str
    subtitle: str | None = None
    episode_id: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    link_path: str | None = None


class CareTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    patient_id: str
    entries: list[CareTimelineEntry]
    generated_at: str
