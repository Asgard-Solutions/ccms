"""
Communication Service — Notification domain model.

Future relational schema:
  notifications (
    id              UUID PRIMARY KEY,
    appointment_id  UUID REFERENCES appointments(id),   -- nullable
    patient_id      UUID REFERENCES patients(id),       -- nullable
    channel         VARCHAR(20) NOT NULL,    -- email|sms
    to_address      VARCHAR(255) NOT NULL,
    subject         VARCHAR(255),
    body            TEXT NOT NULL,
    event_type      VARCHAR(40) NOT NULL,    -- appointment.booked|updated|cancelled
    status          VARCHAR(20) NOT NULL,    -- queued|sent|failed   (MVP logs as "sent_mock")
    created_at      TIMESTAMPTZ NOT NULL
  );
"""
from typing import Literal
from pydantic import BaseModel, ConfigDict

Channel = Literal["email", "sms"]


class NotificationPublic(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    appointment_id: str | None = None
    patient_id: str | None = None
    channel: Channel
    to_address: str
    subject: str | None = None
    body: str
    event_type: str
    status: str
    created_at: str
