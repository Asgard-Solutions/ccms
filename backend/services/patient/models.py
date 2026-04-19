"""
Patient Service — Patient + MedicalRecord domain models.

Future relational schema:
  patients (
    id                 UUID PRIMARY KEY,
    user_id            UUID REFERENCES users(id),         -- nullable
    first_name         VARCHAR(100) NOT NULL,
    last_name          VARCHAR(100) NOT NULL,
    date_of_birth      DATE,
    gender             VARCHAR(20),
    phone              VARCHAR(32),
    email              VARCHAR(255),
    address            TEXT,
    emergency_contact  VARCHAR(255),
    notes              TEXT,
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL
  );

  medical_records (
    id            UUID PRIMARY KEY,
    patient_id    UUID NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    record_type   VARCHAR(40) NOT NULL,   -- assessment|treatment|note|diagnosis
    title         VARCHAR(200) NOT NULL,
    description   TEXT,
    diagnosis     TEXT,
    treatment     TEXT,
    recorded_by   UUID NOT NULL REFERENCES users(id),
    recorded_at   TIMESTAMPTZ NOT NULL
  );
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, EmailStr, Field, ConfigDict

Gender = Literal["male", "female", "non-binary", "other", "prefer-not-to-say"]
RecordType = Literal["assessment", "treatment", "note", "diagnosis"]


class PatientCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    date_of_birth: str | None = None  # ISO date string YYYY-MM-DD
    gender: Gender | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None


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


class PatientPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    user_id: str | None = None
    first_name: str
    last_name: str
    date_of_birth: str | None = None
    gender: Gender | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    emergency_contact: str | None = None
    notes: str | None = None
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
