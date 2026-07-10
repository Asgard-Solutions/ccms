"""Clinical module — Pydantic models + status / type vocabularies.

The source-of-truth collection in Phase 1 is `clinical_episode_cases`.
The other collections (`clinical_notes`, `clinical_diagnoses`,
`clinical_treatment_plans`, `clinical_outcome_entries`, `clinical_media`,
`clinical_audit_events`, `clinical_encounter_links`) are declared here so
their schemas are stable from day one — Phase 2+ will layer CRUD + UI on
top without renaming fields or migrating data.

PostgreSQL-ready relational shape:
    clinical_episode_cases (
      id                   UUID PRIMARY KEY,
      tenant_id            UUID NOT NULL,
      location_id          UUID,                         -- optional
      patient_id           UUID NOT NULL REFERENCES patients(id),
      responsible_provider_id UUID REFERENCES users(id),
      case_type            VARCHAR(32) NOT NULL,
      status               VARCHAR(32) NOT NULL,
      title                VARCHAR(200) NOT NULL,
      chief_complaint      TEXT,
      mechanism_of_injury  TEXT,
      onset_date           DATE,
      start_date           TIMESTAMPTZ NOT NULL,
      end_date             TIMESTAMPTZ,
      closed_reason        VARCHAR(400),
      tags                 JSONB NOT NULL DEFAULT '[]',
      metadata             JSONB NOT NULL DEFAULT '{}',
      created_at           TIMESTAMPTZ NOT NULL,
      updated_at           TIMESTAMPTZ NOT NULL,
      created_by           UUID,
      updated_by           UUID
    );

    -- Artifacts below are declared in Phase 1 but only get full CRUD in
    -- Phase 2+. Shape stays stable.
    clinical_notes            (id, tenant_id, patient_id, episode_id, author_id,
                               note_type, body, status, signed_at, created_at)
    clinical_diagnoses        (id, tenant_id, patient_id, episode_id,
                               icd10_code, description, onset_date, status,
                               ranking, created_at)
    clinical_treatment_plans  (id, tenant_id, patient_id, episode_id, title,
                               goals, frequency_per_week, duration_weeks,
                               status, start_date, end_date, created_at)
    clinical_outcome_entries  (id, tenant_id, patient_id, episode_id, measure,
                               score, max_score, recorded_on, notes, created_at)
    clinical_media            (id, tenant_id, patient_id, episode_id,
                               storage_key, mime_type, category, description,
                               captured_at, uploaded_by, created_at)
    clinical_audit_events     (id, tenant_id, patient_id, episode_id,
                               actor_id, event_type, entity_type, entity_id,
                               metadata, created_at)
    clinical_encounter_links  (id, tenant_id, patient_id, episode_id,
                               appointment_id, entity_type, entity_id,
                               created_at, created_by)

All `*_id` fields are UUID strings in the Mongo representation so the
migration to Postgres is mechanical.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Case-type vocabulary — chiropractic clinical reality.
CASE_TYPE = Literal[
    "new_patient_eval",
    "injury_episode",
    "recurrence",
    "maintenance",
    "mva",
    "workers_comp",
    "personal_injury",
]

# Lifecycle statuses. `active` is the default on create.
EPISODE_STATUS = Literal["active", "on_hold", "closed", "archived"]


# ---------------------------------------------------------------------------
# Episode / Case — Phase 1 primary entity
# ---------------------------------------------------------------------------
class EpisodeCaseBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_type: CASE_TYPE
    title: str = Field(min_length=1, max_length=200)
    chief_complaint: str | None = Field(default=None, max_length=2000)
    mechanism_of_injury: str | None = Field(default=None, max_length=2000)
    onset_date: str | None = Field(
        default=None, description="ISO date the condition/injury began (YYYY-MM-DD)"
    )
    start_date: str | None = Field(
        default=None,
        description="ISO datetime the episode opened; defaults to server now at creation",
    )
    responsible_provider_id: str | None = None
    location_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class EpisodeCaseCreate(EpisodeCaseBase):
    pass


class EpisodeCaseUpdate(BaseModel):
    """PATCH semantics — only fields explicitly provided are applied.

    `end_date` and `closed_reason` can only be set via the `close` transition
    endpoint, not through PATCH, to keep the state machine honest.
    """

    model_config = ConfigDict(extra="forbid")

    case_type: CASE_TYPE | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    chief_complaint: str | None = Field(default=None, max_length=2000)
    mechanism_of_injury: str | None = Field(default=None, max_length=2000)
    onset_date: str | None = None
    responsible_provider_id: str | None = None
    location_id: str | None = None
    status: Literal["active", "on_hold"] | None = None
    tags: list[str] | None = None


class EpisodeCaseClose(BaseModel):
    model_config = ConfigDict(extra="forbid")
    end_date: str | None = None  # ISO datetime; defaults to server now
    closed_reason: str = Field(min_length=3, max_length=400)

    @field_validator("closed_reason")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class EpisodeCasePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    responsible_provider_id: str | None = None
    responsible_provider_name: str | None = None
    case_type: CASE_TYPE
    status: EPISODE_STATUS
    title: str
    chief_complaint: str | None = None
    mechanism_of_injury: str | None = None
    onset_date: str | None = None
    start_date: str
    end_date: str | None = None
    closed_reason: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


# ---------------------------------------------------------------------------
# Clinical Summary — patient-level snapshot exposed to the UI shell
# ---------------------------------------------------------------------------
class ClinicalSectionCount(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int = 0
    open: int = 0
    # Phase 3 Slice 1 — nullable ISO timestamp of the most recent
    # write in this section. Used by NextActionsPanel to decide whether
    # an optional outcome-recording follow-up is due. Only populated
    # for surfaces where the notion is well-defined (currently
    # `outcomes`).
    last_recorded_at: str | None = None


class ClinicalSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")
    patient_id: str
    tenant_id: str
    episodes: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    # Placeholder counts — always zero in Phase 1; Phase 2+ will fill them in
    # as each sub-module ships CRUD. Keeping them in the response contract
    # now means the UI shell doesn't need a new round-trip when phases land.
    notes: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    diagnoses: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    treatment_plans: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    outcomes: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    media: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    encounter_links: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    encounters: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    initial_exams: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    re_exams: ClinicalSectionCount = Field(default_factory=ClinicalSectionCount)
    outcomes_snapshot: list[dict] = Field(default_factory=list)
    history_present: int = 0
    generated_at: str


# ---------------------------------------------------------------------------
# Downstream artifact shapes — Phase 2+ will attach CRUD.
# These classes are intentionally minimal; each carries tenant + patient +
# episode linkage so downstream endpoints can slot in without migrations.
# ---------------------------------------------------------------------------
class ClinicalNoteBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    appointment_id: str | None = None
    note_type: Literal["soap", "progress", "re_exam", "phone_call", "other"] = "soap"
    body: str = Field(min_length=1)
    status: Literal["draft", "signed", "amended"] = "draft"


class DiagnosisBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    icd10_code: str = Field(min_length=2, max_length=10)
    description: str = Field(min_length=1, max_length=500)
    onset_date: str | None = None
    ranking: int | None = Field(default=None, ge=1, le=12)
    status: Literal["active", "resolved", "inactive"] = "active"


class TreatmentPlanBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    goals: list[str] = Field(default_factory=list)
    frequency_per_week: int | None = Field(default=None, ge=0, le=21)
    duration_weeks: int | None = Field(default=None, ge=0, le=104)
    status: Literal["draft", "active", "completed", "discontinued"] = "draft"
    start_date: str | None = None
    end_date: str | None = None


class OutcomeEntryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    measure: str = Field(min_length=1, max_length=80)
    score: float
    max_score: float | None = None
    recorded_on: str
    notes: str | None = Field(default=None, max_length=1000)


class ClinicalMediaBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    storage_key: str
    mime_type: str
    category: Literal["xray", "mri", "ct", "photo", "video", "document", "other"] = (
        "document"
    )
    description: str | None = Field(default=None, max_length=500)
    captured_at: str | None = None


class EncounterLinkBase(BaseModel):
    """Generic linkage between an appointment and any clinical artifact.

    This is the one-stop join table that lets the UI answer questions like
    "show every clinical artifact that came out of appointment X" and
    "show every appointment that fed into episode Y" without forcing every
    artifact model to carry both appointment_id AND episode_id redundantly.
    """

    model_config = ConfigDict(extra="forbid")
    appointment_id: str
    episode_id: str | None = None
    entity_type: Literal[
        "note", "diagnosis", "treatment_plan", "outcome", "media"
    ]
    entity_id: str


class ClinicalAuditEventBase(BaseModel):
    """Clinical-module-specific audit row. Complements the global audit_logs
    collection; this one is scoped to the patient chart for fast "chart
    history" lookups without filtering the global stream.
    """

    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    event_type: str = Field(min_length=1, max_length=64)
    entity_type: str = Field(min_length=1, max_length=32)
    entity_id: str | None = None
    metadata: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
