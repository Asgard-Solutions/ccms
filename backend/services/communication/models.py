"""
Communication Service — Notification domain model.
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
    to_address: str | None = None
    subject: str | None = None
    body: str | None = None
    event_type: str
    status: str
    created_at: str
    unmasked: bool = False
