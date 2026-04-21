"""Pydantic models for multi-version patient intake forms."""
from pydantic import BaseModel, ConfigDict, Field

from services.patient.models import CaseDetails, ClinicalIntake


class IntakeFormCreate(BaseModel):
    """Body for POST /patients/{id}/intake-forms.

    All fields optional — callers may either seed from the patient's current
    intake (default) or pass an explicit payload. `seed_from_patient` copies
    the patient's existing `clinical_intake` / `case_details` blobs into the
    new form as a starting point.
    """
    model_config = ConfigDict(extra="ignore")
    seed_from_patient: bool = True
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    notes: str | None = None


class IntakeFormPatch(BaseModel):
    """Body for PATCH /patients/{id}/intake-forms/{form_id}.

    Only the fields explicitly present in the request body are applied.
    Passing `null` clears the field. Passing nothing leaves it untouched.
    """
    model_config = ConfigDict(extra="ignore")
    status: str | None = Field(default=None, pattern="^(draft|completed)$")
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    notes: str | None = None


class IntakeFormPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    patient_id: str
    tenant_id: str | None = None
    location_id: str | None = None
    status: str  # draft | completed
    version: int
    captured_by: str | None = None
    captured_by_name: str | None = None
    captured_at: str | None = None
    created_at: str
    updated_at: str
    clinical_intake: ClinicalIntake | None = None
    case_details: CaseDetails | None = None
    notes: str | None = None
