"""
Privacy Service — data models.

Future relational schema:
  privacy_requests (
    id               UUID PRIMARY KEY,
    request_type     VARCHAR(32) NOT NULL, -- export|delete|correct|restrict|opt_out
    subject_user_id  UUID NOT NULL,        -- user whose data is requested
    subject_patient_id UUID,               -- optional: if request targets a patient record
    submitted_by_id  UUID NOT NULL,        -- who raised it (patient self or admin/staff)
    status           VARCHAR(24) NOT NULL, -- received|in_review|approved|fulfilled|rejected|withdrawn
    notes            TEXT,
    response_notes   TEXT,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    closed_at        TIMESTAMPTZ,
    fulfillment      JSONB DEFAULT '{}'    -- {exported_rows, deleted_entity_ids, ...}
  );

  consent_records (
    id              UUID PRIMARY KEY,
    user_id         UUID NOT NULL,
    policy_type     VARCHAR(32) NOT NULL, -- privacy_notice | terms_of_service | marketing
    policy_version  VARCHAR(32) NOT NULL,
    action          VARCHAR(16) NOT NULL, -- accepted | withdrawn
    accepted_at     TIMESTAMPTZ NOT NULL,
    ip              VARCHAR(64),
    user_agent      VARCHAR(400)
  );

  communication_preferences (
    user_id         UUID PRIMARY KEY,
    email_opt_in    BOOLEAN NOT NULL DEFAULT TRUE,  -- transactional always-on
    sms_opt_in      BOOLEAN NOT NULL DEFAULT FALSE,
    marketing_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL
  );
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

RequestType = Literal["export", "delete", "correct", "restrict", "opt_out"]
RequestStatus = Literal[
    "received", "in_review", "approved", "fulfilled", "rejected", "withdrawn"
]
PolicyType = Literal["privacy_notice", "terms_of_service", "marketing"]


class PrivacyRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_type: RequestType
    subject_user_id: str | None = None   # defaults to current user when patient self-submits
    subject_patient_id: str | None = None
    notes: str = Field(default="", max_length=2000)


class PrivacyRequestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: RequestStatus | None = None
    response_notes: str | None = Field(default=None, max_length=2000)


class ConsentAccept(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_type: PolicyType = "privacy_notice"
    policy_version: str = Field(min_length=1, max_length=32)
    action: Literal["accepted", "withdrawn"] = "accepted"


class CommPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email_opt_in: bool | None = None
    sms_opt_in: bool | None = None
    marketing_opt_in: bool | None = None


class LegalHoldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold: bool
    reason: str = Field(default="", max_length=500)
