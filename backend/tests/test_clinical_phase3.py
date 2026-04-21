"""Clinical module Phase 3 — appointment-launched encounter tests.

Coverage (matching the Phase 3 deliverable asks):
  * appointment linkage: launch from appointment creates a clinical_encounter
    anchored to the appointment + patient + provider + episode
  * context auto-fill: scheduled_start / scheduled_end / scheduled_duration /
    appointment_snapshot / appointment_status_at_launch are populated from
    the appointment at launch time and remain FROZEN after the appointment
    is later edited
  * chart visibility: `GET /patients/{pid}/clinical/encounters` surfaces
    the newly launched encounter immediately + summary counts update
  * canceled/no-show validation: launching against a cancelled appointment
    without `exception_reason` returns 409; with a reason AND an admin/doctor
    role it succeeds with `is_exception=True`; staff cannot bend the rule
  * encounter state integrity: idempotent launch returns existing encounter
    with `existed=true`; completed → status=completed; cancel path; PATCH
    blocked on non-in_progress states; reauth required on all writes
  * tenant isolation: cross-tenant probes on encounter reads/writes → 404
"""
from __future__ import annotations

import os
import random
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
PATIENT_USER = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token")
    s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r.status_code == 200, r.text
        rt = r.cookies.get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*GROUP_ADMIN)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


def _new_patient(s) -> dict:
    r = s.post(f"{API}/patients", json={
        "first_name": "Enc",
        "last_name": f"P{uuid.uuid4().hex[:6]}",
        "email": f"enc_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0200",
        "date_of_birth": "1988-06-06",
        "gender": "male",
    }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def _pick_provider(s, *, tenant_admin=True) -> str:
    r = s.get(f"{API}/auth/providers", timeout=10)
    assert r.status_code == 200, r.text
    providers = r.json()
    assert providers, "no providers available"
    return providers[0]["id"]


def _book_appointment(s, *, patient_id: str, provider_id: str, hour_offset: int = 0) -> dict:
    # Scatter minutes to avoid colliding with leftover appointments from
    # previous runs or sibling tests in the same module.
    jitter = random.randint(0, 50)
    base = datetime.now(timezone.utc) + timedelta(days=random.randint(7, 30))
    start = base.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset, minutes=jitter)
    end = start + timedelta(minutes=20)
    r = s.post(f"{API}/appointments", json={
        "patient_id": patient_id,
        "provider_id": provider_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "reason": "Phase 3 encounter test",
    }, timeout=15)
    if r.status_code != 201:
        # Retry once with a new jitter in case of rare random collision.
        start = start + timedelta(hours=1, minutes=random.randint(0, 20))
        end = start + timedelta(minutes=20)
        r = s.post(f"{API}/appointments", json={
            "patient_id": patient_id,
            "provider_id": provider_id,
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "reason": "Phase 3 encounter test",
        }, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


# ============================================================================
# Launch happy path + context freeze + chart visibility
# ============================================================================
def test_launch_happy_path_and_context_is_frozen(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id)

    # Launch a new_patient_exam
    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "new_patient_exam"},
        timeout=10,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["existed"] is False
    enc = body["encounter"]
    assert enc["status"] == "in_progress"
    assert enc["is_exception"] is False
    assert enc["appointment_id"] == appt["id"]
    assert enc["patient_id"] == p["id"]
    assert enc["provider_id"] == provider_id
    assert enc["encounter_type"] == "new_patient_exam"
    assert enc["date_of_service"] == appt["start_time"]
    assert enc["scheduled_duration_min"] == 20
    assert enc["appointment_snapshot"]["status"] == "scheduled"

    # Idempotent relaunch returns the same encounter
    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"},  # ignored on reuse
        timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["existed"] is True
    assert r.json()["encounter"]["id"] == enc["id"]
    # encounter_type is NOT changed by a relaunch
    assert r.json()["encounter"]["encounter_type"] == "new_patient_exam"

    # GET by appointment returns the same encounter
    r = admin.get(f"{API}/appointments/{appt['id']}/clinical/encounter", timeout=10)
    assert r.status_code == 200
    assert r.json()["id"] == enc["id"]

    # Chart visibility — list shows the encounter
    r = admin.get(f"{API}/patients/{p['id']}/clinical/encounters", timeout=10)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["id"] == enc["id"]

    # Summary reflects new encounter counts
    r = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10)
    s = r.json()
    assert s["encounters"] == {"total": 1, "open": 1}

    # Appointment GET now surfaces clinical_encounter_id
    r = admin.get(f"{API}/appointments/{appt['id']}", timeout=10)
    assert r.status_code == 200
    assert r.json().get("clinical_encounter_id") == enc["id"]
    assert r.json().get("clinical_encounter_status") == "in_progress"

    # Now edit the appointment's reason — snapshot must NOT change
    r = admin.patch(
        f"{API}/appointments/{appt['id']}",
        json={"reason": "REWRITTEN REASON"}, timeout=10,
    )
    assert r.status_code == 200
    r = admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}", timeout=10,
    )
    assert r.status_code == 200
    fresh = r.json()
    assert fresh["appointment_snapshot"]["reason"] == "Phase 3 encounter test"


# ============================================================================
# Episode linkage + validation
# ============================================================================
def test_launch_with_episode_linkage_and_cross_tenant_episode_rejected(admin, default_admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id)

    # Create an episode and link on launch
    r = admin.post(f"{API}/patients/{p['id']}/clinical/episodes", json={
        "case_type": "injury_episode", "title": "Phase 3 MOI",
    }, timeout=10)
    eid = r.json()["id"]

    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up", "episode_id": eid},
        timeout=10,
    )
    assert r.status_code == 201, r.text
    assert r.json()["encounter"]["episode_id"] == eid
    assert r.json()["encounter"]["episode_title"] == "Phase 3 MOI"

    # Cross-patient / cross-tenant episode rejection
    other_p = _new_patient(default_admin)
    r2 = default_admin.post(f"{API}/patients/{other_p['id']}/clinical/episodes", json={
        "case_type": "maintenance", "title": "other",
    }, timeout=10)
    cross_ep = r2.json()["id"]
    appt2 = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=2)
    r = admin.post(
        f"{API}/appointments/{appt2['id']}/clinical/encounters",
        json={"encounter_type": "follow_up", "episode_id": cross_ep},
        timeout=10,
    )
    assert r.status_code == 400


# ============================================================================
# Cancelled appointment + exception workflow
# ============================================================================
def test_cancelled_requires_exception_reason(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=4)
    # Cancel the appointment
    r = admin.post(f"{API}/appointments/{appt['id']}/cancel", json={"reason": "patient ill"}, timeout=10)
    assert r.status_code == 200

    # Without exception_reason — rejected
    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"},
        timeout=10,
    )
    assert r.status_code == 409

    # With exception_reason — succeeds + flagged
    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={
            "encounter_type": "follow_up",
            "exception_reason": "Patient came in anyway for same-day documentation",
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    enc = r.json()["encounter"]
    assert enc["is_exception"] is True
    assert enc["exception_reason"] == "Patient came in anyway for same-day documentation"
    assert enc["exception_invoked_by"]
    assert enc["exception_invoked_at"]
    assert enc["appointment_status_at_launch"] == "cancelled"


def test_staff_role_cannot_launch_exception(admin):
    # Seed data as admin
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=6)
    admin.post(f"{API}/appointments/{appt['id']}/cancel", json={"reason": "x"}, timeout=10)

    # Try as a staff user. Sunrise tenant has eastside-staff@sunrise.ccms.app.
    try:
        staff = _login("eastside-staff@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
    except Exception:
        pytest.skip("No staff user seeded for Sunrise tenant")

    r = staff.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={
            "encounter_type": "follow_up",
            "exception_reason": "I should be blocked",
        },
        timeout=10,
    )
    # 403 when the staff reaches the endpoint; 404 if the staff's location
    # scope means they can't see this particular appointment at all. Both
    # are positive signals that the exception workflow is gated off from
    # staff.
    assert r.status_code in (403, 404), r.text


# ============================================================================
# Lifecycle — complete + cancel + patch rules
# ============================================================================
def test_complete_and_cancel_lifecycle(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=8)
    enc = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "treatment_visit"}, timeout=10,
    ).json()["encounter"]

    # PATCH works while in_progress
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
        json={"notes": "halfway through visit"}, timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "halfway through visit"

    # Complete
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/complete",
        json={"notes": "done"}, timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "completed"
    assert r.json()["completed_at"]

    # Double-complete 409
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/complete",
        json={}, timeout=10,
    )
    assert r.status_code == 409

    # PATCH on completed → 409
    r = admin.patch(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
        json={"notes": "illegal edit"}, timeout=10,
    )
    assert r.status_code == 409

    # Cancel completed → 409
    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/cancel",
        json={"reason": "nope"}, timeout=10,
    )
    assert r.status_code == 409

    # Summary reflects completed (open=0, total=1)
    r = admin.get(f"{API}/patients/{p['id']}/clinical/summary", timeout=10)
    s = r.json()
    assert s["encounters"] == {"total": 1, "open": 0}


def test_cancel_encounter_allows_relaunch(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=10)
    enc = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"}, timeout=10,
    ).json()["encounter"]

    r = admin.post(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/cancel",
        json={"reason": "launched on wrong appointment"}, timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"

    # A fresh launch now succeeds (because the previous encounter is cancelled)
    r = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "treatment_visit"}, timeout=10,
    )
    assert r.status_code == 201, r.text
    assert r.json()["encounter"]["id"] != enc["id"]


# ============================================================================
# Reauth + tenant isolation + RBAC
# ============================================================================
def test_writes_require_reauth(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    # widen offset range to reduce any leftover-booking collisions
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=random.randint(30, 60))
    s = _login(*GROUP_ADMIN, reauth=False)
    r = s.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"}, timeout=10,
    )
    assert r.status_code == 401
    body = r.json()
    assert "re-auth" in (body.get("detail") or "").lower()


def test_tenant_isolation_on_encounter_reads_and_writes(admin, default_admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=14)
    enc = admin.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"}, timeout=10,
    ).json()["encounter"]

    # Default tenant admin cannot see it via any path
    assert default_admin.get(
        f"{API}/appointments/{appt['id']}/clinical/encounter", timeout=10,
    ).status_code == 404
    assert default_admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters", timeout=10,
    ).status_code == 404
    assert default_admin.get(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}", timeout=10,
    ).status_code == 404
    r = default_admin.patch(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}",
        json={"notes": "hack"}, timeout=10,
    )
    assert r.status_code == 404
    r = default_admin.post(
        f"{API}/patients/{p['id']}/clinical/encounters/{enc['id']}/complete",
        json={}, timeout=10,
    )
    assert r.status_code == 404


def test_patient_role_blocked_on_encounter_launch(admin):
    p = _new_patient(admin)
    provider_id = _pick_provider(admin)
    appt = _book_appointment(admin, patient_id=p["id"], provider_id=provider_id, hour_offset=16)
    pt = _login(*PATIENT_USER, reauth=False)
    r = pt.post(
        f"{API}/appointments/{appt['id']}/clinical/encounters",
        json={"encounter_type": "follow_up"}, timeout=10,
    )
    # patient role is not admin/doctor/staff — 401 (no reauth) or 403 are valid
    assert r.status_code in (401, 403, 404)
