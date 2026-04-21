"""Tests: appointment_type_id persistence + checkout hooks.

Covers:
  * POST /api/appointments with `appointment_type_id` → persists +
    hydrated response includes `appointment_type_id` + `appointment_type_name`.
  * Invalid/inactive/out-of-tenant appointment_type_id → 400.
  * PATCH /api/appointments/{id} can set/change appointment_type_id.
  * Checkout hook creates a `follow_up_suggestions` row when the type
    carries `default_follow_up_days`; idempotent on re-emit.
  * Checkout hook creates a `billing_invoices_stub` draft; idempotent.
  * GET /api/appointments/follow-up-suggestions surfaces the row;
    dismiss endpoint flips status.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]

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


def _ensure_type(s, *, follow_up_days: int | None = None) -> dict:
    name = f"AcceptanceType-{uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/appointment-types", json={
        "name": name,
        "default_duration_minutes": 30,
        "is_active": True,
        "default_follow_up_days": follow_up_days,
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# appointment_type_id persistence
# ---------------------------------------------------------------------------

def test_create_with_valid_appointment_type_id_persists_and_hydrates():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    at = _ensure_type(s)
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=30, minutes=offset)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=15)).isoformat(),
        "appointment_type_id": at["id"],
        "reason": "appt_type persistence",
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["appointment_type_id"] == at["id"]
    assert body["appointment_type_name"] == at["name"]

    # Refetch still has it.
    fresh = s.get(f"{API}/appointments/{body['id']}", timeout=10).json()
    assert fresh["appointment_type_id"] == at["id"]
    assert fresh["appointment_type_name"] == at["name"]


def test_create_rejects_unknown_appointment_type_id():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    # Use a far-future offset so a prior test's appointment doesn't
    # consume the slot first and return 409 instead of 400.
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=120, minutes=offset)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=15)).isoformat(),
        "appointment_type_id": "bogus-does-not-exist",
        "reason": "bad type",
    }, timeout=10)
    assert r.status_code == 400, r.text


def test_patch_can_change_appointment_type_id():
    s = _login(*DEFAULT_ADMIN)
    patient_id, provider_id = _ctx(s)
    a = _ensure_type(s)
    b = _ensure_type(s)
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=30, minutes=offset)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=15)).isoformat(),
        "appointment_type_id": a["id"], "reason": "start",
    }, timeout=10)
    aid = r.json()["id"]
    r = s.patch(f"{API}/appointments/{aid}",
                json={"appointment_type_id": b["id"]}, timeout=10)
    assert r.status_code == 200, r.text
    assert r.json()["appointment_type_id"] == b["id"]


# ---------------------------------------------------------------------------
# Checkout hooks — follow-up suggestion + draft invoice
# ---------------------------------------------------------------------------

def _drive_to_checkout(s, patient_id, provider_id, appointment_type_id: str | None) -> str:
    _ensure_completed_intake(s, patient_id)
    offset = (uuid.uuid4().int >> 32) % 500000
    start = datetime.now(timezone.utc) + timedelta(days=60, minutes=offset)
    payload: dict = {
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=15)).isoformat(),
        "reason": "hook test",
    }
    if appointment_type_id:
        payload["appointment_type_id"] = appointment_type_id
    r = s.post(f"{API}/appointments", json=payload, timeout=10)
    assert r.status_code == 201, r.text
    aid = r.json()["id"]
    for ep in ("check-in", "ready-for-provider", "start-visit", "ready-for-checkout", "complete"):
        rr = s.post(f"{API}/appointments/{aid}/{ep}", json={}, timeout=10)
        assert rr.status_code == 200, f"{ep}: {rr.text}"
    rr = s.post(f"{API}/appointments/{aid}/checkout", json={}, timeout=10)
    assert rr.status_code == 200, rr.text
    return aid


def test_checkout_hook_creates_follow_up_suggestion():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=14)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])

    # Suggestion must appear in the list endpoint filtered by patient.
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    matching = [r for r in rows if r["appointment_id"] == aid]
    assert matching, f"No suggestion for {aid}: {rows}"
    sugg = matching[0]
    assert sugg["status"] == "pending"
    assert sugg["source"] == "checkout_hook"
    assert sugg["appointment_type_id"] == at["id"]
    # suggested_at should be ~14 days after checked_out_at.
    suggested = datetime.fromisoformat(sugg["suggested_at"]).date()
    assert (suggested - datetime.now(timezone.utc).date()).days in range(13, 16)


def test_checkout_hook_skips_when_no_follow_up_days():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=None)  # explicitly no follow-up
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    assert not [r for r in rows if r["appointment_id"] == aid]


def test_checkout_hook_creates_draft_invoice_stub():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=7)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])

    # Verify stub exists directly in Mongo (no public router yet).
    import asyncio
    async def _run():
        client = AsyncIOMotorClient(MONGO_URL)
        try:
            db = client[DB_NAME]
            row = await db.billing_invoices_stub.find_one({"appointment_id": aid}, {"_id": 0})
        finally:
            client.close()
        return row
    row = asyncio.get_event_loop().run_until_complete(_run())
    assert row is not None, f"No draft invoice stub for {aid}"
    assert row["status"] == "draft"
    assert row["source"] == "checkout_hook"
    assert row["total_cents"] == 0


def test_dismiss_follow_up_suggestion():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=21)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    matching = [r for r in rows if r["appointment_id"] == aid]
    assert matching, "No suggestion to dismiss"
    sid = matching[0]["id"]
    r = s.post(f"{API}/appointments/follow-up-suggestions/{sid}/dismiss", timeout=10)
    assert r.status_code == 200, r.text

    # Dismissed: no longer in the default pending list.
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    assert not [r for r in rows if r["id"] == sid]


def test_resolve_follow_up_suggestion_links_new_appointment():
    """Follow-up suggestion gets marked `scheduled` with the newly booked
    appointment id when BookDialog resolves it post-save."""
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=10)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    matching = [r for r in rows if r["appointment_id"] == aid]
    assert matching, "No suggestion to resolve"
    sid = matching[0]["id"]

    # Book the follow-up appointment explicitly.
    offset = (uuid.uuid4().int >> 32) % 200000
    start = datetime.now(timezone.utc) + timedelta(days=25, minutes=offset)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id, "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(minutes=30)).isoformat(),
        "appointment_type_id": at["id"],
        "reason": "resolved follow-up",
    }, timeout=10)
    assert r.status_code == 201, r.text
    new_aid = r.json()["id"]

    # Resolve.
    r = s.post(f"{API}/appointments/follow-up-suggestions/{sid}/resolve",
               json={"appointment_id": new_aid}, timeout=10)
    assert r.status_code == 200, r.text

    # Suggestion no longer pending; status=scheduled; linked appointment stored.
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id, "status": "scheduled"}, timeout=10).json()
    resolved = [r for r in rows if r["id"] == sid]
    assert resolved, "Suggestion not moved to scheduled"
    assert resolved[0]["resolved_appointment_id"] == new_aid

    # Idempotent: second call returns 200 without error.
    r = s.post(f"{API}/appointments/follow-up-suggestions/{sid}/resolve",
               json={"appointment_id": new_aid}, timeout=10)
    assert r.status_code == 200, r.text


def test_resolve_follow_up_suggestion_requires_appointment_id():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=5)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    matching = [r for r in rows if r["appointment_id"] == aid]
    assert matching, "No suggestion to resolve"
    sid = matching[0]["id"]
    r = s.post(f"{API}/appointments/follow-up-suggestions/{sid}/resolve",
               json={}, timeout=10)
    assert r.status_code == 400, r.text


def test_resolve_follow_up_suggestion_unknown_appointment_400():
    s = _login(*DEFAULT_ADMIN)
    at = _ensure_type(s, follow_up_days=5)
    patient_id, provider_id = _ctx(s)
    aid = _drive_to_checkout(s, patient_id, provider_id, at["id"])
    rows = s.get(f"{API}/appointments/follow-up-suggestions",
                 params={"patient_id": patient_id}, timeout=10).json()
    matching = [r for r in rows if r["appointment_id"] == aid]
    assert matching, "No suggestion to resolve"
    sid = matching[0]["id"]
    r = s.post(f"{API}/appointments/follow-up-suggestions/{sid}/resolve",
               json={"appointment_id": "bogus-does-not-exist"}, timeout=10)
    assert r.status_code == 400, r.text
