"""
Room / exam-space domain model.

Future relational schema (PostgreSQL-ready):
  rooms (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    location_id    UUID NOT NULL,
    name           VARCHAR(80) NOT NULL,
    type           VARCHAR(16) NOT NULL,  -- exam|consult|xray|therapy|other
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order     INT     NOT NULL DEFAULT 0,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL,
    UNIQUE (tenant_id, location_id, LOWER(name))
  );
  CREATE INDEX ON rooms (tenant_id, location_id, is_active, sort_order);

  appointment_room_history (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL,
    location_id     UUID,
    appointment_id  UUID NOT NULL REFERENCES appointments(id),
    patient_id      UUID NOT NULL,
    from_room_id    UUID,
    to_room_id      UUID,
    from_location_type VARCHAR(24),
    to_location_type   VARCHAR(24),
    actor_id        UUID NOT NULL,
    reason          TEXT,
    forced          BOOLEAN NOT NULL DEFAULT FALSE,
    at              TIMESTAMPTZ NOT NULL
  );
  CREATE INDEX ON appointment_room_history (appointment_id, at);
  CREATE INDEX ON appointment_room_history (to_room_id, at);

Single-occupancy is the v1 assumption for every room type — conflict
validation is applied across all types uniformly and can be overridden with
`force=True` (each override is explicitly audited).
"""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

RoomType = Literal["exam", "consult", "xray", "therapy", "other"]

MAX_NAME_LEN = 80
MAX_NOTES_LEN = 500


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    location_id: str
    name: str = Field(min_length=1, max_length=MAX_NAME_LEN)
    type: RoomType = "exam"
    sort_order: int = 0
    is_active: bool = True
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LEN)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Room name cannot be blank")
        return v


class RoomUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str | None = Field(default=None, min_length=1, max_length=MAX_NAME_LEN)
    type: RoomType | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    notes: str | None = Field(default=None, max_length=MAX_NOTES_LEN)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Room name cannot be blank")
        return v


class RoomPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str | None = None
    location_id: str
    name: str
    type: RoomType
    is_active: bool
    sort_order: int
    notes: str | None = None
    created_at: str
    updated_at: str


class RoomAssignRequest(BaseModel):
    """Payload for POST /api/appointments/{id}/room."""
    model_config = ConfigDict(extra="ignore")
    room_id: str
    reason: str | None = Field(default=None, max_length=255)
    # Allow moving into an already-occupied single-occupancy room with an
    # explicit, audited override.
    force: bool = False
