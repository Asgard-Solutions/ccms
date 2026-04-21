"""Unit + integration tests for the DEA validator (Task Prompt 9).

Contract:
  * exactly 9 characters, format `[A-Z]{2}[0-9]{7}`
  * first letter ∈ VALID_REGISTRANT_CODES (DEA-published set)
  * second letter any A-Z (last-name match is SOFT, optional warning)
  * last digit is the Luhn-ish DEA check digit computed over the
    6-digit body: ``(odd + even*2) % 10`` where `odd = d1+d3+d5` and
    `even = d2+d4+d6` (1-indexed within the 6-digit block)
  * input is trimmed + upper-cased before validation
  * lower-case & mixed-case inputs are accepted after normalisation
  * whitespace-in-the-middle / dashes / letters past position 2 are
    rejected

Backend enforcement is verified via PATCH /auth/me/profile.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

from core.dea import (
    DeaValidationError,
    VALID_REGISTRANT_CODES,
    compute_dea_check_digit,
    is_valid_dea,
    matches_last_name_initial,
    validate_dea_or_raise,
)

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}

# Canonical examples used across tests.
VALID_DEAS = ("AB1234563", "AB9876547", "BM9999991", "CF1000001")
INVALID_CHECKSUM = ("AB1234567", "AB1234560")   # wrong check digits
INVALID_REGISTRANT = ("IB1234563", "NA1234563", "QA1234563")


# ---------------------------------------------------------------------------
# Unit — compute_dea_check_digit
# ---------------------------------------------------------------------------
class TestComputeDeaCheckDigit:
    def test_cms_example(self):
        # Official CMS-style worked example: body 123456
        #   odd  = 1 + 3 + 5 = 9
        #   even = (2 + 4 + 6) * 2 = 24
        #   total = 33, check digit = 3.
        assert compute_dea_check_digit("123456") == 3

    def test_zero_body(self):
        assert compute_dea_check_digit("000000") == 0

    def test_rejects_wrong_length(self):
        with pytest.raises(ValueError):
            compute_dea_check_digit("12345")
        with pytest.raises(ValueError):
            compute_dea_check_digit("1234567")

    def test_rejects_non_digit(self):
        with pytest.raises(ValueError):
            compute_dea_check_digit("12345A")


# ---------------------------------------------------------------------------
# Unit — is_valid_dea
# ---------------------------------------------------------------------------
class TestIsValidDea:
    @pytest.mark.parametrize("dea", VALID_DEAS)
    def test_known_valid(self, dea):
        assert is_valid_dea(dea) is True

    @pytest.mark.parametrize("dea", INVALID_CHECKSUM)
    def test_wrong_checksum(self, dea):
        assert is_valid_dea(dea) is False

    @pytest.mark.parametrize("dea", INVALID_REGISTRANT)
    def test_invalid_registrant_letter(self, dea):
        assert is_valid_dea(dea) is False

    def test_lowercase_is_normalised(self):
        assert is_valid_dea("ab1234563") is True

    def test_mixed_case_is_normalised(self):
        assert is_valid_dea("Ab1234563") is True

    def test_surrounding_whitespace_is_trimmed(self):
        assert is_valid_dea("  AB1234563  ") is True
        assert is_valid_dea("\tAB1234563\n") is True

    @pytest.mark.parametrize("bad", [
        "",
        None,
        "A1234563",              # too short
        "AB12345631",            # too long
        "AB-1234563",            # dash at the boundary
        "AB 1234563",            # internal space
        "AB123456X",             # letter in the body
        "3B1234563",             # digit as first char
        "A$1234563",             # symbol
    ])
    def test_structurally_garbage(self, bad):
        assert is_valid_dea(bad) is False


# ---------------------------------------------------------------------------
# Unit — validate_dea_or_raise (specific error messages)
# ---------------------------------------------------------------------------
class TestValidateDeaOrRaise:
    def test_happy_path_returns_normalised(self):
        assert validate_dea_or_raise("  ab1234563  ") == "AB1234563"

    def test_empty_raises(self):
        with pytest.raises(DeaValidationError, match="required"):
            validate_dea_or_raise("")
        with pytest.raises(DeaValidationError, match="required"):
            validate_dea_or_raise(None)

    def test_length_mismatch_message(self):
        with pytest.raises(DeaValidationError, match="9 characters"):
            validate_dea_or_raise("AB12345")

    def test_leading_digit_message(self):
        with pytest.raises(DeaValidationError, match="two letters"):
            validate_dea_or_raise("3B1234563")

    def test_invalid_registrant_message(self):
        with pytest.raises(DeaValidationError, match="registrant-type"):
            validate_dea_or_raise("IB1234563")

    def test_non_digit_body_message(self):
        with pytest.raises(DeaValidationError, match="digits"):
            validate_dea_or_raise("AB123456A")

    def test_checksum_mismatch_message(self):
        with pytest.raises(DeaValidationError, match="checksum"):
            validate_dea_or_raise("AB1234560")


# ---------------------------------------------------------------------------
# Unit — matches_last_name_initial (soft heuristic)
# ---------------------------------------------------------------------------
class TestMatchesLastNameInitial:
    def test_match(self):
        assert matches_last_name_initial("AB1234563", "Bennett") is True
        assert matches_last_name_initial("ab1234563", "bennett") is True

    def test_mismatch(self):
        assert matches_last_name_initial("AB1234563", "Smith") is False

    def test_empty_inputs_are_false(self):
        assert matches_last_name_initial("", "Smith") is False
        assert matches_last_name_initial("AB1234563", "") is False
        assert matches_last_name_initial("AB1234563", None) is False


def test_registrant_code_set_is_expected():
    # Spot-check that the published-and-used set excludes I, N, O, Q,
    # V, W, Y, Z. If this list ever has to expand (e.g. novel DEA
    # registrant code), update both `core/dea.py` and this assertion.
    assert "C" in VALID_REGISTRANT_CODES
    assert "M" in VALID_REGISTRANT_CODES
    assert "X" in VALID_REGISTRANT_CODES
    for letter in "INOQVWYZ":
        assert letter not in VALID_REGISTRANT_CODES, letter


# ---------------------------------------------------------------------------
# Integration — PATCH /auth/me/profile enforces DEA rules server-side
# ---------------------------------------------------------------------------
def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _new_doctor():
    admin = _login(ADMIN["email"], ADMIN["password"])
    unique = uuid.uuid4().hex[:8]
    email = f"dea_{unique}@ccms.app"
    password = f"DeaTest@Strong_{unique}!"
    r = admin.post(f"{API}/auth/users", json={
        "email": email, "password": password,
        "name": "DEA Test Doctor", "role": "doctor",
    }, timeout=15)
    assert r.status_code in (200, 201), r.text
    return email, password


@pytest.fixture
def doctor():
    email, password = _new_doctor()
    return {"email": email, "password": password}


class TestBackendDeaEnforcement:
    def test_accepts_valid_dea(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "AB1234563"}, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dea_number"] == "AB1234563"

    def test_accepts_lowercase_and_normalises_upper(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "ab1234563"}, timeout=10)
        # `max_length=9` is enforced at schema layer; lower-case still
        # fits. After normalisation the stored value is upper-case.
        assert r.status_code == 200, r.text
        assert r.json()["dea_number"] == "AB1234563"

    def test_rejects_checksum_failure(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "AB1234560"}, timeout=10)
        assert r.status_code == 422, r.text
        assert "checksum" in r.text.lower()

    def test_rejects_invalid_registrant(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "IB1234563"}, timeout=10)
        assert r.status_code == 422

    def test_rejects_wrong_length(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "AB12345"}, timeout=10)
        assert r.status_code == 422

    def test_rejects_non_alphanumeric(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": "AB123-456"}, timeout=10)
        assert r.status_code == 422

    def test_empty_string_clears_value(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        assert s.patch(f"{API}/auth/me/profile",
                       json={"dea_number": "AB1234563"},
                       timeout=10).status_code == 200
        r = s.patch(f"{API}/auth/me/profile",
                    json={"dea_number": ""}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["dea_number"] is None

    def test_optional_expiry_accepted(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile", json={
            "dea_number": "AB1234563",
            "dea_expires_at": "2028-12-31",
        }, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["dea_number"] == "AB1234563"
        assert body["dea_expires_at"] == "2028-12-31"

    def test_bad_expiry_format_rejected(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        r = s.patch(f"{API}/auth/me/profile", json={
            "dea_number": "AB1234563",
            "dea_expires_at": "31/12/2028",
        }, timeout=10)
        assert r.status_code == 422
        assert "ISO" in r.text or "YYYY" in r.text


# ---------------------------------------------------------------------------
# Audit — DEA create/update/remove generate profile_updated rows with
# the dea_number / dea_expires_at field listed in metadata.
# ---------------------------------------------------------------------------
class TestDeaAudit:
    def test_dea_write_is_audited(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        assert s.patch(f"{API}/auth/me/profile",
                       json={"dea_number": "AB1234563",
                             "dea_expires_at": "2028-12-31"},
                       timeout=10).status_code == 200

        admin = _login(ADMIN["email"], ADMIN["password"])
        admin.post(f"{API}/auth/reauth",
                   json={"password": ADMIN["password"]}, timeout=10)
        tok = admin.cookies.get("reauth_token")
        if tok:
            admin.headers["x-reauth-token"] = tok
        r = admin.get(f"{API}/audit-logs", params={
            "action": "user.profile_updated",
            "actor_email": doctor["email"],
            "limit": 10,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        fields_union = set()
        for row in rows:
            fields_union.update((row.get("metadata") or {}).get("fields", []))
        assert "dea_number" in fields_union, rows
        assert "dea_expires_at" in fields_union, rows

    def test_dea_clear_is_audited(self, doctor):
        s = _login(doctor["email"], doctor["password"])
        assert s.patch(f"{API}/auth/me/profile",
                       json={"dea_number": "AB1234563"},
                       timeout=10).status_code == 200
        assert s.patch(f"{API}/auth/me/profile",
                       json={"dea_number": ""},
                       timeout=10).status_code == 200

        admin = _login(ADMIN["email"], ADMIN["password"])
        admin.post(f"{API}/auth/reauth",
                   json={"password": ADMIN["password"]}, timeout=10)
        tok = admin.cookies.get("reauth_token")
        if tok:
            admin.headers["x-reauth-token"] = tok
        r = admin.get(f"{API}/audit-logs", params={
            "action": "user.profile_updated",
            "actor_email": doctor["email"],
            "limit": 20,
        }, timeout=10)
        if r.status_code == 404:
            pytest.skip("audit-logs endpoint not exposed")
        rows = r.json() if isinstance(r.json(), list) else r.json().get("items") or []
        # At least one row should reflect the clear (dea_number in fields).
        write_rows = [
            row for row in rows
            if "dea_number" in (row.get("metadata") or {}).get("fields", [])
        ]
        assert len(write_rows) >= 2, rows
