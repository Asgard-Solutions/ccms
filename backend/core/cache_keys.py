"""
Cache key catalogue.

Centralising key shapes makes invalidation straightforward and prevents subtle
bugs where two places format the same key slightly differently.

TTL strategy summary (see PERFORMANCE_ARCHITECTURE.md):
  - identity-static (provider list, user count): 5 minutes
  - schedule data (calendar, availability): 30 seconds
  - patient list/detail (masked): 30 seconds
  - notifications list (masked only): 15 seconds
  - dashboard aggregates: 60 seconds

Anything containing UNMASKED PHI is intentionally not cacheable.
"""
from typing import Any

PROVIDERS = "identity:providers:active"


def patients_list(role: str, search: str | None, include_deleted: bool, masked: bool) -> str:
    return (
        f"patients:list:role={role}:search={(search or '').lower()}:"
        f"deleted={int(bool(include_deleted))}:masked={int(masked)}"
    )


def patient_detail(patient_id: str, masked: bool) -> str:
    return f"patient:detail:{patient_id}:masked={int(masked)}"


def appointments_query(role: str, params: dict[str, Any]) -> str:
    norm = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)
    return f"appts:list:role={role}:{norm}"


def calendar_week(provider_id: str | None, from_iso: str, to_iso: str) -> str:
    return f"appts:calendar:p={provider_id or 'all'}:{from_iso}:{to_iso}"


def dashboard_aggregates(user_id: str) -> str:
    return f"dashboard:aggregates:user={user_id}"


def notifications_list(event_type: str | None, patient_id: str | None, limit: int) -> str:
    """Masked-only notifications list. We never cache the unmask=true branch."""
    return (
        f"notifications:list:event={event_type or 'all'}:"
        f"patient={patient_id or 'all'}:limit={limit}:masked=1"
    )


# Prefixes for invalidation
PREFIX_PATIENTS = "patients:"
PREFIX_PATIENT = "patient:"
PREFIX_APPOINTMENTS = "appts:"
PREFIX_DASHBOARD = "dashboard:"
PREFIX_PROVIDERS = "identity:providers"
PREFIX_NOTIFICATIONS = "notifications:"
