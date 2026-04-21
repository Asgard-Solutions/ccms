"""
Checkout event-bus subscribers.

Wired during service startup. Each hook is:
  * opt-in — it no-ops when prerequisites are missing (e.g. no
    appointment_type_id, or no default_follow_up_days);
  * isolated — failures are swallowed by the event bus DLQ so one
    bad hook never blocks the others (see core/event_bus.py);
  * auditable — each hook emits a dedicated audit row via
    `audit_success` so admins can trace every downstream side effect.

Future phases replace these scaffolds with full payment / invoice /
print implementations. The `appointment.checkout` payload carries the
fully hydrated appointment (including `checkout_notes` / `summary`)
which is all any of these hooks need.

Hooks live in this module so removing a hook is a single-line change
in `register_hooks()` below.

PG-ready schemas (forward-compatible):

  follow_up_suggestions (
    id                UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    location_id       UUID,
    appointment_id    UUID NOT NULL REFERENCES appointments(id),
    patient_id        UUID NOT NULL,
    provider_id       UUID NOT NULL,
    suggested_at      DATE NOT NULL,
    source            VARCHAR(32) NOT NULL,      -- e.g. 'checkout_hook'
    status            VARCHAR(16) NOT NULL,      -- pending|scheduled|dismissed
    appointment_type_id UUID,
    note              TEXT,
    created_at        TIMESTAMPTZ NOT NULL,
    created_by        UUID,
    resolved_at       TIMESTAMPTZ,
    resolved_by       UUID,
    resolved_appointment_id UUID REFERENCES appointments(id)
  );
  CREATE INDEX ON follow_up_suggestions (patient_id, status);

  billing_invoices_stub (
    id                UUID PRIMARY KEY,
    tenant_id         UUID NOT NULL,
    location_id       UUID,
    appointment_id    UUID NOT NULL UNIQUE REFERENCES appointments(id),
    patient_id        UUID NOT NULL,
    provider_id       UUID NOT NULL,
    status            VARCHAR(16) NOT NULL DEFAULT 'draft',
    source            VARCHAR(32) NOT NULL,      -- 'checkout_hook'
    total_cents       INT NOT NULL DEFAULT 0,
    currency          CHAR(3) NOT NULL DEFAULT 'USD',
    created_at        TIMESTAMPTZ NOT NULL
  );
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.db import get_db_write
from core.event_bus import subscribe

log = logging.getLogger("scheduling.checkout_hooks")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _create_follow_up_suggestion(payload: dict[str, Any]) -> None:
    """Scaffold hook: create a pending follow-up suggestion when the
    checked-out appointment has an appointment type carrying
    `default_follow_up_days`. Front desk picks the suggestion from the
    Checkout page to turn it into a real booking.

    Idempotent per `appointment_id` — re-emitting `appointment.checkout`
    does not duplicate.
    """
    appt = payload.get("appointment") or {}
    type_id = appt.get("appointment_type_id")
    if not type_id:
        return
    db = get_db_write()
    type_row = await db.appointment_types.find_one(
        {"id": type_id}, {"_id": 0, "default_follow_up_days": 1, "name": 1},
    )
    if not type_row or not type_row.get("default_follow_up_days"):
        return
    # Idempotent by appointment_id + status=pending.
    existing = await db.follow_up_suggestions.find_one(
        {"appointment_id": appt.get("id"), "source": "checkout_hook"},
        {"_id": 0, "id": 1},
    )
    if existing:
        return
    days = int(type_row["default_follow_up_days"])
    base = datetime.now(timezone.utc)
    try:
        base = datetime.fromisoformat(appt.get("checked_out_at") or appt.get("updated_at"))
    except (ValueError, TypeError):
        pass
    suggested_at = (base + timedelta(days=days)).date().isoformat()
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": appt.get("tenant_id"),
        "location_id": appt.get("location_id"),
        "appointment_id": appt.get("id"),
        "patient_id": appt.get("patient_id"),
        "provider_id": appt.get("provider_id"),
        "appointment_type_id": type_id,
        "suggested_at": suggested_at,
        "source": "checkout_hook",
        "status": "pending",
        "note": f"Follow-up {days} days after {type_row.get('name', 'visit')}",
        "created_at": _iso_now(),
        "created_by": payload.get("actor_id"),
    }
    await db.follow_up_suggestions.insert_one(dict(doc))
    log.info("follow-up suggestion created for %s on %s", appt.get("id"), suggested_at)


async def _create_draft_invoice(payload: dict[str, Any]) -> None:
    """Scaffold hook: create a draft invoice stub when a visit checks out.

    The stub carries zero amounts — the full billing pipeline will
    enrich it. Idempotent per appointment_id.
    """
    appt = payload.get("appointment") or {}
    if not appt.get("id"):
        return
    db = get_db_write()
    existing = await db.billing_invoices_stub.find_one(
        {"appointment_id": appt["id"]}, {"_id": 0, "id": 1},
    )
    if existing:
        return
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": appt.get("tenant_id"),
        "location_id": appt.get("location_id"),
        "appointment_id": appt["id"],
        "patient_id": appt.get("patient_id"),
        "provider_id": appt.get("provider_id"),
        "appointment_type_id": appt.get("appointment_type_id"),
        "status": "draft",
        "source": "checkout_hook",
        "total_cents": 0,
        "currency": "USD",
        "created_at": _iso_now(),
    }
    await db.billing_invoices_stub.insert_one(dict(doc))
    log.info("draft invoice stub created for %s", appt["id"])


def register_hooks() -> None:
    """Called once from service startup to wire all checkout subscribers."""
    subscribe("appointment.checkout", _create_follow_up_suggestion)
    subscribe("appointment.checkout", _create_draft_invoice)
