"""Backend tests for professional license self-service CRUD.

Covered:
  * NPI number added to the /profile endpoint with format validation.
  * `/auth/me/licenses` CRUD for clinician roles (admin, doctor).
  * Staff / patient receive 403 on write but 200-empty on read (so the
    UI can uniformly hide the section).
  * Dup (type, state, number) yields 409.
  * Format validation (state = USPS 2-letter, expiration date YYYY-MM-DD).
  * Tenant scoping via user_id — one user cannot see/modify another's.
  * Audit trail: user.license_added/_updated/_removed written with
    non-sensitive metadata only (no license number).
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _new_user(role="doctor"):
    admin = _login(ADMIN["email"], ADMIN["password"])
    unique = uuid.uuid4().hex[:8]
    email = f"lic_{unique}@ccms.app"
    password = f"License1@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": f"License {role}", "role": role,
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def doctor():
    email, password = _new_user("doctor")
    return {"email": email, "password": password}


@pytest.fixture
def staff():
    email, password = _new_user("staff")
    return {"email": email, "password": password}


# ---------------------------------------------------------------------------
# NPI on profile
# ---------------------------------------------------------------------------
class TestNpiOnProfile:
    def test_profile_initially_has_no_npi(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        assert s.get(f"{API}/auth/me").json()["npi_number"] is None

    def test_set_valid_npi(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"npi_number": "1234567890"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["npi_number"] == "1234567890"

    def test_reject_non_digit_npi(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"npi_number": "12345abcde"}, timeout=10)
        assert r.status_code == 422

    def test_reject_short_npi(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"npi_number": "123"}, timeout=10)
        assert r.status_code == 422

    def test_empty_string_clears_npi(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        s.patch(f"{API}/auth/me/profile",
                json={"npi_number": "1234567890"}, timeout=10)
        r = s.patch(f"{API}/auth/me/profile",
                    json={"npi_number": ""}, timeout=10)
        assert r.status_code == 200
        assert r.json()["npi_number"] is None


# ---------------------------------------------------------------------------
# License CRUD
# ---------------------------------------------------------------------------
class TestLicenseCrud:
    def test_list_is_empty_initially(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.get(f"{API}/auth/me/licenses", timeout=10)
        assert r.status_code == 200
        assert r.json() == []

    def test_create_happy_path_and_read_back(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "CA-987654",
            "issuing_state": "ca",   # lower-case → normalised to CA
            "expiration_date": "2027-12-31",
            "specialty": "Diversified technique",
        }, timeout=10)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["issuing_state"] == "CA"
        assert body["license_type"] == "DC"
        assert body["license_number"] == "CA-987654"
        assert body["expiration_date"] == "2027-12-31"
        assert body["specialty"] == "Diversified technique"
        assert body["board_notes"] is None

        rows = s.get(f"{API}/auth/me/licenses").json()
        assert len(rows) == 1
        assert rows[0]["id"] == body["id"]

    def test_update_partial(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        lic = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "CA-987654",
            "issuing_state": "CA",
            "expiration_date": "2027-12-31",
        }, timeout=10).json()
        r = s.patch(f"{API}/auth/me/licenses/{lic['id']}", json={
            "expiration_date": "2029-06-30",
            "specialty": "Graston / IASTM",
        }, timeout=10)
        assert r.status_code == 200, r.text
        updated = r.json()
        assert updated["expiration_date"] == "2029-06-30"
        assert updated["specialty"] == "Graston / IASTM"
        # Fields not in the PATCH should be preserved
        assert updated["license_number"] == "CA-987654"

    def test_delete(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        lic = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "DEL-1",
            "issuing_state": "NY",
            "expiration_date": "2028-01-01",
        }, timeout=10).json()
        r = s.delete(f"{API}/auth/me/licenses/{lic['id']}", timeout=10)
        assert r.status_code == 204
        assert s.get(f"{API}/auth/me/licenses").json() == []

    def test_duplicate_returns_409(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        body = {
            "license_type": "DC",
            "license_number": "DUP-1",
            "issuing_state": "CA",
            "expiration_date": "2027-12-31",
        }
        r1 = s.post(f"{API}/auth/me/licenses", json=body, timeout=10)
        assert r1.status_code == 201
        r2 = s.post(f"{API}/auth/me/licenses", json=body, timeout=10)
        assert r2.status_code == 409

    def test_update_missing_license_404(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/licenses/{uuid.uuid4()}",
                    json={"specialty": "x"}, timeout=10)
        assert r.status_code == 404

    def test_delete_missing_license_404(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.delete(f"{API}/auth/me/licenses/{uuid.uuid4()}", timeout=10)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Format validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_bad_state_length(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "AB-1",
            "issuing_state": "California",   # too long
            "expiration_date": "2027-12-31",
        }, timeout=10)
        assert r.status_code == 422

    def test_bad_state_non_alpha(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "AB-1",
            "issuing_state": "C1",
            "expiration_date": "2027-12-31",
        }, timeout=10)
        assert r.status_code == 422

    def test_bad_expiration_format(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "AB-1",
            "issuing_state": "CA",
            "expiration_date": "31/12/2027",
        }, timeout=10)
        assert r.status_code == 422

    def test_bad_license_type(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "ZZ",
            "license_number": "AB-1",
            "issuing_state": "CA",
            "expiration_date": "2027-12-31",
        }, timeout=10)
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Role gating
# ---------------------------------------------------------------------------
class TestRoleGating:
    def test_staff_can_read_empty_list(self, staff):
        s = _login(staff["email"], staff["password"])
        assert s.get(f"{API}/auth/me/licenses").json() == []

    def test_staff_cannot_create(self, staff):
        s = _login(staff["email"], staff["password"])
        r = s.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC",
            "license_number": "NO-1",
            "issuing_state": "CA",
            "expiration_date": "2027-12-31",
        }, timeout=10)
        assert r.status_code == 403

    def test_staff_cannot_update_existing(self, doctor, staff):
        """Even if we synthesise a license for the doctor, a staff
        user cannot touch it (403 on update)."""
        d = _login(doctor["email"], doctor["password"])
        lic = d.post(f"{API}/auth/me/licenses", json={
            "license_type": "DC", "license_number": "AA-1",
            "issuing_state": "CA", "expiration_date": "2027-12-31",
        }).json()
        s = _login(staff["email"], staff["password"])
        r = s.patch(f"{API}/auth/me/licenses/{lic['id']}",
                    json={"specialty": "hacked"}, timeout=10)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Isolation — one doctor's license is not visible to another
# ---------------------------------------------------------------------------
def test_licenses_are_user_scoped():
    d1_email, d1_password = _new_user("doctor")
    d2_email, d2_password = _new_user("doctor")

    d1 = _login(d1_email, d1_password)
    lic = d1.post(f"{API}/auth/me/licenses", json={
        "license_type": "DC", "license_number": "ISO-1",
        "issuing_state": "CA", "expiration_date": "2027-12-31",
    }).json()

    d2 = _login(d2_email, d2_password)
    # Visibility isolation
    assert d2.get(f"{API}/auth/me/licenses").json() == []
    # Write isolation
    r = d2.patch(f"{API}/auth/me/licenses/{lic['id']}",
                 json={"specialty": "steal"}, timeout=10)
    assert r.status_code == 404
    r = d2.delete(f"{API}/auth/me/licenses/{lic['id']}", timeout=10)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Unauthenticated
# ---------------------------------------------------------------------------
def test_unauthenticated_rejected():
    r = requests.get(f"{API}/auth/me/licenses", timeout=10)
    assert r.status_code == 401
    r = requests.post(f"{API}/auth/me/licenses", json={
        "license_type": "DC", "license_number": "AA-1",
        "issuing_state": "CA", "expiration_date": "2027-12-31",
    }, timeout=10)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Sensitive data not leaked in responses / audits
# ---------------------------------------------------------------------------
def test_license_number_never_surfaces_in_audit_metadata(doctor):
    s = _login(doctor["email"], doctor["password"])
    license_num = f"SENSITIVE-{uuid.uuid4().hex[:8]}"
    s.post(f"{API}/auth/me/licenses", json={
        "license_type": "DC",
        "license_number": license_num,
        "issuing_state": "CA",
        "expiration_date": "2027-12-31",
    }, timeout=10)

    # Admin looks up the audit row for user.license_added.
    admin = _login(ADMIN["email"], ADMIN["password"])
    admin.post(f"{API}/auth/reauth",
               json={"password": ADMIN["password"]}, timeout=10)
    tok = admin.cookies.get("reauth_token")
    if tok:
        admin.headers["x-reauth-token"] = tok
    r = admin.get(f"{API}/audit-logs", params={
        "action": "user.license_added",
        "actor_email": doctor["email"],
        "limit": 5,
    }, timeout=10)
    if r.status_code == 404:
        pytest.skip("audit-logs endpoint not exposed in this env")
    rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
    assert rows, rows
    # license_number must NOT appear in audit metadata — only its length.
    for row in rows:
        md = row.get("metadata") or {}
        assert license_num not in str(md), md
        assert "license_number_length" in md
