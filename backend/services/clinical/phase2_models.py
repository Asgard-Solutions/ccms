"""Clinical history + problem-list models (Phase 2).

**Clinical history** is a single, editable, per-patient chart-level snapshot
of the intake narrative a provider needs at a glance. It is auto-seeded
*once* from the patient's most recent *completed* intake form on first read,
after which any provider edit flips the field's `source` from `"intake"` to
`"provider_edit"`. Subsequent explicit imports must not silently overwrite
provider-edited fields (see `history_router.import_history`).

**Problem list** (a.k.a. diagnoses) is stored in `clinical_diagnoses`. Each
row can optionally be linked to an episode/case for care-plan alignment, and
supports body region + laterality + chronicity + ICD-10 + a primary flag.
`is_primary=True` is auto-uniqued across (patient, episode_id-or-null) at
write time — one primary per active-episode group, one for orphan
diagnoses. Lifecycle: `active → resolved → reactivated`.

Relational mirror:
    clinical_history (
      id                    UUID PRIMARY KEY,
      tenant_id             UUID NOT NULL,
      patient_id            UUID NOT NULL UNIQUE,     -- exactly one per patient
      chief_complaint       TEXT,
      history_of_present_illness TEXT,
      onset_date            DATE,
      mechanism_of_injury   TEXT,
      pain_locations        JSONB,
      pain_radiation        TEXT,
      aggravating_factors   JSONB,
      relieving_factors     JSONB,
      severity              SMALLINT,
      prior_treatment       TEXT,
      prior_chiropractic_care BOOLEAN,
      medications           TEXT,
      allergies             TEXT,
      past_medical_history  TEXT,
      past_surgical_history TEXT,
      family_history        TEXT,
      social_history        TEXT,
      occupation            VARCHAR(200),
      activity_level        VARCHAR(32),
      accident_details      JSONB,
      work_comp_details     JSONB,
      review_of_systems     TEXT,
      red_flag_screening    JSONB,
      field_meta            JSONB NOT NULL DEFAULT '{}',  -- per-field traceability
      seeded_from_form_id   UUID,
      last_imported_at      TIMESTAMPTZ,
      created_at / updated_at / created_by / updated_by
    );

    clinical_diagnoses (
      id, tenant_id, patient_id, episode_id,
      icd10_code, label, status, is_primary, body_region, laterality,
      chronicity, onset_date, resolved_date, notes,
      created_at, updated_at, created_by, updated_by
    );
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Every field in `ClinicalHistoryBase` is optional. Lists default to None to
# keep a "never populated" signal distinct from "explicitly empty list".
class ClinicalHistoryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chief_complaint: str | None = Field(default=None, max_length=2000)
    history_of_present_illness: str | None = Field(default=None, max_length=4000)
    onset_date: str | None = None  # ISO date
    mechanism_of_injury: str | None = Field(default=None, max_length=2000)
    pain_locations: list[str] | None = None
    pain_radiation: str | None = Field(default=None, max_length=1000)
    aggravating_factors: list[str] | None = None
    relieving_factors: list[str] | None = None
    severity: int | None = Field(default=None, ge=0, le=10)
    prior_treatment: str | None = Field(default=None, max_length=2000)
    prior_chiropractic_care: bool | None = None
    medications: str | None = Field(default=None, max_length=2000)
    allergies: str | None = Field(default=None, max_length=1000)
    past_medical_history: str | None = Field(default=None, max_length=4000)
    past_surgical_history: str | None = Field(default=None, max_length=2000)
    family_history: str | None = Field(default=None, max_length=2000)
    social_history: str | None = Field(default=None, max_length=2000)
    occupation: str | None = Field(default=None, max_length=200)
    activity_level: str | None = Field(default=None, max_length=64)
    accident_details: dict | None = None  # { date_of_injury, location, carrier... }
    work_comp_details: dict | None = None
    review_of_systems: str | None = Field(default=None, max_length=4000)
    red_flag_screening: dict | None = None  # { flag_name: bool }


class ClinicalHistoryPatch(ClinicalHistoryBase):
    """PATCH body — only keys present in the request are applied.

    Passing `null` explicitly clears the field. Supplying a key flips that
    field's traceability source to `"provider_edit"`.
    """

    pass


class ClinicalHistoryFieldMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: Literal["intake", "provider_edit"] | None = None
    source_form_id: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None


class ClinicalHistoryPublic(ClinicalHistoryBase):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    # Per-field traceability. Keys match any of the base fields above.
    field_meta: dict[str, ClinicalHistoryFieldMeta] = Field(default_factory=dict)
    seeded_from_form_id: str | None = None
    last_imported_at: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None


class ClinicalHistoryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    form_id: str | None = Field(
        default=None,
        description="Intake form to import from. Defaults to the most recent completed form.",
    )


class ClinicalHistoryImportResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    history: ClinicalHistoryPublic
    imported_fields: list[str] = Field(default_factory=list)
    skipped_fields: list[str] = Field(
        default_factory=list,
        description="Fields NOT imported because they already carry a provider edit.",
    )
    source_form_id: str | None = None


# ---------------------------------------------------------------------------
# Diagnoses / problem list
# ---------------------------------------------------------------------------
LATERALITY = Literal["left", "right", "bilateral", "midline"]
CHRONICITY = Literal["acute", "subacute", "chronic"]
DX_STATUS = Literal["active", "resolved"]


class DiagnosisCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icd10_code: str = Field(min_length=2, max_length=10)
    label: str = Field(min_length=1, max_length=300)
    episode_id: str | None = None
    is_primary: bool = False
    body_region: str | None = Field(default=None, max_length=80)
    laterality: LATERALITY | None = None
    chronicity: CHRONICITY | None = None
    onset_date: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class DiagnosisUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icd10_code: str | None = Field(default=None, min_length=2, max_length=10)
    label: str | None = Field(default=None, min_length=1, max_length=300)
    episode_id: str | None = None
    is_primary: bool | None = None
    body_region: str | None = Field(default=None, max_length=80)
    laterality: LATERALITY | None = None
    chronicity: CHRONICITY | None = None
    onset_date: str | None = None
    notes: str | None = Field(default=None, max_length=2000)


class DiagnosisResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resolved_date: str | None = None  # ISO; defaults to now
    resolution_notes: str | None = Field(default=None, max_length=1000)


class DiagnosisPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    patient_id: str
    episode_id: str | None = None
    icd10_code: str
    label: str
    status: DX_STATUS
    is_primary: bool
    body_region: str | None = None
    laterality: LATERALITY | None = None
    chronicity: CHRONICITY | None = None
    onset_date: str | None = None
    resolved_date: str | None = None
    resolution_notes: str | None = None
    notes: str | None = None
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None
