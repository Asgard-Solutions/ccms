"""Initial Exam models — Phase 4.

A chiropractic Initial Exam is a specialized, structured chart note spawned
from an in-progress `clinical_encounter`. Exactly one Initial Exam per
encounter; future amendment/addendum workflows may relax that later.

Storage shape (MongoDB `clinical_initial_exams`, PostgreSQL-ready):

    clinical_initial_exams (
      id                 UUID PRIMARY KEY,
      tenant_id          UUID NOT NULL,
      location_id        UUID,
      patient_id         UUID NOT NULL,
      encounter_id       UUID NOT NULL UNIQUE,  -- one exam per encounter
      appointment_id     UUID,
      provider_id        UUID,
      episode_id         UUID,
      date_of_service    TIMESTAMPTZ NOT NULL,
      status             VARCHAR(16) NOT NULL DEFAULT 'draft',
      template_id        UUID,
      template_snapshot  JSONB NOT NULL,        -- frozen at create
      history            JSONB NOT NULL DEFAULT '{}',
      examination        JSONB NOT NULL DEFAULT '{}',
      assessment         JSONB NOT NULL DEFAULT '{}',
      diagnosis_ids      UUID[]  NOT NULL DEFAULT '{}',
      new_diagnoses      JSONB   NOT NULL DEFAULT '[]',
      prefilled_from_chart_at TIMESTAMPTZ,
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

EXAM_STATUS = Literal["draft", "sign_ready", "signed"]


# History is mostly free-text — one field per classic H&P section.
class ExamHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chief_complaint: str | None = Field(default=None, max_length=2000)
    history_of_present_illness: str | None = Field(default=None, max_length=6000)
    onset_mechanism: str | None = Field(default=None, max_length=2000)
    medications: str | None = Field(default=None, max_length=2000)
    allergies: str | None = Field(default=None, max_length=1000)
    past_medical_history: str | None = Field(default=None, max_length=4000)
    past_surgical_history: str | None = Field(default=None, max_length=2000)
    family_history: str | None = Field(default=None, max_length=2000)
    social_history: str | None = Field(default=None, max_length=2000)
    occupation_activity: str | None = Field(default=None, max_length=2000)
    review_of_systems: str | None = Field(default=None, max_length=4000)


class Vitals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blood_pressure: str | None = None        # "120/80"
    pulse_bpm: int | None = Field(default=None, ge=20, le=260)
    respiratory_rate: int | None = Field(default=None, ge=4, le=80)
    temperature_f: float | None = Field(default=None, ge=80, le=115)
    height_in: float | None = Field(default=None, ge=10, le=108)
    weight_lb: float | None = Field(default=None, ge=5, le=1000)
    o2_sat_pct: int | None = Field(default=None, ge=50, le=100)


class RegionROM(BaseModel):
    model_config = ConfigDict(extra="ignore")
    flexion: str | None = None
    extension: str | None = None
    left_rotation: str | None = None
    right_rotation: str | None = None
    left_lateral_flexion: str | None = None
    right_lateral_flexion: str | None = None
    notes: str | None = None


class RangeOfMotion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cervical: RegionROM | None = None
    thoracic: RegionROM | None = None
    lumbar: RegionROM | None = None
    shoulders: RegionROM | None = None
    hips: RegionROM | None = None


class OrthopedicTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    region: str | None = Field(default=None, max_length=80)
    result: Literal["positive", "negative", "equivocal"] | None = None
    notes: str | None = Field(default=None, max_length=500)


class MuscleStrengthEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    muscle: str = Field(min_length=1, max_length=100)
    grade: int | None = Field(default=None, ge=0, le=5)
    side: Literal["left", "right", "bilateral"] | None = None
    notes: str | None = Field(default=None, max_length=300)


class ExamExamination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vitals: Vitals | None = None
    observation_inspection: str | None = Field(default=None, max_length=3000)
    posture: str | None = Field(default=None, max_length=2000)
    gait: str | None = Field(default=None, max_length=2000)
    palpation_findings: str | None = Field(default=None, max_length=4000)
    segmental_spinal_findings: str | None = Field(default=None, max_length=4000)
    range_of_motion: RangeOfMotion | None = None
    orthopedic_tests: list[OrthopedicTest] = Field(default_factory=list)
    neurologic_findings: str | None = Field(default=None, max_length=4000)
    muscle_strength: list[MuscleStrengthEntry] = Field(default_factory=list)
    sensory_reflex_findings: str | None = Field(default=None, max_length=4000)


class ExamAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    functional_limitations: str | None = Field(default=None, max_length=3000)
    assessment_summary: str | None = Field(default=None, max_length=4000)
    initial_clinical_impression: str | None = Field(default=None, max_length=4000)
    treatment_recommendations: str | None = Field(default=None, max_length=4000)


# New diagnoses may be added during the exam; they are materialized as real
# `clinical_diagnoses` rows at sign time. Duplicates against the patient's
# existing active problem list are de-duped.
class NewDiagnosisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icd10_code: str = Field(min_length=2, max_length=10)
    label: str = Field(min_length=1, max_length=300)
    body_region: str | None = Field(default=None, max_length=80)
    laterality: Literal["left", "right", "bilateral", "midline"] | None = None
    chronicity: Literal["acute", "subacute", "chronic"] | None = None
    is_primary: bool = False


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class InitialExamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encounter_id: str
    prefill_from_chart: bool = True
    template_id: str | None = None


class InitialExamUpdate(BaseModel):
    """PATCH — every field is optional. Passing None explicitly clears it."""
    model_config = ConfigDict(extra="forbid")
    history: ExamHistory | None = None
    examination: ExamExamination | None = None
    assessment: ExamAssessment | None = None
    diagnosis_ids: list[str] | None = None
    new_diagnoses: list[NewDiagnosisDraft] | None = None


class InitialExamPrefillRequest(BaseModel):
    """Explicit 'pull fresh values from the chart' trigger. Non-destructive —
    only empty fields are filled; existing provider entries remain."""
    model_config = ConfigDict(extra="forbid")
    sections: list[Literal["history", "diagnoses"]] | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class InitialExamPublic(BaseModel):
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
    date_of_service: str
    status: EXAM_STATUS
    template_id: str | None = None
    template_snapshot: dict | None = None
    history: dict = Field(default_factory=dict)
    examination: dict = Field(default_factory=dict)
    assessment: dict = Field(default_factory=dict)
    diagnosis_ids: list[str] = Field(default_factory=list)
    new_diagnoses: list[dict] = Field(default_factory=list)
    materialized_diagnosis_ids: list[str] = Field(default_factory=list)
    prefilled_from_chart_at: str | None = None
    marked_sign_ready_at: str | None = None
    marked_sign_ready_by: str | None = None
    signed_at: str | None = None
    signed_by: str | None = None
    signed_by_name: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class InitialExamNarrative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    exam_id: str
    patient_id: str
    narrative: str
    generated_at: str
