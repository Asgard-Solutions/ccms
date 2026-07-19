"""Tests for the appointment workflow backbone — Phase 1.

Covers:
  * Lifecycle status + physical location are independent
  * Validated forward transitions (check-in → complete → checkout)
  * Validation rules:
      - cannot check in a canceled appointment
      - cannot start visit before check-in unless override=True
      - cannot complete before visit starts
      - cannot check out before provider phase complete unless override=True
      - cannot mark no-show after the visit has started
  * Reversion: undo-check-in works and is audited
  * Depart endpoint sets the physical location
  * Patient-portal role cannot drive workflow transitions (permission denied)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest  # noqa: F401
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
PATIENT_USER = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        if r.status_code == 200:
            rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
            if rt:
                s.headers["x-reauth-token"] = rt
    return s


def _create_future_appt(s: requests.Session, *, complete_intake: bool = True) -> dict:
    """Create a fresh appointment we can drive through the workflow.

    By default we also ensure the patient has at least one *completed*
    intake form so the ready_for_provider gate passes. Pass
    `complete_intake=False` to exercise the gate itself.
    """
    patients = s.get(f"{API}/patients", timeout=10).json()
    assert patients, "Need at least one patient seeded"
    patient_id = patients[0]["id"]
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    assert providers, "Need at least one provider seeded"
    provider_id = providers[0]["id"]

    # Stagger start times per test to avoid provider-double-booking conflicts.
    # Use a wide pseudo-random minute offset so concurrent tests don't collide.
    offset_minutes = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset_minutes)
    end = start + timedelta(minutes=15)
    r = s.post(
        f"{API}/appointments",
        json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "reason": "workflow test",
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    appt = r.json()
    if complete_intake:
        _ensure_completed_intake(s, patient_id)
    return appt


def _ensure_completed_intake(s: requests.Session, patient_id: str) -> None:
    """Idempotently create + complete an intake form for the patient."""
    # Already have a completed form? Do nothing.
    existing = s.get(
        f"{API}/patients/{patient_id}/intake-forms", timeout=10
    ).json()
    if any(f.get("status") == "completed" for f in existing):
        return
    create = s.post(
        f"{API}/patients/{patient_id}/intake-forms",
        json={"seed_from_patient": True},
        timeout=10,
    )
    assert create.status_code == 201, create.text
    form_id = create.json()["id"]
    patch = s.patch(
        f"{API}/patients/{patient_id}/intake-forms/{form_id}",
        json={"status": "completed"},
        timeout=10,
    )
    assert patch.status_code == 200, patch.text


def _post(s: requests.Session, path: str, payload: dict | None = None):
    r = s.post(f"{API}{path}", json=payload or {}, timeout=10)
    return r


# ---------------------------------------------------------------------------
# Happy path: the full workflow
# ---------------------------------------------------------------------------

def test_workflow_full_happy_path():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]

    # check in
    r = _post(s, f"/appointments/{aid}/check-in")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "checked_in"
    assert body["current_location_type"] == "waiting_room"
    assert body["checked_in_at"] and body["checked_in_by_user_id"]

    # ready for provider
    r = _post(s, f"/appointments/{aid}/ready-for-provider")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready_for_provider"
    assert body["current_location_type"] == "roomed"
    assert body["ready_for_provider_at"]

    # start visit
    r = _post(s, f"/appointments/{aid}/start-visit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["visit_started_at"]

    # ready for checkout
    r = _post(s, f"/appointments/{aid}/ready-for-checkout")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready_for_checkout"
    # Phase-6 UX note: ready-for-checkout no longer auto-moves location to
    # 'checkout'; the front desk drives that via /start-checkout.
    assert body["ready_for_checkout_at"]

    # complete
    r = _post(s, f"/appointments/{aid}/complete")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["completed_at"]

    # checkout
    r = _post(s, f"/appointments/{aid}/checkout")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "checked_out"
    assert body["checked_out_at"]
    assert body["current_location_type"] == "departed"


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

def test_cannot_check_in_canceled():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    r = s.post(f"{API}/appointments/{aid}/cancel", timeout=10)
    assert r.status_code == 200, r.text
    r = _post(s, f"/appointments/{aid}/check-in")
    assert r.status_code == 400, r.text


def test_cannot_start_visit_before_check_in():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    r = _post(s, f"/appointments/{aid}/start-visit")
    assert r.status_code == 400, r.text


def test_start_visit_from_checked_in_requires_override():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")

    # Without override: blocked (must go through ready_for_provider first).
    r = _post(s, f"/appointments/{aid}/start-visit")
    assert r.status_code == 400, r.text

    # With override: allowed, explicitly audited.
    r = _post(s, f"/appointments/{aid}/start-visit",
              {"override": True, "reason": "skipping ready step"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"


def test_cannot_complete_before_visit_starts():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    r = _post(s, f"/appointments/{aid}/complete")
    assert r.status_code == 400, r.text


def test_cannot_checkout_before_complete_without_override():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    _post(s, f"/appointments/{aid}/ready-for-provider")
    _post(s, f"/appointments/{aid}/start-visit")
    _post(s, f"/appointments/{aid}/ready-for-checkout")

    # ready_for_checkout → checked_out without override is blocked.
    r = _post(s, f"/appointments/{aid}/checkout")
    assert r.status_code == 400, r.text

    # With override, allowed.
    r = _post(s, f"/appointments/{aid}/checkout", {"override": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "checked_out"


def test_cannot_no_show_after_visit_started():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    _post(s, f"/appointments/{aid}/ready-for-provider")
    _post(s, f"/appointments/{aid}/start-visit")

    r = _post(s, f"/appointments/{aid}/no-show")
    assert r.status_code == 400, r.text


def test_no_show_from_scheduled_succeeds():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    r = _post(s, f"/appointments/{aid}/no-show")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_show"
    assert body["no_show_at"] and body["no_show_by_user_id"]


# ---------------------------------------------------------------------------
# Reversions
# ---------------------------------------------------------------------------

def test_undo_check_in_returns_to_scheduled():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    r = _post(s, f"/appointments/{aid}/undo-check-in",
              {"reason": "checked in wrong patient"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "scheduled"
    assert body["current_location_type"] == "not_arrived"


def test_undo_check_in_after_visit_started_requires_override():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    _post(s, f"/appointments/{aid}/ready-for-provider")
    _post(s, f"/appointments/{aid}/start-visit")

    r = _post(s, f"/appointments/{aid}/undo-check-in")
    assert r.status_code == 400, r.text
    r = _post(s, f"/appointments/{aid}/undo-check-in", {"override": True})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "scheduled"


# ---------------------------------------------------------------------------
# Depart + explicit location change
# ---------------------------------------------------------------------------

def test_depart_marks_location_departed():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    # Run full workflow, then depart
    _post(s, f"/appointments/{aid}/check-in")
    _post(s, f"/appointments/{aid}/ready-for-provider")
    _post(s, f"/appointments/{aid}/start-visit")
    _post(s, f"/appointments/{aid}/complete")
    _post(s, f"/appointments/{aid}/checkout")

    r = _post(s, f"/appointments/{aid}/depart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_location_type"] == "departed"
    # Lifecycle status unchanged by `depart`
    assert body["status"] == "checked_out"


def test_depart_before_checkout_requires_override():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    r = _post(s, f"/appointments/{aid}/depart")
    assert r.status_code == 400, r.text
    r = _post(s, f"/appointments/{aid}/depart", {"override": True})
    assert r.status_code == 200, r.text
    assert r.json()["current_location_type"] == "departed"


def test_set_location_does_not_touch_status():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s)
    aid = appt["id"]
    r = s.post(f"{API}/appointments/{aid}/location",
               json={"location": "waiting_room"}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_location_type"] == "waiting_room"
    assert body["status"] == "scheduled"
    assert body["location_updated_at"] and body["location_updated_by_user_id"]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_patient_portal_cannot_drive_workflow():
    admin = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(admin)
    aid = appt["id"]

    patient = _login(*PATIENT_USER, reauth=False)
    r = patient.post(f"{API}/appointments/{aid}/check-in", json={}, timeout=10)
    assert r.status_code in (401, 403), r.text


# ---------------------------------------------------------------------------
# Intake gating + intake_status hydration
# ---------------------------------------------------------------------------

def test_ready_for_provider_blocked_when_intake_incomplete():
    """Without a completed intake form, ready_for_provider must fail."""
    s = _login(*DEFAULT_ADMIN)

    # Create an isolated patient so we control the intake state.
    patient_payload = {
        "first_name": "Intake", "last_name": f"Gate{uuid.uuid4().hex[:6]}",
        "date_of_birth": "1990-01-01",
        "phone": "+15555550101",
    }
    pr = s.post(f"{API}/patients", json=patient_payload, timeout=10)
    assert pr.status_code in (200, 201), pr.text
    pid = pr.json()["id"]

    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    provider_id = providers[0]["id"]
    offset_minutes = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset_minutes)
    end = start + timedelta(minutes=15)
    ar = s.post(
        f"{API}/appointments",
        json={
            "patient_id": pid, "provider_id": provider_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "reason": "intake-gate",
        },
        timeout=10,
    )
    assert ar.status_code == 201, ar.text
    aid = ar.json()["id"]

    # Check in succeeds (no intake required yet).
    r = _post(s, f"/appointments/{aid}/check-in")
    assert r.status_code == 200, r.text

    # ready_for_provider blocked because intake isn't completed.
    r = _post(s, f"/appointments/{aid}/ready-for-provider")
    assert r.status_code == 400, r.text
    assert "intake" in r.json()["detail"].lower()

    # Override bypasses the gate (audited with intake_gate_bypassed=True).
    r = _post(s, f"/appointments/{aid}/ready-for-provider",
              {"override": True, "reason": "provider saw walk-in"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready_for_provider"


def test_ready_for_provider_passes_once_intake_completed():
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s, complete_intake=True)
    aid = appt["id"]
    _post(s, f"/appointments/{aid}/check-in")
    r = _post(s, f"/appointments/{aid}/ready-for-provider")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ready_for_provider"


def test_intake_status_surfaces_on_appointment():
    """Appointment response must include intake_status + completion meta."""
    s = _login(*DEFAULT_ADMIN)
    appt = _create_future_appt(s, complete_intake=True)
    aid = appt["id"]
    r = s.get(f"{API}/appointments/{aid}", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["intake_status"] == "completed"
    assert body["intake_completed_at"]
    assert body["intake_form_id"]
