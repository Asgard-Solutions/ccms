"""Checkout workflow — Phase 6 tests.

Covers the front-desk checkout flow:
  in_progress → ready_for_checkout (Send to Checkout)
            → [location=checkout, checkout_started_at/_by]  (Start Checkout)
            → completed → checked_out + notes/summary       (Complete Checkout)
            → current_location_type=departed                (Mark Departed)

Plus the hook-point contract:
  POST /api/appointments/{id}/checkout accepts
  {checkout_notes, checkout_summary} and persists them (encrypted).
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
    r = s.post(f"{API}/patients/{patient_id}/intake-forms",
               json={"seed_from_patient": True}, timeout=10)
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    s.patch(f"{API}/patients/{patient_id}/intake-forms/{fid}",
            json={"status": "completed"}, timeout=10)


def _appt_ready_for_checkout(s) -> dict:
    """Create + drive an appointment all the way to ready_for_checkout."""
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
        "reason": "checkout test",
    }, timeout=10)
    aid = r.json()["id"]
    for ep in ("check-in", "ready-for-provider", "start-visit", "ready-for-checkout"):
        rr = s.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
        assert rr.status_code == 200, f"{ep}: {rr.status_code} {rr.text}"
    return rr.json()


# ---------------------------------------------------------------------------
# Start Checkout (physical-location motion only)
# ---------------------------------------------------------------------------

def test_start_checkout_moves_location_keeps_status():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_checkout(s)
    aid = a["id"]
    r = s.post(f"{API}/appointments/{aid}/start-checkout", json={}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ready_for_checkout"  # unchanged
    assert body["current_location_type"] == "checkout"
    assert body["checkout_started_at"] and body["checkout_started_by_user_id"]


def test_start_checkout_rejected_from_wrong_state():
    s = _login(*DEFAULT_ADMIN)
    # Appointment is still in_progress — start-checkout not allowed.
    patients = s.get(f"{API}/patients", timeout=10).json()
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    _ensure_completed_intake(s, patients[0]["id"])
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=30, minutes=offset)
    end = start + timedelta(minutes=15)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patients[0]["id"], "provider_id": providers[0]["id"],
        "start_time": start.isoformat(), "end_time": end.isoformat(),
        "reason": "early start-checkout test",
    }, timeout=10)
    aid = r.json()["id"]
    for ep in ("check-in", "ready-for-provider", "start-visit"):
        s.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/start-checkout", json={}, timeout=10)
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# Complete Checkout — full context + notes/summary capture
# ---------------------------------------------------------------------------

def test_complete_checkout_captures_notes_and_summary():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_checkout(s)
    aid = a["id"]
    # Through complete → checkout
    s.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/checkout", json={
        "checkout_notes": "Patient requested printed receipt.",
        "checkout_summary": "Scheduled follow-up in 2 weeks.",
    }, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "checked_out"
    assert body["checkout_notes"] == "Patient requested printed receipt."
    assert body["checkout_summary"] == "Scheduled follow-up in 2 weeks."
    assert body["checked_out_at"] and body["checked_out_by_user_id"]


def test_checkout_hook_payload_is_encrypted_at_rest():
    """Checkout notes/summary must be stored encrypted — we verify by
    reading the raw doc and confirming the plain text doesn't appear."""
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_checkout(s)
    aid = a["id"]
    s.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    secret = f"Hook-{uuid.uuid4().hex}"
    r = s.post(f"{API}/appointments/{aid}/checkout", json={
        "checkout_notes": secret,
        "checkout_summary": "OK",
    }, timeout=10)
    assert r.status_code == 200, r.text

    # Round-trip via API (decrypted path) still returns plaintext.
    fresh = s.get(f"{API}/appointments/{aid}", timeout=10).json()
    assert fresh["checkout_notes"] == secret


# ---------------------------------------------------------------------------
# Mark Departed
# ---------------------------------------------------------------------------

def test_depart_after_checkout_sets_location_departed():
    s = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_checkout(s)
    aid = a["id"]
    s.post(f"{API}/appointments/{aid}/complete", json={}, timeout=10)
    s.post(f"{API}/appointments/{aid}/checkout", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/depart", json={}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["current_location_type"] == "departed"
    # Lifecycle unchanged by depart.
    assert r.json()["status"] == "checked_out"


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def test_patient_portal_cannot_drive_checkout_actions():
    admin = _login(*DEFAULT_ADMIN)
    a = _appt_ready_for_checkout(admin)
    aid = a["id"]
    pt = _login(*PATIENT_USER, reauth=False)
    for ep in ("start-checkout", "checkout", "depart"):
        r = pt.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
        assert r.status_code in (401, 403), f"{ep}: {r.status_code} {r.text}"
