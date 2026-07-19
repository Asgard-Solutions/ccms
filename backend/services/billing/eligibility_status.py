"""
services/billing/eligibility_status.py — Eligibility state model.

Canonical set of 9 states per P0 spec:

  not_checked   — never verified for this context
  submitted     — request in flight (async live engines)
  active        — payer returned active coverage
  inactive      — payer returned inactive / terminated coverage
  partial       — active but some requested benefits missing
  rejected      — payer rejected request (subscriber mismatch, etc.)
  error         — engine-level transport / parsing failure
  unknown       — payer response neither active nor inactive
  expired       — result too old or context drifted (service date,
                  policy snapshot); callers should re-run.

Expiration rules (`is_expired(...)`)
------------------------------------
A stored eligibility check is considered expired when ANY of:
  * `checked_at` is more than `ELIGIBILITY_TTL_DAYS` (default 30) old
  * `service_date` on the record differs from the caller's target DOS
  * `policy_snapshot_hash` on the record differs from the current
    policy snapshot (member_id / effective_date / termination_date /
    group_number / payer_id changed since the check was run).

`classify_result(parsed)` → one of {active, inactive, partial,
rejected, unknown}. Error and expired are caller-assigned.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal


ELIGIBILITY_TTL_DAYS = 30

ELIGIBILITY_STATUSES: tuple[str, ...] = (
    "not_checked", "submitted", "active", "inactive", "partial",
    "rejected", "error", "unknown", "expired",
)
EligibilityStatus = Literal[
    "not_checked", "submitted", "active", "inactive", "partial",
    "rejected", "error", "unknown", "expired",
]

STATUS_LABELS: dict[str, str] = {
    "not_checked": "Not checked",
    "submitted":   "Submitted",
    "active":      "Active",
    "inactive":    "Inactive",
    "partial":     "Partial",
    "rejected":    "Rejected",
    "error":       "Error",
    "unknown":     "Unknown",
    "expired":     "Expired",
}

# Tones the UI uses to colour status badges.
STATUS_TONES: dict[str, str] = {
    "active":   "success",
    "partial":  "warning",
    "inactive": "destructive",
    "rejected": "destructive",
    "error":    "destructive",
    "unknown":  "muted",
    "expired":  "warning",
    "submitted": "muted",
    "not_checked": "muted",
}


BAD_MARKER = "BAD"   # mock trigger — subscriber mismatch → rejected
ERR_MARKER = "ERR"   # mock trigger — engine error → error
TERM_MARKER = "TERM"  # mock trigger — terminated coverage → inactive


DISCLAIMER_TEXT = (
    "Eligibility information is payer-reported and is not a guarantee "
    "of payment."
)


def policy_snapshot_hash(policy: dict[str, Any] | None) -> str:
    """Stable 12-char hash of the policy fields that invalidate a
    prior eligibility check when changed. Empty policy hashes to an
    empty string so the caller can distinguish "no policy" from a
    real snapshot."""
    if not policy:
        return ""
    keys = (
        "member_id", "group_number", "payer_id",
        "effective_date", "termination_date", "subscriber_name",
        "relationship_to_subscriber",
    )
    material = "|".join((str(policy.get(k)) or "") for k in keys)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def classify_result(parsed: dict[str, Any] | None) -> str:
    """Map a parsed 271 result into one of {active, inactive, partial,
    rejected, unknown}. Caller overlays `error` / `expired` / etc.
    """
    if not parsed:
        return "unknown"
    # Rejected when the 271 explicitly carries a `rejected` marker.
    if parsed.get("rejected"):
        return "rejected"
    if parsed.get("coverage_active"):
        # Partial when coverage is active but an expected benefit is
        # missing (e.g. caller asked for chiropractic, payer did not
        # return any chiropractic EB).
        requested = parsed.get("requested_service_types") or []
        returned = {
            b.get("service_type") for b in (parsed.get("benefits") or [])
            if b.get("qualifier") == "1"
        }
        if requested and not any(c in returned for c in requested):
            return "partial"
        return "active"
    benefits = parsed.get("benefits") or []
    if not benefits:
        return "unknown"
    # Any EB*6 / EB*I indicates inactive / non-covered coverage.
    for b in benefits:
        if b.get("qualifier") in ("6", "I"):
            return "inactive"
    # EB*V — cannot process / refer to payer
    for b in benefits:
        if b.get("qualifier") == "V":
            return "rejected"
    return "unknown"


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        v = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


def is_expired(
    row: dict[str, Any],
    *,
    target_service_date: str | None = None,
    target_policy_snapshot: str | None = None,
    now: datetime | None = None,
    ttl_days: int = ELIGIBILITY_TTL_DAYS,
) -> bool:
    """True when the stored check is stale for the caller's context."""
    now = now or datetime.now(timezone.utc)
    checked = _parse_iso(row.get("checked_at"))
    if checked is None:
        return True
    if (now - checked).days >= ttl_days:
        return True
    if target_service_date and row.get("service_date") \
            and row["service_date"] != target_service_date:
        return True
    if target_policy_snapshot and row.get("policy_snapshot_hash") \
            and row["policy_snapshot_hash"] != target_policy_snapshot:
        return True
    return False


def overlay_expiration(
    row: dict[str, Any],
    *,
    target_service_date: str | None = None,
    target_policy_snapshot: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a copy of `row` with `effective_status` set. When the
    stored status is already terminal (rejected/error/inactive) we
    still downgrade it to `expired` when the record is stale — the
    caller's UI should prompt a re-check regardless of the original
    outcome."""
    out = dict(row)
    current = row.get("status") or classify_result(row.get("result"))
    if is_expired(
        row,
        target_service_date=target_service_date,
        target_policy_snapshot=target_policy_snapshot,
        now=now,
    ):
        out["effective_status"] = "expired"
    else:
        out["effective_status"] = current
    return out
