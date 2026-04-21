"""Re-Exam models — Phase 6.

A Re-Exam is a distinct chart artifact authored against a `re_evaluation`
encounter (or occasionally any in-progress encounter with an active plan).
It compares the patient's current state to the frozen baseline captured at
the most recent signed Initial Exam and/or the active treatment plan's
baselines + goals. The re-exam carries a provider recommendation
(continue / modify_plan / discharge / transition_maintenance).

Signed re-exams are immutable. The re-exam record NEVER mutates the
treatment plan — a `modify_plan` recommendation emits an audit event; the
provider explicitly PATCHes the plan afterward.

Storage shape (`clinical_reexams`, PostgreSQL-ready):

    clinical_reexams (
      id                      UUID PRIMARY KEY,
      tenant_id               UUID NOT NULL,
      location_id             UUID,
      patient_id              UUID NOT NULL,
      encounter_id            UUID NOT NULL UNIQUE,
      appointment_id          UUID,
      provider_id             UUID,
      episode_id              UUID,
      treatment_plan_id       UUID,
      initial_exam_id         UUID,
      prior_reexam_id         UUID,
      date_of_service         TIMESTAMPTZ NOT NULL,
      status                  VARCHAR(16) NOT NULL DEFAULT 'draft',
      visit_number_at_reexam  INT,
      baseline_snapshot       JSONB NOT NULL,    -- frozen at create
      current_findings        JSONB NOT NULL DEFAULT '{}',
      goal_progress           JSONB NOT NULL DEFAULT '[]',
      outcome_updates         JSONB NOT NULL DEFAULT '[]',
      updated_diagnosis_ids   UUID[],
      new_diagnoses           JSONB NOT NULL DEFAULT '[]',
      recommendation_decision VARCHAR(32),
      recommendation_reason   VARCHAR(2000),
      revised_plan_summary    TEXT,
      signed_at, signed_by,
      marked_sign_ready_at, marked_sign_ready_by,
      created_at/updated_at/created_by/updated_by
    );
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.clinical.exams_models import ExamExamination, NewDiagnosisDraft

REEXAM_STATUS = Literal["draft", "sign_ready", "signed"]
GOAL_PROGRESS_STATUS = Literal["on_track", "improved", "plateau", "regressed", "met"]
OUTCOME_MEASURE = Literal["ndi", "oswestry", "pain_vas", "functional_index", "custom"]
RECOMMENDATION = Literal["continue", "modify_plan", "discharge", "transition_maintenance"]


class GoalProgressEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal_id: str = Field(min_length=1)
    current_value: float | str | None = None
    status: GOAL_PROGRESS_STATUS
    note: str | None = Field(default=None, max_length=1000)


class OutcomeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    measure_type: OUTCOME_MEASURE
    label: str = Field(min_length=1, max_length=120)
    score: float | None = None
    max_score: float | None = None
    note: str | None = Field(default=None, max_length=500)


class ReExamCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    encounter_id: str


class ReExamUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_findings: ExamExamination | None = None
    goal_progress: list[GoalProgressEntry] | None = None
    outcome_updates: list[OutcomeUpdate] | None = None
    updated_diagnosis_ids: list[str] | None = None
    new_diagnoses: list[NewDiagnosisDraft] | None = None
    recommendation_decision: RECOMMENDATION | None = None
    recommendation_reason: str | None = Field(default=None, max_length=2000)
    revised_plan_summary: str | None = Field(default=None, max_length=4000)


class ReExamPublic(BaseModel):
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
    initial_exam_id: str | None = None
    prior_reexam_id: str | None = None
    date_of_service: str
    status: REEXAM_STATUS
    visit_number_at_reexam: int | None = None
    baseline_snapshot: dict = Field(default_factory=dict)
    current_findings: dict = Field(default_factory=dict)
    goal_progress: list[dict] = Field(default_factory=list)
    outcome_updates: list[dict] = Field(default_factory=list)
    updated_diagnosis_ids: list[str] = Field(default_factory=list)
    new_diagnoses: list[dict] = Field(default_factory=list)
    materialized_diagnosis_ids: list[str] = Field(default_factory=list)
    recommendation_decision: RECOMMENDATION | None = None
    recommendation_reason: str | None = None
    revised_plan_summary: str | None = None
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


class ReExamNarrative(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reexam_id: str
    patient_id: str
    narrative: str
    generated_at: str
