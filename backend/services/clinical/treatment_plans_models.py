"""Treatment Plan models — Phase 6.

Chart-level plan-of-care artifact. One active plan per episode (enforced).
Links to patient, episode, responsible provider, diagnoses. Carries
measurable goals, objective baselines, frequency/duration, home-care &
activity/work recommendations, re-exam date, discharge criteria, and
maintenance/wellness transition notes.

Storage shape (`clinical_treatment_plans`, PostgreSQL-ready):

    clinical_treatment_plans (
      id                           UUID PRIMARY KEY,
      tenant_id                    UUID NOT NULL,
      location_id                  UUID,
      patient_id                   UUID NOT NULL,
      episode_id                   UUID,
      responsible_provider_id      UUID,
      plan_status                  VARCHAR(16) NOT NULL DEFAULT 'active',
      title                        VARCHAR(300) NOT NULL,
      diagnosis_ids                UUID[],
      target_body_regions          TEXT[],
      frequency_visits_per_week    INT,
      frequency_total_visits       INT,
      expected_duration_weeks      INT,
      start_date                   TIMESTAMPTZ NOT NULL,
      re_exam_date                 DATE,
      planned_interventions        JSONB NOT NULL DEFAULT '[]',
      goals                        JSONB NOT NULL DEFAULT '[]',
      baselines                    JSONB NOT NULL DEFAULT '{}',
      home_care_recommendations    TEXT,
      activity_work_recommendations TEXT,
      discharge_criteria           TEXT,
      maintenance_transition_notes TEXT,
      discharge_reason             VARCHAR(500),
      discharged_at                TIMESTAMPTZ,
      created_at/updated_at/created_by/updated_by
    );
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PLAN_STATUS = Literal["active", "on_hold", "completed", "discharged", "cancelled"]
GOAL_STATUS = Literal["active", "met", "modified", "abandoned"]
MEASURE_TYPE = Literal["pain_scale", "functional", "rom", "outcome_score", "custom"]


class PlannedIntervention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["adjustment", "modality", "soft_tissue", "exercise", "education", "other"]
    description: str = Field(min_length=1, max_length=400)
    frequency: str | None = Field(default=None, max_length=120)


class PlanGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None  # assigned server-side if missing
    description: str = Field(min_length=1, max_length=500)
    measure_type: MEASURE_TYPE
    unit: str | None = Field(default=None, max_length=40)
    baseline_value: float | str | None = None
    target_value: float | str | None = None
    status: GOAL_STATUS = "active"
    progress_notes: str | None = Field(default=None, max_length=1000)


class FunctionalMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=120)
    value: float | str | None = None
    unit: str | None = Field(default=None, max_length=40)


class PlanBaselines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pain_scale_0_10: int | None = Field(default=None, ge=0, le=10)
    key_rom_summary: str | None = Field(default=None, max_length=2000)
    functional_measures: list[FunctionalMeasure] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)


class TreatmentPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    responsible_provider_id: str | None = None
    diagnosis_ids: list[str] = Field(default_factory=list)
    target_body_regions: list[str] = Field(default_factory=list)
    frequency_visits_per_week: int | None = Field(default=None, ge=0, le=14)
    frequency_total_visits: int | None = Field(default=None, ge=0, le=500)
    expected_duration_weeks: int | None = Field(default=None, ge=0, le=260)
    start_date: str | None = None
    re_exam_date: str | None = None
    planned_interventions: list[PlannedIntervention] = Field(default_factory=list)
    goals: list[PlanGoal] = Field(default_factory=list)
    baselines: PlanBaselines | None = None
    home_care_recommendations: str | None = Field(default=None, max_length=3000)
    activity_work_recommendations: str | None = Field(default=None, max_length=3000)
    discharge_criteria: str | None = Field(default=None, max_length=2000)
    maintenance_transition_notes: str | None = Field(default=None, max_length=2000)


class TreatmentPlanUpdate(BaseModel):
    """PATCH — every field optional."""
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=300)
    responsible_provider_id: str | None = None
    diagnosis_ids: list[str] | None = None
    target_body_regions: list[str] | None = None
    frequency_visits_per_week: int | None = Field(default=None, ge=0, le=14)
    frequency_total_visits: int | None = Field(default=None, ge=0, le=500)
    expected_duration_weeks: int | None = Field(default=None, ge=0, le=260)
    re_exam_date: str | None = None
    planned_interventions: list[PlannedIntervention] | None = None
    goals: list[PlanGoal] | None = None
    baselines: PlanBaselines | None = None
    home_care_recommendations: str | None = Field(default=None, max_length=3000)
    activity_work_recommendations: str | None = Field(default=None, max_length=3000)
    discharge_criteria: str | None = Field(default=None, max_length=2000)
    maintenance_transition_notes: str | None = Field(default=None, max_length=2000)


class TreatmentPlanSetStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_status: PLAN_STATUS
    reason: str = Field(min_length=3, max_length=500)


class TreatmentPlanProgress(BaseModel):
    model_config = ConfigDict(extra="ignore")
    visits_completed: int = 0
    total_visits: int | None = None
    percent: int | None = None


class TreatmentPlanPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    episode_id: str | None = None
    episode_title: str | None = None
    responsible_provider_id: str | None = None
    responsible_provider_name: str | None = None
    plan_status: PLAN_STATUS
    title: str
    diagnosis_ids: list[str] = Field(default_factory=list)
    target_body_regions: list[str] = Field(default_factory=list)
    frequency_visits_per_week: int | None = None
    frequency_total_visits: int | None = None
    expected_duration_weeks: int | None = None
    start_date: str
    re_exam_date: str | None = None
    planned_interventions: list[dict] = Field(default_factory=list)
    goals: list[dict] = Field(default_factory=list)
    baselines: dict = Field(default_factory=dict)
    home_care_recommendations: str | None = None
    activity_work_recommendations: str | None = None
    discharge_criteria: str | None = None
    maintenance_transition_notes: str | None = None
    discharge_reason: str | None = None
    discharged_at: str | None = None
    progress: TreatmentPlanProgress | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class TreatmentPlanSummary(BaseModel):
    """Compact projection for embedding in follow-up notes / re-exams."""
    model_config = ConfigDict(extra="ignore")
    id: str
    title: str
    plan_status: PLAN_STATUS
    frequency_visits_per_week: int | None = None
    frequency_total_visits: int | None = None
    expected_duration_weeks: int | None = None
    re_exam_date: str | None = None
    goals: list[dict] = Field(default_factory=list)
    progress: TreatmentPlanProgress | None = None
