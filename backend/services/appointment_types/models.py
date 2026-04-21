"""
Appointment Types service — tenant-scoped catalog of bookable visit types.

Used by the Book Appointment modal to offer default durations (and a
display-friendly "reason" value) so front-desk staff don't have to
re-derive how long a "follow-up" vs an "initial consult" takes.

Future relational schema:
    appointment_types (
      id                        UUID PRIMARY KEY,
      tenant_id                 UUID NOT NULL,
      name                      VARCHAR(120) NOT NULL,
      default_duration_minutes  INTEGER NOT NULL CHECK (
                                  default_duration_minutes BETWEEN 5 AND 480),
      description               TEXT,
      is_active                 BOOLEAN NOT NULL DEFAULT TRUE,
      sort_order                INTEGER NOT NULL DEFAULT 0,
      created_at                TIMESTAMPTZ NOT NULL,
      updated_at                TIMESTAMPTZ NOT NULL,
      created_by                UUID,
      updated_by                UUID,
      UNIQUE (tenant_id, name)
    );
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AppointmentTypeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    default_duration_minutes: int = Field(ge=5, le=480)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class AppointmentTypeCreate(AppointmentTypeBase):
    is_active: bool = True


class AppointmentTypeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    default_duration_minutes: int | None = Field(default=None, ge=5, le=480)
    description: str | None = Field(default=None, max_length=1000)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name cannot be blank")
        return v


class AppointmentTypePublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    name: str
    default_duration_minutes: int
    description: str | None = None
    sort_order: int = 0
    is_active: bool = True
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None
