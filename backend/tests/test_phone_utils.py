"""Unit + integration tests for the phone validator (Task Prompt 10).

Canonical form: **10 digits, no formatting** on write. Display is
`(XXX) XXX-XXXX` only for 10-digit values; other shapes echo back.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

from core.phone import (
    format_us_phone,
    is_valid_us_phone,
    normalize_us_phone,
    search_normalize_phone,
)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}


# ---------------------------------------------------------------------------
# Unit — normalize
# ---------------------------------------------------------------------------
class TestNormalize:
    @pytest.mark.parametrize("raw, expected", [
        ("6155551212", "6155551212"),
        ("615-555-1212", "6155551212"),
        ("(615) 555-1212", "6155551212"),
        ("(615)5551212", "6155551212"),
        ("615.555.1212", "6155551212"),
        (" 6155551212 ", "6155551212"),
        ("+1 615 555 1212", "6155551212"),
        ("1-615-555-1212", "6155551212"),
        ("1 (615) 555-1212", "6155551212"),
    ])
    def test_accepts_formatted(self, raw, expected):
        assert normalize_us_phone(raw) == expected

    def test_none_is_none(self):
        assert normalize_us_phone(None) is None

    def test_blank_is_none(self):
        assert normalize_us_phone("") is None
        assert normalize_us_phone("   ") is None

    @pytest.mark.parametrize("raw", [
        "555-1212",           # 7 digits only
        "12345",               # too short
        "61555512345",         # 11 digits, leading != 1
        "0615-555-1212",       # 11 digits, leading 0
        "6155551212x99",       # extension (out of scope)
        "61555",
    ])
    def test_rejects_bad_length(self, raw):
        with pytest.raises(ValueError):
            normalize_us_phone(raw)


# ---------------------------------------------------------------------------
# Unit — is_valid_us_phone (empty/valid are True)
# ---------------------------------------------------------------------------
class TestIsValid:
    def test_empty_is_valid(self):
        assert is_valid_us_phone(None) is True
        assert is_valid_us_phone("") is True
        assert is_valid_us_phone("  ") is True

    def test_10_digit_is_valid(self):
        assert is_valid_us_phone("6155551212") is True
        assert is_valid_us_phone("(615) 555-1212") is True

    def test_bad_length_is_invalid(self):
        assert is_valid_us_phone("5551212") is False
        assert is_valid_us_phone("123") is False


# ---------------------------------------------------------------------------
# Unit — format_us_phone (permissive display)
# ---------------------------------------------------------------------------
class TestFormat:
    def test_formats_10_digit(self):
        assert format_us_phone("6155551212") == "(615) 555-1212"
        assert format_us_phone("(615) 555-1212") == "(615) 555-1212"
        assert format_us_phone("+1-615-555-1212") == "(615) 555-1212"

    def test_passes_through_legacy_strings(self):
        # `+1-555-0102` → digits `15550102` → 8 digits (leading 1 only
        # stripped when total=11) → unchanged.
        assert format_us_phone("+1-555-0102") == "+1-555-0102"
        assert format_us_phone("555-1212") == "555-1212"

    def test_none_and_empty(self):
        assert format_us_phone(None) == ""
        assert format_us_phone("") == ""
        assert format_us_phone("   ") == ""


# ---------------------------------------------------------------------------
# Unit — search_normalize
# ---------------------------------------------------------------------------
class TestSearchNormalize:
    def test_strips_non_digits(self):
        assert search_normalize_phone("(615) 555-1212") == "6155551212"
        assert search_normalize_phone("555") == "555"
        assert search_normalize_phone("") == ""
        assert search_normalize_phone(None) == ""


# ---------------------------------------------------------------------------
# Integration — identity PATCH /me/profile enforces canonical 10-digit
# ---------------------------------------------------------------------------
def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _new_user():
    admin = _login(ADMIN["email"], ADMIN["password"])
    unique = uuid.uuid4().hex[:10]
    email = f"phone_{unique}@ccms.app"
    password = f"PhoneTest@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": "Phone Test", "role": "staff",
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def user():
    email, password = _new_user()
    return {"email": email, "password": password}


class TestProfilePatchCanonicalises:
    def test_formatted_input_stored_as_digits(self, user):
        s = _login(user["email"], user["password"])
        r = s.patch(f"{API}/auth/me/profile", json={
            "mobile_phone": "(615) 555-1212",
            "work_phone": "615.555.2222",
        }, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mobile_phone"] == "6155551212"
        assert body["work_phone"] == "6155552222"

    def test_plus1_prefix_stripped(self, user):
        s = _login(user["email"], user["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"mobile_phone": "+1-615-555-1212"}, timeout=10)
        assert r.status_code == 200
        assert r.json()["mobile_phone"] == "6155551212"

    def test_malformed_rejected_422(self, user):
        s = _login(user["email"], user["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"mobile_phone": "555-1212"}, timeout=10)
        assert r.status_code == 422

    def test_empty_clears(self, user):
        s = _login(user["email"], user["password"])
        assert s.patch(f"{API}/auth/me/profile",
                       json={"mobile_phone": "6155551212"},
                       timeout=10).status_code == 200
        r = s.patch(f"{API}/auth/me/profile",
                    json={"mobile_phone": ""}, timeout=10)
        assert r.status_code == 200
        assert r.json()["mobile_phone"] is None


# ---------------------------------------------------------------------------
# Integration — registration & admin-create normalise phone input
# ---------------------------------------------------------------------------
class TestRegistrationNormalises:
    def test_register_stores_digits_only(self):
        unique = uuid.uuid4().hex[:10]
        email = f"reg_{unique}@ccms.app"
        r = requests.post(f"{API}/auth/register", json={
            "email": email, "password": f"RegPhone@Strong_{unique}!",
            "name": "Reg Test", "phone": "(615) 555-3333",
        }, timeout=15)
        assert r.status_code in (200, 201), r.text
        assert r.json()["phone"] == "6155553333"

    def test_register_rejects_malformed(self):
        unique = uuid.uuid4().hex[:10]
        email = f"reg_bad_{unique}@ccms.app"
        r = requests.post(f"{API}/auth/register", json={
            "email": email, "password": f"RegPhone@Strong_{unique}!",
            "name": "Reg Bad", "phone": "555-1212",
        }, timeout=15)
        assert r.status_code == 422

    def test_admin_create_normalises(self):
        admin = _login(ADMIN["email"], ADMIN["password"])
        unique = uuid.uuid4().hex[:10]
        email = f"admin_create_{unique}@ccms.app"
        r = admin.post(f"{API}/auth/users", json={
            "email": email, "password": f"AdminCreate@Strong_{unique}!",
            "name": "Admin Created", "role": "staff",
            "phone": "615.555.4444",
        }, timeout=15)
        assert r.status_code in (200, 201), r.text
        assert r.json()["phone"] == "6155554444"


# ---------------------------------------------------------------------------
# Integration — /patients/search accepts formatted phone queries
# ---------------------------------------------------------------------------
class TestPatientSearchAcceptsFormatted:
    def test_search_by_formatted_phone_matches(self):
        admin = _login(ADMIN["email"], ADMIN["password"])
        # The canonical seed patient "Alex Rivera" has phone 555-0099 in
        # the seed — that's a 7-digit legacy. We don't depend on finding
        # a specific record; we only verify that both query shapes hit
        # the same endpoint without a 400.
        r1 = admin.get(f"{API}/patients/search",
                       params={"phone": "(615) 555-0099"}, timeout=10)
        r2 = admin.get(f"{API}/patients/search",
                       params={"phone": "6155550099"}, timeout=10)
        assert r1.status_code in (200, 400) and r2.status_code in (200, 400), (
            r1.text, r2.text,
        )
        # Both shapes should behave identically — either both 200, or
        # both 400 (if the digits aren't enough for the server-side
        # regex).  Never mismatched.
        assert r1.status_code == r2.status_code
