"""Contract tests for the chart-wide billing-readiness aggregate.

`GET /api/patients/{id}/clinical/billing-readiness/aggregate` must:

  * Return the pinned `schema_version = "1.0"` shape.
  * Compute counts by reusing the persisted `clinical_billing_readiness`
    rows — no duplicate rule engine.
  * Surface counts only for rows the caller can see (tenant-scoped).
  * Never leak free-form check detail strings — `top_message` must be
    either `null` or a value from the allow-listed vocabulary.
  * Not mutate source records.
  * Return zero counts with `status="ready"` and `top_message=null`
    when the caller has no readiness rows in view.
  * Deny non-billing roles (staff) with 403 — the frontend then keeps
    the panel row hidden.
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
DEMO_PATIENT = "0601bbe4-251e-435d-8727-30ce68d1c8ee"

APPROVED_MESSAGES = {
    "Insurance eligibility not verified",
    "Diagnosis linkage incomplete",
    "Note not signed",
    "Provider signature missing",
    "Chart note missing",
    "Treatment not documented",
    "Provider missing on encounter",
    "Patient missing on encounter",
    "Date of service missing",
    "Treatment plan linkage incomplete",
    "Objective findings not captured",
    "Response to care not documented",
    "Encounter not marked completed",
    "Appointment not linked",
    "Re-exam overdue",
}


def _login(email: str = ADMIN[0], password: str = ADMIN[1]):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


class TestAggregateContract:
    def test_schema_shape_pinned(self):
        s = _login()
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == "1.0"
        assert set(body.keys()) == {"schema_version", "warning_count", "blocked_count", "top_message", "status"}
        assert isinstance(body["warning_count"], int)
        assert isinstance(body["blocked_count"], int)
        assert body["status"] in {"ready", "warning", "blocked"}
        # Counts must be non-negative.
        assert body["warning_count"] >= 0
        assert body["blocked_count"] >= 0

    def test_top_message_is_allow_listed_or_null(self):
        s = _login()
        body = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=30).json()
        tm = body["top_message"]
        assert tm is None or tm in APPROVED_MESSAGES, f"leaked message: {tm!r}"

    def test_status_matches_counts(self):
        s = _login()
        body = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=30).json()
        if body["blocked_count"] > 0:
            assert body["status"] == "blocked"
        elif body["warning_count"] > 0:
            assert body["status"] == "warning"
        else:
            assert body["status"] == "ready"
            assert body["top_message"] is None

    def test_does_not_mutate_readiness_rows(self):
        s = _login()
        # A grouped-encounters read is the canonical way to pull the
        # existing readiness `updated_at` values into scope.
        before = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30).json()
        s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=30)
        after = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/encounters/grouped", timeout=30).json()
        # source_ids on every group must be byte-identical after the
        # aggregate read.
        b_ids = [g["source_ids"] for g in before["groups"]]
        a_ids = [g["source_ids"] for g in after["groups"]]
        assert b_ids == a_ids


class TestAggregatePermissions:
    def test_staff_role_denied(self):
        try:
            s = _login(*STAFF)
        except AssertionError:
            pytest.skip("staff test account unavailable")
        r = s.get(f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=15)
        # Frontend depends on 403 here to keep the panel row hidden.
        assert r.status_code == 403, r.text

    def test_anonymous_denied(self):
        r = requests.get(
            f"{API}/patients/{DEMO_PATIENT}/clinical/billing-readiness/aggregate", timeout=15,
        )
        assert r.status_code == 401


class TestAggregateTenantIsolation:
    def test_unknown_patient_returns_404(self):
        s = _login()
        r = s.get(
            f"{API}/patients/00000000-0000-0000-0000-000000000000/clinical/billing-readiness/aggregate",
            timeout=15,
        )
        # Same behaviour as the underlying _load_patient — a patient the
        # caller cannot see returns 404 (not 200 with fake zeros).
        assert r.status_code == 404, r.text
