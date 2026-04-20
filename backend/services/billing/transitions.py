"""
Billing status-transition helpers.

Every call-site that moves a billing entity from one `status` to another
MUST go through `advance()` so the allowed-transition table is the single
source of truth. A direct update that skips this helper is a bug.
"""
from __future__ import annotations

from fastapi import HTTPException, status as http_status

from services.billing.models import (
    CLAIM_TRANSITIONS,
    DENIAL_TRANSITIONS,
    INVOICE_TRANSITIONS,
    PAYMENT_TRANSITIONS,
    REMITTANCE_TRANSITIONS,
    TERMINAL_STATUSES,
)

_MAPS: dict[str, dict[str, set[str]]] = {
    "invoice": INVOICE_TRANSITIONS,
    "payment": PAYMENT_TRANSITIONS,
    "claim": CLAIM_TRANSITIONS,
    "remittance": REMITTANCE_TRANSITIONS,
    "denial": DENIAL_TRANSITIONS,
}


class TransitionError(ValueError):
    """Raised when a caller asks for an illegal status transition."""


def allowed_next(entity: str, current: str) -> set[str]:
    if entity not in _MAPS:
        raise TransitionError(f"unknown entity type: {entity}")
    return set(_MAPS[entity].get(current, set()))


def is_terminal(entity: str, current: str) -> bool:
    return current in TERMINAL_STATUSES.get(entity, set())


def advance(entity: str, current: str, desired: str) -> str:
    """Return `desired` if the transition is legal, else raise.

    `entity` is one of: invoice | payment | claim | remittance | denial.
    Idempotent same-state "transitions" are allowed (no-op) so retries do
    not explode at the API layer.
    """
    if current == desired:
        return desired
    allowed = allowed_next(entity, current)
    if desired not in allowed:
        raise TransitionError(
            f"illegal {entity} transition {current!r} → {desired!r}; "
            f"allowed: {sorted(allowed) or '[terminal]'}"
        )
    return desired


def http_advance(entity: str, current: str, desired: str) -> str:
    """Same as `advance()` but raises `HTTPException(409)` on failure."""
    try:
        return advance(entity, current, desired)
    except TransitionError as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, str(exc)) from exc
