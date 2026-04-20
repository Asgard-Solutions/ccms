"""
Scheduling Service — Appointment domain model.

Future relational schema:
  appointments (
    id          UUID PRIMARY KEY,
    patient_id  UUID NOT NULL REFERENCES patients(id),
    provider_id UUID NOT NULL REFERENCES users(id),
    start_time  TIMESTAMPTZ NOT NULL,
    end_time    TIMESTAMPTZ NOT NULL,
    reason      VARCHAR(255),
    status      VARCHAR(20)  NOT NULL,    -- scheduled|completed|cancelled
    notes       TEXT,
    created_by  UUID NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL,
    CHECK (end_time > start_time)
  );
  CREATE INDEX ON appointments (provider_id, start_time);
"""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

AppointmentStatus = Literal["scheduled", "completed", "cancelled"]


class AppointmentCreate(BaseModel):
    patient_id: str
    provider_id: str
    start_time: datetime
    end_time: datetime
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    location_id: str | None = None


class AppointmentUpdate(BaseModel):
    start_time: datetime | None = None
    end_time: datetime | None = None
    reason: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    status: AppointmentStatus | None = None
    location_id: str | None = None


class AppointmentPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str | None = None
    location_id: str | None = None
    patient_id: str
    patient_name: str | None = None
    patient_phone: str | None = None
    provider_id: str
    provider_name: str | None = None
    start_time: str
    end_time: str
    reason: str | None = None
    notes: str | None = None
    status: AppointmentStatus
    created_by: str
    created_at: str
    updated_at: str
