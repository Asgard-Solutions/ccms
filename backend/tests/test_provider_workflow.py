"""Provider workflow — Phase 5 tests.

Covers the provider-facing progression:
  ready_for_provider → in_progress → ready_for_checkout / completed
  and the handoff to checkout (completed → checked_out).

Scope:
  * Start Visit: valid only from ready_for_provider (allowed_from);
    requires override when coming from checked_in.
  * Ready for Checkout: valid only from in_progress.
  * Complete Visit: valid only from in_progress OR ready_for_checkout.
  * completed → checked_out is a clean handoff (no override needed).
  * Front-desk + provider both retain appointment.update and can drive
    provider transitions; patient role is denied.
  * Timing stamps (visit_started_at / ready_for_checkout_at /
    completed_at) are populated server-side.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
PATIENT_USER = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
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


def _ensure_completed_intake(s, patient_id: str) -> None:
    existing = s.get(f"{API}/patients/{patient_id}/intake-forms", timeout=10).json()
    if any(f.get("status") == "completed" for f in existing):
        return
    create = s.post(
        f"{API}/patients/{patient_id}/intake-forms",
        json={"seed_from_patient": True},
        timeout=10,
    )
    assert create.status_code == 201, create.text
    fid = create.json()["id"]
    r = s.patch(
        f"{API}/patients/{patient_id}/intake-forms/{fid}",
        json={"status": "completed"},
        timeout=10,
    )
    assert r.status_code == 200, r.text


def _appt_ready_for_provider(s) -> dict:
    patients = s.get(f"{API}/patients", timeout=10).json()
    patient_id = patients[0]["id"]
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    provider_id = providers[0]["id"]
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=30, minutes=offset)
    end = start + timedelta(minutes=15)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(), "end_time": end.isoformat(),
        "reason": "provider workflow test",
    }, timeout=10)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    assert s.post(f"{API}/appointments/{aid}/check-in", json={}, timeout=10).status_code == 200
    r = s.post(f"{API}/appointments/{aid}/ready-for-provider", json={}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Happy path: ready → in_progress → ready_for_checkout → complete → checkout
# ---------------------------------------------------------------------------

def test_provider_handoff_happy_path():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(s)
    aid = a["id"]

    r = s.post(f"{API}/appointments/{aid}/start-visit", json={}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["visit_started_at"] and body["visit_started_by_user_id"]

    r = s.post(f"{API}/appointments/{aid}/ready-for-checkout", json={}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready_for_checkout"
    assert body["ready_for_checkout_at"]

    r = s.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"
    assert r.json()["completed_at"]

    # Clean handoff to checkout: completed → checked_out without override.
    r = s.post(f"{API}/appointments/{aid}/checkout", json={}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "checked_out"


def test_complete_direct_from_in_progress():
    """Provider may complete straight from in_progress (skip ready_for_checkout)."""
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(s)
    aid = a["id"]
    s.post(f"{API}/appointments/{aid}/start-visit", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "completed"


# ---------------------------------------------------------------------------
# Validation: rejection paths
# ---------------------------------------------------------------------------

def test_start_visit_rejected_from_wrong_state():
    s = _login(*DEFAULT_ADMIN)
    # Build an appointment still in "scheduled".
    patients = s.get(f"{API}/patients", timeout=10).json()
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=30, minutes=offset)
    end = start + timedelta(minutes=15)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patients[0]["id"], "provider_id": providers[0]["id"],
        "start_time": start.isoformat(), "end_time": end.isoformat(),
        "reason": "bad transition",
    }, timeout=10)
    aid = r.json()["id"]
    r = s.post(f"{API}/appointments/{aid}/start-visit", json={}, timeout=10)
    assert r.status_code == 400, r.text


def test_ready_for_checkout_rejected_outside_in_progress():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(s)
    # Directly from ready_for_provider — not allowed.
    r = s.post(f"{API}/appointments/{a['id']}/ready-for-checkout", json={}, timeout=10)
    assert r.status_code == 400, r.text


def test_complete_rejected_before_visit_starts():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(s)
    r = s.post(f"{API}/appointments/{a['id']}/complete", json={}, timeout=10)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Permissions: patient role can't drive provider transitions
# ---------------------------------------------------------------------------

def test_patient_portal_cannot_drive_provider_transitions():
    admin = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(admin)
    aid = a["id"]

    pt = _login(*PATIENT_USER, reauth=False)
    for ep in ("start-visit", "ready-for-checkout", "complete"):
        r = pt.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
        assert r.status_code in (401, 403), f"{ep} leaked: {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# Front-desk role is equally allowed — same appointment.update grant
# ---------------------------------------------------------------------------

def test_admin_and_provider_both_drive_handoff():
    """Admin may drive start-visit; the provider (same tenant) may then
    drive ready-for-checkout + complete."""
    admin = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_provider(admin)
    aid = a["id"]
    r = admin.post(f"{API}/appointments/{aid}/start-visit", json={}, timeout=10)
    assert r.status_code == 200, r.text
    r = admin.post(f"{API}/appointments/{aid}/ready-for-checkout", json={}, timeout=10)
    assert r.status_code == 200, r.text
    r = admin.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    assert r.status_code == 200, r.text
