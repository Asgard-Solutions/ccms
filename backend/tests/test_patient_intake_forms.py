"""Multi-version patient intake forms — end-to-end API tests.

Covers:
  * GET /patients/{id}/intake-forms lists forms newest first
  * POST seeds from patient.clinical_intake by default
  * PATCH only updates fields explicitly present (exclude_unset)
  * PATCH on completed form -> 409 (immutable)
  * Completing a form stamps captured_at / captured_by
  * DELETE only works on drafts
  * Cross-tenant isolation
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    assert access, f"no access_token cookie: {dict(r.cookies)}"
    s.headers["Authorization"] = f"Bearer {access}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text
    reauth = r.cookies.get("reauth_token")
    if reauth:
        s.headers["x-reauth-token"] = reauth
    return s


@pytest.fixture(scope="module")
def sunrise_admin():
    return _login(*GROUP_ADMIN)


@pytest.fixture(scope="module")
def default_admin():
    return _login(*DEFAULT_ADMIN)


@pytest.fixture
def patient(sunrise_admin):
    """Create a patient with clinical_intake already populated — so we can
    verify seeding behavior."""
    s = sunrise_admin
    r = s.post(f"{API}/patients", json={
        "first_name": "Intake",
        "last_name": f"V{uuid.uuid4().hex[:6]}",
        "email": f"intake_{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+1-555-0100",
        "date_of_birth": "1985-03-12",
        "gender": "female",
        "clinical_intake": {
            "chief_complaint": "Low back pain",
            "pain_level": 6,
            "pain_locations": ["Lower back", "Right hip"],
            "symptoms": ["Stiffness", "Tingling"],
            "medications": "Ibuprofen PRN",
        },
        "case_details": {
            "case_type": "auto_accident",
            "date_of_injury": "2026-01-15",
            "claim_number": "AUTO-42",
        },
    }, timeout=15)
    assert r.status_code == 201, r.text
    yield r.json()


def test_list_empty_then_post_seeds_from_patient(sunrise_admin, patient):
    s = sunrise_admin
    pid = patient["id"]

    r = s.get(f"{API}/patients/{pid}/intake-forms", timeout=10)
    assert r.status_code == 200, r.text
    assert r.json() == []

    r = s.post(f"{API}/patients/{pid}/intake-forms",
               json={"seed_from_patient": True}, timeout=10)
    assert r.status_code == 201, r.text
    form = r.json()
    assert form["status"] == "draft"
    assert form["version"] == 1
    assert form["captured_at"] is None
    # Seeded from patient.
    assert form["clinical_intake"]["chief_complaint"] == "Low back pain"
    assert form["clinical_intake"]["pain_level"] == 6
    assert form["case_details"]["case_type"] == "auto_accident"

    # Second POST without seed — empty body.
    r = s.post(f"{API}/patients/{pid}/intake-forms",
               json={"seed_from_patient": False}, timeout=10)
    assert r.status_code == 201, r.text
    second = r.json()
    assert second["version"] == 2
    assert second["clinical_intake"] is None or second["clinical_intake"] == {}
    assert second["case_details"] is None or second["case_details"] == {}

    # Newest first in the list.
    lst = s.get(f"{API}/patients/{pid}/intake-forms", timeout=10).json()
    assert len(lst) == 2
    assert lst[0]["id"] == second["id"]
    assert lst[1]["id"] == form["id"]


def test_patch_only_updates_supplied_fields(sunrise_admin, patient):
    s = sunrise_admin
    pid = patient["id"]

    form = s.post(f"{API}/patients/{pid}/intake-forms",
                  json={"seed_from_patient": True}, timeout=10).json()
    fid = form["id"]

    # Patch only notes — clinical_intake must be untouched.
    r = s.patch(f"{API}/patients/{pid}/intake-forms/{fid}",
                json={"notes": "Patient late for appt"}, timeout=10)
    assert r.status_code == 200, r.text
    updated = r.json()
    assert updated["notes"] == "Patient late for appt"
    assert updated["clinical_intake"]["chief_complaint"] == "Low back pain"
    # Status still draft, captured_at still None.
    assert updated["status"] == "draft"
    assert updated["captured_at"] is None

    # Empty body -> 400.
    r = s.patch(f"{API}/patients/{pid}/intake-forms/{fid}",
                json={}, timeout=10)
    assert r.status_code == 400, r.text


def test_complete_then_immutable(sunrise_admin, patient):
    s = sunrise_admin
    pid = patient["id"]
    form = s.post(f"{API}/patients/{pid}/intake-forms",
                  json={"seed_from_patient": True}, timeout=10).json()
    fid = form["id"]

    r = s.patch(f"{API}/patients/{pid}/intake-forms/{fid}",
                json={"status": "completed"}, timeout=10)
    assert r.status_code == 200, r.text
    completed = r.json()
    assert completed["status"] == "completed"
    assert completed["captured_at"] is not None
    assert completed["captured_by"] is not None

    # Further PATCH rejected.
    r = s.patch(f"{API}/patients/{pid}/intake-forms/{fid}",
                json={"notes": "sneaky"}, timeout=10)
    assert r.status_code == 409, r.text

    # DELETE rejected on completed.
    r = s.delete(f"{API}/patients/{pid}/intake-forms/{fid}", timeout=10)
    assert r.status_code == 409, r.text


def test_delete_draft(sunrise_admin, patient):
    s = sunrise_admin
    pid = patient["id"]
    form = s.post(f"{API}/patients/{pid}/intake-forms",
                  json={"seed_from_patient": False}, timeout=10).json()
    fid = form["id"]
    r = s.delete(f"{API}/patients/{pid}/intake-forms/{fid}", timeout=10)
    assert r.status_code == 204, r.text
    r = s.get(f"{API}/patients/{pid}/intake-forms/{fid}", timeout=10)
    assert r.status_code == 404


def test_cross_tenant_isolation(sunrise_admin, default_admin, patient):
    # default tenant admin cannot see sunrise patient's intake forms.
    pid = patient["id"]
    r = default_admin.get(f"{API}/patients/{pid}/intake-forms", timeout=10)
    assert r.status_code == 404, r.text

    r = default_admin.post(f"{API}/patients/{pid}/intake-forms",
                           json={"seed_from_patient": False}, timeout=10)
    assert r.status_code == 404, r.text
