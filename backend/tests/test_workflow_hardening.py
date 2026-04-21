"""Phase 7 hardening tests — edge cases not covered in earlier phases.

Covers:
  * Cancelled appointments remain historically visible but do NOT block
    rebooking the same provider/time slot.
  * Booking against an active (non-cancelled) appointment at the same
    slot is blocked (409) — catches the Phase-6 regression where only
    status='scheduled' blocked booking.
  * Undo Ready-for-Provider (back to checked_in, stamps cleared).
  * Undo Ready-for-Checkout (back to in_progress, stamps cleared).
  * Undo Check-In clears every forward stamp so the patient can
    re-walk the workflow without ghost timestamps.
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


def _ctx(s):
    patients = s.get(f"{API}/patients", timeout=10).json()
    providers = s.get(f"{API}/auth/providers", timeout=10).json()
    return patients[0]["id"], providers[0]["id"]


def _book(s, patient_id, provider_id, start: datetime, end: datetime, **kw):
    payload = {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "reason": kw.get("reason", "phase7 test"),
    }
    return s.post(f"{API}/appointments", json=payload, timeout=10)


# ---------------------------------------------------------------------------
# Cancelled appointments stay historically visible but don't block rebooking
# ---------------------------------------------------------------------------

def test_cancelled_slot_unblocks_future_booking():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    end = start + timedelta(minutes=15)

    # First booking wins the slot.
    r1 = _book(s, patient_id, provider_id, start, end)
    assert r1.status_code == 201, r1.text
    aid = r1.json()["id"]

    # Second booking at the same slot → 409 (active conflict).
    r2 = _book(s, patient_id, provider_id, start, end)
    assert r2.status_code == 409, r2.text

    # Cancel the first — then the same slot must rebook cleanly.
    r = s.post(f"{API}/appointments/{aid}/cancel", timeout=10)
    assert r.status_code == 200, r.text
    r3 = _book(s, patient_id, provider_id, start, end)
    assert r3.status_code == 201, r3.text

    # History of the cancelled appt is still retrievable.
    h = s.get(f"{API}/appointments/{aid}", timeout=10)
    assert h.status_code == 200
    assert h.json()["status"] in ("cancelled", "canceled")


def test_active_non_scheduled_status_still_blocks_rebooking():
    """Regression guard — prior to Phase-7 hardening the conflict check
    only filtered status='scheduled', so a checked_in appointment would
    silently allow double-booking. Must be 409."""
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    end = start + timedelta(minutes=15)
    r = _book(s, patient_id, provider_id, start, end)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    # Move to checked_in (status != 'scheduled').
    r = s.post(f"{API}/appointments/{aid}/check-in", json={}, timeout=10)
    assert r.status_code == 200
    # Overlapping booking should still be blocked.
    r2 = _book(s, patient_id, provider_id, start, end)
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# Reversal — undo ready_for_provider / undo ready_for_checkout
# ---------------------------------------------------------------------------

def test_undo_ready_for_provider_returns_to_checked_in_and_clears_stamps():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    r = _book(s, patient_id, provider_id, start, start + timedelta(minutes=15))
    aid = r.json()["id"]
    s.post(f"{API}/appointments/{aid}/check-in", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/ready-for-provider", json={}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["ready_for_provider_at"]
    r = s.post(f"{API}/appointments/{aid}/undo-ready-for-provider", json={
        "reason": "wrong patient"
    }, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "checked_in"
    assert body["ready_for_provider_at"] in (None, "")
    assert body["ready_for_provider_by_user_id"] in (None, "")


def test_undo_ready_for_checkout_returns_to_in_progress_and_clears_stamps():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    r = _book(s, patient_id, provider_id, start, start + timedelta(minutes=15))
    aid = r.json()["id"]
    for ep in ("check-in", "ready-for-provider", "start-visit", "ready-for-checkout"):
        rr = s.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
        assert rr.status_code == 200, f"{ep}: {rr.text}"
    r = s.post(f"{API}/appointments/{aid}/undo-ready-for-checkout", json={}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "in_progress"
    assert body["ready_for_checkout_at"] in (None, "")


def test_undo_check_in_clears_all_forward_stamps():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    r = _book(s, patient_id, provider_id, start, start + timedelta(minutes=15))
    aid = r.json()["id"]
    # Drive through to in_progress then reach back via override.
    for ep in ("check-in", "ready-for-provider", "start-visit"):
        s.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
    r = s.post(f"{API}/appointments/{aid}/undo-check-in", json={
        "override": True, "reason": "booked wrong patient",
    }, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "scheduled"
    assert body["checked_in_at"] in (None, "")
    assert body["ready_for_provider_at"] in (None, "")
    assert body["visit_started_at"] in (None, "")
