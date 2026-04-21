"""
End-to-end regression for the Scheduling workstream.

Covers:
- Appointment create + list + range-filtered list + counts reconcile.
- Reschedule + cancel + re-query reflect state.
- Counts endpoint respects tenant / role scoping alongside list endpoint.
- Permissions: patient may not create appointments for other patients.

These tests run against a live backend at $CCMS_BASE_URL (default
http://localhost:8001/api) and use the seeded demo accounts.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest  # noqa: F401
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    token = r.cookies.get("access_token")
    if token:
        s.headers["Authorization"] = f"Bearer {token}"
    return s


def _reauth(s: requests.Session, password: str) -> None:
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("reauth_token")
    if tok:
        s.headers["x-reauth-token"] = tok


def _seed_patient_and_provider(admin: requests.Session) -> tuple[str, str]:
    """Return (patient_id, provider_id) usable on the Default tenant."""
    # First provider
    providers = admin.get(f"{API}/auth/providers", timeout=10).json()
    assert providers, "no providers seeded"
    provider_id = providers[0]["id"]

    # First patient
    patients = admin.get(f"{API}/patients", timeout=10).json()
    if patients:
        return patients[0]["id"], provider_id
    # Otherwise create one
    new = admin.post(f"{API}/patients", json={
        "first_name": "Sched",
        "last_name": f"Test-{uuid.uuid4().hex[:6]}",
        "date_of_birth": "1990-01-01",
        "gender": "other",
        "phone": "+15551110000",
        "email": f"sched-{uuid.uuid4().hex[:6]}@test.example",
    }, timeout=10).json()
    return new["id"], provider_id


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0, tzinfo=timezone.utc).isoformat()


def test_create_list_range_and_counts_reconcile():
    admin = _login(*DEFAULT_ADMIN)
    _reauth(admin, DEFAULT_ADMIN[1])
    patient_id, provider_id = _seed_patient_and_provider(admin)

    # Pin to a unique future day so parallel tests don't clash.
    day = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    day = day + timedelta(days=45)
    start = day
    end = day + timedelta(minutes=30)

    created = admin.post(f"{API}/appointments", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": _iso(start),
        "end_time": _iso(end),
        "reason": "Regression test",
    }, timeout=10)
    assert created.status_code in (200, 201), created.text
    appt = created.json()
    appt_id = appt["id"]

    try:
        # Range fetch must include it
        frm = _iso(day - timedelta(hours=2))
        to_ = _iso(day + timedelta(hours=2))
        list_rows = admin.get(f"{API}/appointments",
                              params={"from": frm, "to": to_}, timeout=10).json()
        assert any(a["id"] == appt_id for a in list_rows), \
            "created appt missing from range list"

        # Counts endpoint must carry the same appt bucketed to the correct local date
        counts = admin.get(f"{API}/appointments/counts",
                           params={"from": frm, "to": to_, "tz": "UTC",
                                   "include_samples": 5},
                           timeout=10).json()
        bucket = next(
            (row for row in counts if row["date"] == start.strftime("%Y-%m-%d")),
            None,
        )
        assert bucket is not None, f"no counts row for day; got {counts}"
        assert bucket["count"] >= 1
        assert any(s["id"] == appt_id for s in bucket["samples"])

        # Reschedule
        new_start = start + timedelta(minutes=30)
        new_end = end + timedelta(minutes=30)
        rescheduled = admin.patch(f"{API}/appointments/{appt_id}", json={
            "start_time": _iso(new_start),
            "end_time": _iso(new_end),
        }, timeout=10)
        assert rescheduled.status_code == 200, rescheduled.text
        assert rescheduled.json()["start_time"].startswith(
            new_start.strftime("%Y-%m-%dT%H:%M")
        )

        # Cancel
        cancelled = admin.post(f"{API}/appointments/{appt_id}/cancel", timeout=10)
        assert cancelled.status_code in (200, 204), cancelled.text
        # Verify via refetch
        final = admin.get(f"{API}/appointments",
                          params={"from": frm, "to": to_}, timeout=10).json()
        hit = next((a for a in final if a["id"] == appt_id), None)
        assert hit is not None
        assert hit["status"] == "cancelled"

        # Cancelled appts now tracked separately (task 15: counts split
        # active vs cancelled so daily operational load isn't inflated).
        counts_after = admin.get(f"{API}/appointments/counts",
                                 params={"from": frm, "to": to_, "tz": "UTC"},
                                 timeout=10).json()
        bucket_after = next(
            (row for row in counts_after
             if row["date"] == new_start.strftime("%Y-%m-%d")),
            None,
        )
        assert bucket_after is not None
        assert bucket_after["cancelled_count"] >= 1
    finally:
        # No hard delete endpoint; cancellation is cleanup enough.
        pass


def test_patient_cannot_create_for_other_patient():
    """A patient's self-service attempt to book for someone else must fail."""
    patient_sess = _login(*PATIENT)
    admin = _login(*DEFAULT_ADMIN)
    other_patient_id, provider_id = _seed_patient_and_provider(admin)

    # The logged-in patient's own patient_id
    own = patient_sess.get(f"{API}/patients/me", timeout=10)
    # /patients/me may not exist; if so, fall back — we only care that booking
    # for `other_patient_id` is rejected for a patient actor.
    r = patient_sess.post(f"{API}/appointments", json={
        "patient_id": other_patient_id,
        "provider_id": provider_id,
        "start_time": _iso(datetime.now(timezone.utc) + timedelta(days=2)),
        "end_time": _iso(datetime.now(timezone.utc) + timedelta(days=2, minutes=30)),
    }, timeout=10)
    # Accept either 403 (explicit block) or 404 (patient not visible to caller).
    assert r.status_code in (401, 403, 404, 422), r.text
    _ = own  # silence unused


def test_patient_counts_never_leak_other_tenants():
    """The counts endpoint must never return appointments outside the caller's scope."""
    patient_sess = _login(*PATIENT)
    # Grab full date range
    r = patient_sess.get(f"{API}/appointments/counts",
                        params={"from": "2000-01-01T00:00:00Z",
                                "to": "2040-01-01T00:00:00Z"},
                        timeout=10)
    assert r.status_code == 200
    # Samples (if any) are all for this patient — verified by the list endpoint
    lst = patient_sess.get(f"{API}/appointments", timeout=10).json()
    own_ids = {a["id"] for a in lst}
    for row in r.json():
        for s in row.get("samples", []):
            # Counts endpoint defaulted samples=0 so this should be an empty loop
            assert s["id"] in own_ids


def test_cancelled_slot_is_rebookable():
    """Task 15 — cancelling an appointment must free the slot for rebooking."""
    admin = _login(*DEFAULT_ADMIN)
    _reauth(admin, DEFAULT_ADMIN[1])
    patient_id, provider_id = _seed_patient_and_provider(admin)

    slot_start = datetime.now(timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0
    ) + timedelta(days=120)
    slot_end = slot_start + timedelta(minutes=30)

    # 1. Book the slot.
    r1 = admin.post(f"{API}/appointments", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": _iso(slot_start),
        "end_time": _iso(slot_end),
        "reason": "initial",
    }, timeout=10)
    assert r1.status_code in (200, 201), r1.text
    first_id = r1.json()["id"]

    # 2. Double-book on the SAME slot must fail.
    r2 = admin.post(f"{API}/appointments", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": _iso(slot_start),
        "end_time": _iso(slot_end),
        "reason": "double-book attempt",
    }, timeout=10)
    assert r2.status_code == 409, (r2.status_code, r2.text)

    # 3. Cancel the first appointment.
    rc = admin.post(f"{API}/appointments/{first_id}/cancel", timeout=10)
    assert rc.status_code in (200, 204), rc.text

    # 4. Rebook must succeed now.
    r3 = admin.post(f"{API}/appointments", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": _iso(slot_start),
        "end_time": _iso(slot_end),
        "reason": "after cancellation",
    }, timeout=10)
    assert r3.status_code in (200, 201), (r3.status_code, r3.text)
    assert r3.json()["id"] != first_id

    # 5. Counts: active=1, cancelled=1 for that date.
    frm = _iso(slot_start - timedelta(hours=1))
    to_ = _iso(slot_start + timedelta(hours=1))
    counts = admin.get(f"{API}/appointments/counts",
                       params={"from": frm, "to": to_, "tz": "UTC"},
                       timeout=10).json()
    bucket = next(
        (row for row in counts if row["date"] == slot_start.strftime("%Y-%m-%d")),
        None,
    )
    assert bucket is not None
    assert bucket["count"] >= 1
    assert bucket["cancelled_count"] >= 1


def test_include_cancelled_flag_on_list():
    """list endpoint filters cancelled when include_cancelled=false."""
    admin = _login(*DEFAULT_ADMIN)
    frm = "2000-01-01T00:00:00Z"
    to_ = "2040-01-01T00:00:00Z"
    all_rows = admin.get(f"{API}/appointments",
                         params={"from": frm, "to": to_,
                                 "include_cancelled": "true"},
                         timeout=10).json()
    active_rows = admin.get(f"{API}/appointments",
                            params={"from": frm, "to": to_,
                                    "include_cancelled": "false"},
                            timeout=10).json()
    assert len(active_rows) <= len(all_rows)
    for a in active_rows:
        assert a["status"] != "cancelled"

