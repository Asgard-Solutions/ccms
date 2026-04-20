"""
Patient Service — Patient + MedicalRecord domain models (HIPAA-hardened).

Added to the Phase 1 schema:
  patients + status VARCHAR(20) NOT NULL DEFAULT 'active'  -- active|deleted
          + deleted_at    TIMESTAMPTZ
          + deleted_by    UUID REFERENCES users(id)
          + retention_until TIMESTAMPTZ      -- 7-year retention window

Sensitive free-text columns are stored encrypted at rest via core.crypto.
"""
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict

Gender = Literal["male", "female", "non-binary", "other", "prefer-not-to-say"]
RecordType = Literal["assessment", "treatment", "note", "diagnosis"]
PatientStatus = Literal["active", "deleted"]


class PatientCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None
    location_id: str | None = None


class PatientUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None
    location_id: str | None = None


class PatientPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str | None = None
    tenant_id: str | None = None
    location_id: str | None = None
    first_name: str
    last_name: str
    display_name_masked: str | None = None
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None
    status: PatientStatus = "active"
    deleted_at: str | None = None
    retention_until: str | None = None
    unmasked: bool = False
    created_at: str
    updated_at: str


class MedicalRecordCreate(BaseModel):
    record_type: RecordType
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    diagnosis: str | None = None
    treatment: str | None = None


class MedicalRecordPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    patient_id: str
    record_type: RecordType
    title: str
    description: str | None = None
    diagnosis: str | None = None
    treatment: str | None = None
    recorded_by: str
    recorded_by_name: str | None = None
    recorded_at: str
