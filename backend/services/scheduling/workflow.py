"""
Appointment workflow — transition engine.

A single source of truth for every lifecycle + physical-location transition
an appointment can undergo. All transitions are:
  - explicitly validated server-side (default-deny)
  - atomic (one $set per DB write)
  - audited with who/when/action/previous_status/override_used
  - paired with a sensible `current_location_type` default when the transition
    implies a physical movement (callers may override per-request)

The router exposes thin endpoints that:
  1. resolve the target appointment under tenant scope
  2. call `apply_transition(...)` here
  3. hydrate + return the public view

Lifecycle status and the patient's physical location are independent concepts.
`status` governs what the clinic can next do with the visit; `location`
tracks where the patient physically is. Front desk can move a patient to
the waiting room while status is still `scheduled`, and the provider can
pull a patient into `roomed` without advancing status.

Validation rules (mirrored from the product spec):
  - cannot check in a canceled / completed / checked_out appointment
  - cannot start visit before check-in unless `override=True`
  - cannot complete before visit has started
  - cannot check out before the provider phase is complete unless `override=True`
  - cannot mark no-show after the visit has started
  - every reversion (undo_check_in) is explicit and audited
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from fastapi import HTTPException, Request, status

from core.audit import audit_success
from core.db import get_db_write, read_after_write_db
from core.crypto import decrypt_fields, encrypt_fields
from core import cache, cache_keys
from core.event_bus import publish

# Canonical cancel spelling for *new* transitions. Legacy rows carrying
# "cancelled" remain valid and are treated equivalently in guards.
CANCELED = "canceled"
CANCELLED_LEGACY = "cancelled"

TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"checked_out", "no_show", CANCELED, CANCELLED_LEGACY}
)
POST_VISIT_START: frozenset[str] = frozenset(
    {"in_progress", "ready_for_checkout", "completed", "checked_out"}
)

ENCRYPTED_FIELDS = ["notes", "checkout_notes", "checkout_summary"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_canceled(s: str | None) -> bool:
    return s in (CANCELED, CANCELLED_LEGACY)


@dataclass(frozen=True)
class TransitionSpec:
    """Defines how one named workflow action moves the appointment."""
    name: str                               # e.g. "check_in"
    target_status: str | None               # None = do not change status
    allowed_from: frozenset[str]            # statuses we may transition from
    overridable_from: frozenset[str] = frozenset()  # allowed only with override=True
    # metadata fields to stamp: field_prefix -> (at_field, by_field)
    stamp_at: str | None = None
    stamp_by: str | None = None
    # default physical location if none explicitly supplied by caller
    default_location: str | None = None
    audit_action: str = ""
    # Human-readable message for rejections
    reject_msg: str = "Invalid appointment transition"
    # When True, requires the appointment NOT to already be canceled/no_show.
    forbid_terminal: bool = True


TRANSITIONS: dict[str, TransitionSpec] = {
    "check_in": TransitionSpec(
        name="check_in",
        target_status="checked_in",
        allowed_from=frozenset({"scheduled", "confirmed"}),
        stamp_at="checked_in_at",
        stamp_by="checked_in_by_user_id",
        default_location="waiting_room",
        audit_action="appointment.checked_in",
        reject_msg="Cannot check in from current status",
    ),
    "undo_check_in": TransitionSpec(
        name="undo_check_in",
        target_status="scheduled",
        allowed_from=frozenset({"checked_in", "ready_for_provider"}),
        overridable_from=frozenset({"in_progress"}),  # only with override=True
        default_location="not_arrived",
        audit_action="appointment.undo_check_in",
        reject_msg="Cannot undo check-in after the visit has started",
    ),
    "no_show": TransitionSpec(
        name="no_show",
        target_status="no_show",
        allowed_from=frozenset(
            {"scheduled", "confirmed", "checked_in", "ready_for_provider"}
        ),
        stamp_at="no_show_at",
        stamp_by="no_show_by_user_id",
        audit_action="appointment.no_show",
        reject_msg="Cannot mark no-show after the visit has started",
    ),
    "ready_for_provider": TransitionSpec(
        name="ready_for_provider",
        target_status="ready_for_provider",
        allowed_from=frozenset({"checked_in"}),
        stamp_at="ready_for_provider_at",
        stamp_by="ready_for_provider_by_user_id",
        default_location="roomed",
        audit_action="appointment.ready_for_provider",
        reject_msg="Patient must be checked in before marking ready for provider",
    ),
    "start_visit": TransitionSpec(
        name="start_visit",
        target_status="in_progress",
        allowed_from=frozenset({"ready_for_provider"}),
        # checked_in -> in_progress skips the "ready" step; only allowed with override
        overridable_from=frozenset({"checked_in"}),
        stamp_at="visit_started_at",
        stamp_by="visit_started_by_user_id",
        default_location="roomed",
        audit_action="appointment.visit_started",
        reject_msg="Cannot start visit before check-in",
    ),
    "ready_for_checkout": TransitionSpec(
        name="ready_for_checkout",
        target_status="ready_for_checkout",
        allowed_from=frozenset({"in_progress"}),
        stamp_at="ready_for_checkout_at",
        stamp_by="ready_for_checkout_by_user_id",
        # Intentionally do NOT default-set location=checkout — keeping the
        # patient's current physical location lets the front desk decide
        # when the patient actually moves to the counter (via
        # /start-checkout). Without this, the UI's "Ready for Checkout"
        # staging section would always be empty.
        default_location=None,
        audit_action="appointment.ready_for_checkout",
        reject_msg="Visit must be in progress to mark ready for checkout",
    ),
    "start_checkout": TransitionSpec(
        name="start_checkout",
        # Status stays ready_for_checkout — this is a physical motion only.
        target_status=None,
        allowed_from=frozenset({"ready_for_checkout", "completed"}),
        stamp_at="checkout_started_at",
        stamp_by="checkout_started_by_user_id",
        default_location="checkout",
        audit_action="appointment.checkout_started",
        reject_msg="Appointment must be ready for checkout or completed",
    ),
    "complete": TransitionSpec(
        name="complete",
        target_status="completed",
        allowed_from=frozenset({"in_progress", "ready_for_checkout"}),
        stamp_at="completed_at",
        stamp_by="completed_by_user_id",
        audit_action="appointment.completed",
        reject_msg="Cannot complete before the visit has started",
    ),
    "checkout": TransitionSpec(
        name="checkout",
        target_status="checked_out",
        allowed_from=frozenset({"completed"}),
        # allow ready_for_checkout -> checked_out only with override
        overridable_from=frozenset({"ready_for_checkout"}),
        stamp_at="checked_out_at",
        stamp_by="checked_out_by_user_id",
        default_location="departed",
        audit_action="appointment.checked_out",
        reject_msg="Visit must be completed before checkout",
    ),
    "depart": TransitionSpec(
        name="depart",
        # `depart` only moves physical location — lifecycle stays put.
        target_status=None,
        # Depart is typically after checkout, but a no-show / canceled
        # patient may also be marked as departed. Any other status needs
        # override.
        allowed_from=frozenset({"checked_out", "no_show", CANCELED, CANCELLED_LEGACY}),
        overridable_from=frozenset(
            {"scheduled", "confirmed", "checked_in",
             "ready_for_provider", "in_progress", "ready_for_checkout",
             "completed"}
        ),
        default_location="departed",
        audit_action="appointment.departed",
        reject_msg="Patient has not completed the visit; use override to force depart",
        forbid_terminal=False,  # this IS the terminal motion for no_show/canceled
    ),
}


def _guard_and_build(
    current: dict,
    spec: TransitionSpec,
    *,
    actor_id: str,
    override: bool,
    location_override: str | None,
) -> dict:
    """Validate the transition + return the `$set` update payload."""
    cur_status = current.get("status", "scheduled")

    # Block transitions on canceled appointments entirely (except depart).
    if _is_canceled(cur_status) and spec.name != "depart":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot transition a canceled appointment",
        )

    # Guard against already-terminal statuses if the spec disallows them.
    if (
        spec.forbid_terminal
        and cur_status in TERMINAL_STATUSES
        and cur_status != spec.target_status
        and spec.name != "depart"
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Appointment is already {cur_status}",
        )

    # Block no_show once the visit has already started.
    if spec.name == "no_show" and cur_status in POST_VISIT_START:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, spec.reject_msg
        )

    # Membership check — allowed_from or (override + overridable_from).
    if cur_status in spec.allowed_from:
        pass  # OK
    elif override and cur_status in spec.overridable_from:
        pass  # OK (override path)
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, spec.reject_msg)

    now = _now_iso()
    update: dict = {"updated_at": now}

    if spec.target_status is not None:
        update["status"] = spec.target_status

    if spec.stamp_at:
        update[spec.stamp_at] = now
    if spec.stamp_by:
        update[spec.stamp_by] = actor_id

    # Physical location handling — caller may override, else use the spec
    # default (if any). Location updates are always stamped when written.
    final_location = location_override or spec.default_location
    if final_location is not None:
        update["current_location_type"] = final_location
        update["location_updated_at"] = now
        update["location_updated_by_user_id"] = actor_id

    return update


async def _intake_is_complete(patient_id: str, tenant_id: str | None) -> bool:
    """Return True if the patient has at least one completed intake form.

    Tenant-scoped when tenant_id is known. Doesn't touch PHI fields — only
    reads status flag from `patient_intake_forms`.
    """
    from core.db import get_db_read
    db = get_db_read()
    q: dict = {"patient_id": patient_id, "status": "completed"}
    if tenant_id:
        q["tenant_id"] = tenant_id
    row = await db.patient_intake_forms.find_one(q, {"_id": 0, "id": 1})
    return bool(row)


async def _guard_intake_gating(
    current: dict,
    spec: TransitionSpec,
    *,
    override: bool,
    tenant_id: str | None,
) -> None:
    """Block `ready_for_provider` unless the patient has a completed intake
    form. Explicit `override=True` bypasses the check (audited separately)."""
    if spec.name != "ready_for_provider":
        return
    if override:
        return
    patient_id = current.get("patient_id")
    if not patient_id:
        return  # defensive — if we can't identify a patient, skip the gate
    if not await _intake_is_complete(patient_id, tenant_id):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Patient intake is not complete. Complete intake or pass "
            "override=true to bypass.",
        )


async def apply_transition(
    appointment_id: str,
    spec: TransitionSpec,
    *,
    current: dict,
    actor: dict,
    request: Request,
    override: bool,
    reason: str | None,
    location_override: str | None,
    tenant_id: str | None,
    extra_fields: dict | None = None,
) -> dict:
    """Execute the transition and return the updated (decrypted) document.

    `extra_fields` lets specific endpoints capture additional persisted
    data as part of the same atomic write — e.g. checkout notes on the
    `checkout` transition. Keys are restricted to the appointments
    schema; values are trusted (already validated by the Pydantic model
    in the router layer) and encrypted in-flight via ENCRYPTED_FIELDS.
    """
    db = get_db_write()

    # Intake gating runs BEFORE the state-machine check so the operator gets
    # the most specific error first (otherwise they'd see a generic state
    # rejection when the real blocker is missing intake).
    await _guard_intake_gating(
        current, spec, override=override, tenant_id=tenant_id,
    )

    update = _guard_and_build(
        current, spec,
        actor_id=actor["id"],
        override=override,
        location_override=location_override,
    )
    if extra_fields:
        # Only accept a narrow, known set of keys to avoid accidental
        # over-writes from caller-controlled payloads.
        allowed = {"checkout_notes", "checkout_summary"}
        for k, v in extra_fields.items():
            if k in allowed and v is not None:
                update[k] = v

    await db.appointments.update_one(
        {"id": appointment_id},
        {"$set": encrypt_fields(dict(update), ENCRYPTED_FIELDS)},
    )
    updated = await read_after_write_db().appointments.find_one(
        {"id": appointment_id}, {"_id": 0}
    )
    updated_dec = decrypt_fields(updated, ENCRYPTED_FIELDS)

    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)
    await cache.invalidate_prefix(cache_keys.PREFIX_DASHBOARD)

    # Event bus — downstream consumers (billing, notifications, analytics).
    await publish(
        f"appointment.{spec.name}",
        {
            "appointment": updated_dec,
            "previous_status": current.get("status"),
            "actor_id": actor["id"],
            "override": override,
        },
    )

    # Audit — no PHI, only operational metadata + reason.
    intake_override = (
        spec.name == "ready_for_provider" and override
        and not await _intake_is_complete(current.get("patient_id"), tenant_id)
    )
    await audit_success(
        actor, spec.audit_action, request,
        entity_type="appointment", entity_id=appointment_id,
        phi_accessed=False,
        metadata={
            "from_status": current.get("status"),
            "to_status": update.get("status", current.get("status")),
            "override": override,
            "reason": reason,
            "location_after": update.get("current_location_type"),
            "tenant_id": tenant_id,
            "intake_gate_bypassed": intake_override,
        },
    )
    return updated_dec


async def apply_patient_location(
    appointment_id: str,
    *,
    current: dict,
    new_location: str,
    actor: dict,
    request: Request,
    reason: str | None,
    tenant_id: str | None,
) -> dict:
    """Change only the patient's physical location (no lifecycle change)."""
    if _is_canceled(current.get("status")):
        # Canceled appts may still be marked `departed` but nothing else.
        if new_location != "departed":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Canceled appointments can only be marked departed",
            )

    now = _now_iso()
    update = {
        "current_location_type": new_location,
        "location_updated_at": now,
        "location_updated_by_user_id": actor["id"],
        "updated_at": now,
    }
    db = get_db_write()
    await db.appointments.update_one(
        {"id": appointment_id}, {"$set": update}
    )
    updated = await read_after_write_db().appointments.find_one(
        {"id": appointment_id}, {"_id": 0}
    )
    updated_dec = decrypt_fields(updated, ENCRYPTED_FIELDS)
    await cache.invalidate_prefix(cache_keys.PREFIX_APPOINTMENTS)

    await publish(
        "appointment.location_changed",
        {
            "appointment": updated_dec,
            "previous_location": current.get("current_location_type"),
            "actor_id": actor["id"],
        },
    )
    await audit_success(
        actor, "appointment.location_changed", request,
        entity_type="appointment", entity_id=appointment_id,
        phi_accessed=False,
        metadata={
            "from_location": current.get("current_location_type"),
            "to_location": new_location,
            "reason": reason,
            "tenant_id": tenant_id,
        },
    )
    return updated_dec
