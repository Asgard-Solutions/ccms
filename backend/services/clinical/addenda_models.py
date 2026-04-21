"""Clinical addenda — Phase 8.

Addenda are append-only clinical annotations attached to a signed parent
artifact (follow-up note, initial exam, or re-exam). Once an addendum is
signed, its own content becomes immutable. Addenda never mutate the parent
artifact; they sit alongside it and are rendered together in the chart.

Storage shape (MongoDB `clinical_addenda`, PostgreSQL-ready):

    clinical_addenda (
      id                 UUID PRIMARY KEY,
      tenant_id          UUID NOT NULL,
      location_id        UUID,
      patient_id         UUID NOT NULL,
      parent_type        VARCHAR(32) NOT NULL,    -- follow_up_note|initial_exam|re_exam
      parent_id          UUID NOT NULL,
      parent_signed_by   UUID,                    -- original signer at time of creation
      encounter_id       UUID,
      episode_id         UUID,
      reason             VARCHAR(160) NOT NULL,
      narrative          TEXT NOT NULL,
      status             VARCHAR(16) NOT NULL,    -- draft|signed
      signed_at          TIMESTAMPTZ,
      signed_by          UUID,
      created_at         TIMESTAMPTZ NOT NULL,
      updated_at         TIMESTAMPTZ NOT NULL,
      created_by         UUID NOT NULL,
      updated_by         UUID,
      history_log        JSONB NOT NULL DEFAULT '[]'
    );

    CREATE INDEX ON clinical_addenda (tenant_id, parent_type, parent_id, created_at DESC);
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ADDENDUM_PARENT_TYPE = Literal["follow_up_note", "initial_exam", "re_exam"]
ADDENDUM_STATUS = Literal["draft", "signed"]


class ClinicalAddendumCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str = Field(min_length=10, max_length=8000)
    reason: str = Field(min_length=3, max_length=160)


class ClinicalAddendumUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    narrative: str | None = Field(default=None, min_length=10, max_length=8000)
    reason: str | None = Field(default=None, min_length=3, max_length=160)


class ClinicalAddendumPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    tenant_id: str
    location_id: str | None = None
    patient_id: str
    parent_type: ADDENDUM_PARENT_TYPE
    parent_id: str
    encounter_id: str | None = None
    episode_id: str | None = None
    reason: str
    narrative: str
    status: ADDENDUM_STATUS
    signed_at: str | None = None
    signed_by: str | None = None
    signed_by_name: str | None = None
    author_id: str
    author_name: str | None = None
    created_at: str
    updated_at: str
