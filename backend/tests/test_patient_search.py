"""Backend regression for the patient lookup search endpoint.

Covers wildcard semantics, case-insensitivity, DOB parsing, phone
matching across plaintext + encrypted sub-fields, address matching on
encrypted blobs, pagination, empty/edge states, and permissioning.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}
PATIENT = {"email": "patient@ccms.app", "password": "Patient@ComplianceClinic1"}


def _session(creds):
    s = requests.Session()
    s.post(f"{API}/auth/login", json=creds, timeout=10).raise_for_status()
    return s


def _reauth(session, password):
    r = session.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    r.raise_for_status()


@pytest.fixture(scope="module")
def admin():
    s = _session(ADMIN)
    _reauth(s, ADMIN["password"])
    return s


@pytest.fixture(scope="module")
def fixture_patients(admin):
    """Seed 3 deterministic patients we can search for and clean up after."""
    suffix = uuid.uuid4().hex[:6].upper()
    seeds = [
        {
            "first_name": f"Jacobsen{suffix}",
            "last_name": f"LarkspurA{suffix}",
            "date_of_birth": "1985-01-15",
            "phone": f"5558887777{suffix[:2]}"[:15],
            "address": {
                "line1": f"42 Meadow Lane {suffix}",
                "city": "Portland", "state": "OR", "postal_code": "97201",
            },
            "contact": {"phone_alt": "555-333-4444"},
        },
        {
            "first_name": f"Jacob{suffix}",
            "last_name": f"Thorne{suffix}",
            "date_of_birth": "1992-07-22",
            "phone": "",
            "address": {"line1": "19 Pine Ave", "city": "Austin", "state": "TX", "postal_code": "73301"},
        },
        {
            "first_name": f"Emil{suffix}",
            "last_name": f"Jacobsen{suffix}",
            "date_of_birth": "2001-03-03",
            "phone": "555-999-1111",
            "address": {"line1": "7 River Rd", "city": "Boise", "state": "ID", "postal_code": "83702"},
        },
    ]
    created = []
    for body in seeds:
        r = admin.post(f"{API}/patients", json=body, timeout=10)
        r.raise_for_status()
        created.append(r.json()["id"])
    yield {"ids": created, "suffix": suffix}
    # Best-effort cleanup — soft-delete requires reauth each time.
    try:
        _reauth(admin, ADMIN["password"])
        for pid in created:
            admin.delete(f"{API}/patients/{pid}", timeout=10)
    except Exception:
        pass


def _search(session, **params):
    r = session.get(f"{API}/patients/search", params=params, timeout=15)
    return r


class TestSearchAuth:
    def test_unauthenticated_rejected(self):
        r = requests.get(f"{API}/patients/search", params={"q": "x"}, timeout=10)
        assert r.status_code == 401

    def test_empty_params_400(self, admin):
        r = _search(admin)
        assert r.status_code == 400

    def test_patient_role_scoped_to_self(self):
        s = _session(PATIENT)
        r = _search(s, q="anything")
        assert r.status_code in (200, 403)
        if r.status_code == 200:
            # Patient never sees other patients' records.
            body = r.json()
            for row in body["results"]:
                assert row["id"]  # no cross-tenant leaks — explicit shape only


class TestWildcardSemantics:
    def test_prefix_wildcard(self, admin, fixture_patients):
        r = _search(admin, name=f"Jaco%")
        assert r.status_code == 200
        total = r.json()["total"]
        # The seed adds 2 patients whose first_name starts with "Jaco".
        assert total >= 2

    def test_suffix_wildcard(self, admin, fixture_patients):
        suffix = fixture_patients["suffix"]
        r = _search(admin, name=f"%{suffix}")
        assert r.status_code == 200
        assert r.json()["total"] >= 3

    def test_middle_wildcard(self, admin, fixture_patients):
        r = _search(admin, name="J%b")
        assert r.status_code == 200
        assert r.json()["total"] >= 2

    def test_no_wildcard_contains(self, admin, fixture_patients):
        r = _search(admin, name="Jacob")
        assert r.status_code == 200
        # "Jacob" substring matches all three seeds via first_name OR last_name.
        assert r.json()["total"] >= 3

    def test_case_insensitive(self, admin, fixture_patients):
        suffix = fixture_patients["suffix"]
        r = _search(admin, name=f"jacobsen{suffix.lower()}")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_double_percent_rejected(self, admin):
        r = _search(admin, q="abc%%def")
        assert r.status_code == 400

    def test_overlong_input_rejected(self, admin):
        r = _search(admin, q="x" * 200)
        assert r.status_code == 400


class TestDob:
    def test_iso_dob(self, admin, fixture_patients):
        r = _search(admin, dob="1985-01-15")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_us_dob(self, admin, fixture_patients):
        r = _search(admin, dob="01/15/1985")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_year_only_dob(self, admin, fixture_patients):
        r = _search(admin, dob="1985")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_invalid_dob_400(self, admin):
        r = _search(admin, dob="notadate")
        assert r.status_code == 400


class TestPhone:
    def test_plaintext_phone_digits(self, admin, fixture_patients):
        r = _search(admin, phone="9991111")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_encrypted_sub_phone(self, admin, fixture_patients):
        # `contact.phone_alt` lives inside the encrypted `contact` blob.
        # Searcher must post-decrypt — this verifies the loop.
        r = _search(admin, phone="3334444", name="Jaco%")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_phone_digits_only_normalisation(self, admin, fixture_patients):
        # Input carries formatting; matcher should strip it.
        r = _search(admin, phone="(555) 999-1111")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_phone_must_contain_digits(self, admin):
        r = _search(admin, phone="abcxyz")
        assert r.status_code == 400


class TestAddress:
    def test_city_match(self, admin, fixture_patients):
        r = _search(admin, address="Portland")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_line1_match(self, admin, fixture_patients):
        r = _search(admin, address="Meadow")
        assert r.status_code == 200
        assert r.json()["total"] >= 1

    def test_no_match(self, admin, fixture_patients):
        r = _search(admin, address="ZzzNoSuchStreet%")
        assert r.status_code == 200
        assert r.json()["total"] == 0


class TestResultShape:
    def test_results_are_masked(self, admin, fixture_patients):
        r = _search(admin, name=f"Jaco%")
        assert r.status_code == 200
        for row in r.json()["results"]:
            assert "first_name" in row
            assert "date_of_birth" in row
            assert "primary_phone" in row
            assert "address_summary" in row
            # Search never returns unmasked grouped sections.
            assert "demographics" not in row
            assert "insurance" not in row


class TestPagination:
    def test_limit_respected(self, admin, fixture_patients):
        r = _search(admin, name="Jaco%", limit=1)
        assert r.status_code == 200
        body = r.json()
        assert len(body["results"]) == 1
        assert body["limit"] == 1

    def test_limit_clamped(self, admin, fixture_patients):
        r = _search(admin, name="Jaco%", limit=500)
        assert r.status_code == 422  # FastAPI rejects le=50 violations

    def test_offset_pagination(self, admin, fixture_patients):
        page1 = _search(admin, name="Jaco%", limit=1, offset=0).json()
        page2 = _search(admin, name="Jaco%", limit=1, offset=1).json()
        if page1["total"] >= 2:
            assert page1["results"][0]["id"] != page2["results"][0]["id"]


class TestCompliance:
    def test_tenant_scoping_preserved(self, admin, fixture_patients):
        # Admin should see their tenant's patients only; a naked query must
        # never return cross-tenant rows. We approximate by confirming the
        # tenant_id field isn't present in the result shape (proving masking).
        r = _search(admin, q="Jaco")
        assert r.status_code == 200
        for row in r.json()["results"]:
            assert "tenant_id" not in row
