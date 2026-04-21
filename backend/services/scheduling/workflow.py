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

ENCRYPTED_FIELDS = ["notes"]


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
        default_location="checkout",
        audit_action="appointment.ready_for_checkout",
        reject_msg="Visit must be in progress to mark ready for checkout",
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
) -> dict:
    """Execute the transition and return the updated (decrypted) document."""
    db = get_db_write()

    update = _guard_and_build(
        current, spec,
        actor_id=actor["id"],
        override=override,
        location_override=location_override,
    )

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
