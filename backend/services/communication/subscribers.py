"""
Communication Service event subscribers.

These react to appointment lifecycle events published on the in-process bus
and create mock Notification rows the UI can display. No real provider is
called in Phase 1 — this validates the event-driven pipeline end-to-end.
"""
import logging
import uuid
from datetime import datetime, timezone

from core.db import get_db
from core.event_bus import subscribe

logger = logging.getLogger("communication.subscribers")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%a %b %d, %Y at %I:%M %p UTC")
    except Exception:
        return ts


async def _resolve_patient_contact(patient_id: str) -> tuple[str, str, str]:
    """Returns (full_name, email, phone). Uses empty strings if missing."""
    db = get_db()
    p = await db.patients.find_one(
        {"id": patient_id},
        {"_id": 0, "first_name": 1, "last_name": 1, "email": 1, "phone": 1},
    )
    if not p:
        return ("Unknown patient", "", "")
    name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    return name, p.get("email") or "", p.get("phone") or ""


async def _resolve_provider_name(provider_id: str) -> str:
    db = get_db()
    u = await db.users.find_one({"id": provider_id}, {"_id": 0, "name": 1})
    return u["name"] if u else "your provider"


async def _insert(notifs: list[dict]) -> None:
    notifs = [n for n in notifs if n.get("to_address")]
    if not notifs:
        return
    await get_db().notifications.insert_many(notifs)


async def _make_pair(
    *,
    appointment: dict,
    event_type: str,
    subject: str,
    body_template: str,
) -> list[dict]:
    patient_name, email, phone = await _resolve_patient_contact(appointment["patient_id"])
    provider_name = await _resolve_provider_name(appointment["provider_id"])
    body = body_template.format(
        patient_name=patient_name or "Patient",
        provider_name=provider_name,
        when=_fmt(appointment["start_time"]),
        reason=appointment.get("reason") or "Chiropractic consultation",
    )
    now = _now()
    base = {
        "appointment_id": appointment["id"],
        "patient_id": appointment["patient_id"],
        "event_type": event_type,
        "subject": subject,
        "body": body,
        "status": "sent_mock",
        "created_at": now,
    }
    return [
        {**base, "id": str(uuid.uuid4()), "channel": "email", "to_address": email},
        {**base, "id": str(uuid.uuid4()), "channel": "sms", "to_address": phone},
    ]


async def on_appointment_booked(payload: dict) -> None:
    appt = payload["appointment"]
    notifs = await _make_pair(
        appointment=appt,
        event_type="appointment.booked",
        subject="Your appointment is confirmed",
        body_template=(
            "Hi {patient_name}, your appointment with {provider_name} is "
            "confirmed for {when}. Reason: {reason}. Reply STOP to opt out."
        ),
    )
    await _insert(notifs)
    logger.info("Queued %d mock notifications for appointment.booked", len(notifs))


async def on_appointment_updated(payload: dict) -> None:
    appt = payload["appointment"]
    notifs = await _make_pair(
        appointment=appt,
        event_type="appointment.updated",
        subject="Your appointment was updated",
        body_template=(
            "Hi {patient_name}, your appointment with {provider_name} has been "
            "updated. New time: {when}. Reason: {reason}."
        ),
    )
    await _insert(notifs)


async def on_appointment_cancelled(payload: dict) -> None:
    appt = payload["appointment"]
    notifs = await _make_pair(
        appointment=appt,
        event_type="appointment.cancelled",
        subject="Your appointment was cancelled",
        body_template=(
            "Hi {patient_name}, your appointment with {provider_name} scheduled "
            "for {when} has been cancelled. We will contact you to reschedule."
        ),
    )
    await _insert(notifs)


def register() -> None:
    subscribe("appointment.booked", on_appointment_booked)
    subscribe("appointment.updated", on_appointment_updated)
    subscribe("appointment.cancelled", on_appointment_cancelled)
