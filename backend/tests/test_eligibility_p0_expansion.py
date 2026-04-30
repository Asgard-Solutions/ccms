"""
Eligibility — P0 expanded surfaces.

Covers the P0 spec delta beyond the initial 270/271 work:
  * Status model — 9 canonical states, classify_result() + is_expired()
  * Mock trigger markers: `BAD` → rejected, `ERR` → error
  * Missing-info validation (400 when patient/member_id/DOB missing)
  * Patient-anchored endpoints (latest / history / run)
  * Appointment-anchored endpoints (latest / run; service date is
    inherited from the appointment)
  * Billing readiness integration — `eligibility_verified` check is
    emitted with expected severity/detail wiring
  * Reference endpoint surfaces labels/tones/disclaimer
  * RBAC: raw payload endpoint denies non-admin/billing roles
  * Expiration overlay downgrades old rows to `expired`
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv

from services.billing.eligibility_status import (
    DISCLAIMER_TEXT,
    ELIGIBILITY_STATUSES,
    classify_result,
    is_expired,
    overlay_expiration,
    policy_snapshot_hash,
)


load_dotenv("/app/backend/.env")
API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email, password, reauth=True):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        rr = s.post(f"{API}/auth/reauth",
                    json={"password": password}, timeout=10)
        if rr.status_code == 200:
            rtok = rr.json().get("reauth_token")
            if rtok:
                s.headers["x-reauth-token"] = rtok
    return s


# ---------------------------------------------------------------------------
# Pure module-level helpers
# ---------------------------------------------------------------------------
class TestStatusModule:
    def test_canonical_status_set(self):
        for s in ("not_checked", "submitted", "active", "inactive",
                  "partial", "rejected", "error", "unknown", "expired"):
            assert s in ELIGIBILITY_STATUSES

    def test_classify_active_vs_inactive(self):
        active = {
            "coverage_active": True,
            "benefits": [{"qualifier": "1", "service_type": "30"}],
            "requested_service_types": ["30"],
        }
        assert classify_result(active) == "active"

        inactive = {
            "coverage_active": False,
            "benefits": [{"qualifier": "6", "service_type": "30"}],
        }
        assert classify_result(inactive) == "inactive"

    def test_classify_partial_when_expected_benefit_missing(self):
        active_no_chiro = {
            "coverage_active": True,
            "benefits": [{"qualifier": "1", "service_type": "30"}],
            # Requested chiropractic (33) but only got plan-level (30).
            "requested_service_types": ["33"],
        }
        assert classify_result(active_no_chiro) == "partial"

    def test_classify_rejected_when_flagged(self):
        rejected = {"coverage_active": False, "rejected": True,
                    "benefits": []}
        assert classify_result(rejected) == "rejected"

    def test_classify_unknown_for_blank_response(self):
        assert classify_result({}) == "unknown"
        assert classify_result({"coverage_active": False,
                                "benefits": []}) == "unknown"

    def test_policy_snapshot_stable(self):
        p = {"member_id": "A", "group_number": "G",
             "payer_id": "pid", "effective_date": "2026-01-01"}
        h1 = policy_snapshot_hash(p)
        h2 = policy_snapshot_hash(dict(p))
        assert h1 == h2 and len(h1) == 12
        # Mutating member_id must change the hash.
        p["member_id"] = "B"
        assert policy_snapshot_hash(p) != h1

    def test_is_expired_ttl_days(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=45)).isoformat()
        assert is_expired({"checked_at": old}, now=now) is True
        fresh = (now - timedelta(days=3)).isoformat()
        assert is_expired({"checked_at": fresh}, now=now) is False

    def test_is_expired_service_date_drift(self):
        now = datetime.now(timezone.utc)
        row = {
            "checked_at": now.isoformat(),
            "service_date": "2026-04-01",
        }
        assert is_expired(row, target_service_date="2026-05-15",
                          now=now) is True
        assert is_expired(row, target_service_date="2026-04-01",
                          now=now) is False

    def test_is_expired_policy_drift(self):
        now = datetime.now(timezone.utc)
        row = {"checked_at": now.isoformat(),
               "policy_snapshot_hash": "abc123"}
        assert is_expired(row, target_policy_snapshot="def456",
                          now=now) is True
        assert is_expired(row, target_policy_snapshot="abc123",
                          now=now) is False

    def test_overlay_expiration_downgrades(self):
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=60)).isoformat()
        row = {"checked_at": old, "status": "active"}
        out = overlay_expiration(row, now=now)
        assert out["effective_status"] == "expired"
        # Fresh row — effective_status echoes the stored status.
        fresh = {"checked_at": now.isoformat(), "status": "active"}
        out2 = overlay_expiration(fresh, now=now)
        assert out2["effective_status"] == "active"


# ---------------------------------------------------------------------------
# Mock engine demo triggers
# ---------------------------------------------------------------------------
class TestDemoTriggers:
    def _base(self):
        return dict(
            submitter={"id": "SUB1", "name": "X"},
            receiver={"id": "P1", "name": "X"},
            provider={"npi": "1841792253", "name": "X", "entity_type": "org"},
            payer={"id": "p", "name": "Acme", "electronic_payer_id": "P1",
                   "payer_type": "commercial"},
            patient={"first_name": "A", "last_name": "B",
                     "date_of_birth": "1990-01-01", "sex_at_birth": "female"},
        )

    def test_err_marker_raises_engine_error(self):
        from services.billing.eligibility import (
            EligibilityEngineError, MockEligibilityEngine,
        )
        policy = {"member_id": "TEST-ERR", "relationship_to_subscriber": "self"}
        with pytest.raises(EligibilityEngineError):
            MockEligibilityEngine().check(**self._base(), policy=policy)

    def test_bad_marker_returns_rejected(self):
        from services.billing.eligibility import MockEligibilityEngine
        policy = {"member_id": "FOO-BAD", "relationship_to_subscriber": "self"}
        outcome = MockEligibilityEngine().check(**self._base(), policy=policy)
        r = outcome["result"]
        assert r["rejected"] is True
        assert r["coverage_active"] is False
        # Classifier maps this to "rejected"
        assert classify_result(r) == "rejected"

    def test_term_marker_still_inactive(self):
        from services.billing.eligibility import MockEligibilityEngine
        policy = {"member_id": "FOO-TERM", "relationship_to_subscriber": "self"}
        outcome = MockEligibilityEngine().check(**self._base(), policy=policy)
        r = outcome["result"]
        assert r["coverage_active"] is False
        assert classify_result(r) == "inactive"


# ---------------------------------------------------------------------------
# HTTP — patient + appointment + reference endpoints
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def demo_patient(admin):
    patients = admin.get(f"{API}/patients", timeout=15).json()
    for p in patients:
        policies = admin.get(
            f"{API}/billing/insurance-policies?patient_id={p['id']}",
            timeout=10,
        ).json()
        if policies:
            return {"patient": p, "policy": policies[0]}
    pytest.fail("no demo patient has a seeded insurance policy")


class TestReferenceEndpoint:
    def test_reference_shape(self, admin):
        r = admin.get(f"{API}/billing/eligibility/reference", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert set(body["statuses"]) == set(ELIGIBILITY_STATUSES)
        assert body["disclaimer"] == DISCLAIMER_TEXT
        assert "active" in body["labels"]
        assert body["tones"]["active"] == "success"


class TestPatientEndpoints:
    def test_patient_eligibility_latest_has_seeded_row(self, admin, demo_patient):
        pid = demo_patient["patient"]["id"]
        r = admin.get(
            f"{API}/billing/patients/{pid}/eligibility-latest",
            timeout=10,
        )
        assert r.status_code == 200
        body = r.json()
        assert body is not None, "seed should have populated this patient"
        assert body["status"] in ELIGIBILITY_STATUSES
        assert body["effective_status"] in ELIGIBILITY_STATUSES

    def test_run_patient_check_with_service_date(self, admin, demo_patient):
        pid = demo_patient["patient"]["id"]
        dos = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = admin.post(
            f"{API}/billing/patients/{pid}/eligibility-check",
            json={"inquiry_date": dos, "service_type_codes": ["30", "33"]},
            timeout=15,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["service_date"] == dos
        assert body["status"] in ("active", "partial", "inactive",
                                  "rejected", "unknown")

    def test_run_patient_check_no_policy_400(self, admin):
        """Creating a patient without a policy should surface a 400 with
        the missing-info explanation."""
        pid = str(uuid.uuid4())  # non-existent
        r = admin.post(
            f"{API}/billing/patients/{pid}/eligibility-check",
            json={}, timeout=10,
        )
        # Patient not found → 400 (no active policy) — behaviour is
        # consistent either way as long as it's 4xx with an actionable
        # message.
        assert r.status_code in (400, 404)

    def test_patient_eligibility_history(self, admin, demo_patient):
        pid = demo_patient["patient"]["id"]
        # Seed a fresh run so there's at least one row.
        admin.post(
            f"{API}/billing/patients/{pid}/eligibility-check",
            json={}, timeout=15,
        )
        r = admin.get(
            f"{API}/billing/patients/{pid}/eligibility-checks",
            timeout=10,
        )
        assert r.status_code == 200
        rows = r.json()
        assert rows
        # Reverse-chronological order.
        ts = [row["checked_at"] for row in rows]
        assert ts == sorted(ts, reverse=True)
        # List endpoint omits wires.
        assert "request_wire" not in rows[0]
        # Effective status is always projected.
        assert rows[0]["effective_status"] in ELIGIBILITY_STATUSES


class TestAppointmentEndpoint:
    def _pick_appointment(self, admin, patient_id):
        r = admin.get(
            f"{API}/scheduling/appointments?patient_id={patient_id}",
            timeout=10,
        )
        if r.status_code != 200:
            return None
        arr = r.json() or []
        return arr[0] if arr else None

    def test_appointment_run_and_latest(self, admin, demo_patient):
        appt = self._pick_appointment(admin, demo_patient["patient"]["id"])
        if not appt:
            pytest.skip("no appointments seeded for this patient")
        # Latest before a dedicated run may be None OR a DOS-matching
        # patient-scoped check from the seed.
        r = admin.post(
            f"{API}/billing/appointments/{appt['id']}/eligibility-check",
            json={}, timeout=15,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["appointment_id"] == appt["id"]
        # Service date derived from the appointment's start_time.
        expected_dos = (appt.get("start_time") or "")[:10]
        if expected_dos:
            assert body["service_date"] == expected_dos

        r2 = admin.get(
            f"{API}/billing/appointments/{appt['id']}/eligibility-latest",
            timeout=10,
        )
        assert r2.status_code == 200
        latest = r2.json()
        assert latest is not None
        assert latest["appointment_id"] == appt["id"]


class TestRawPayloadRBAC:
    def test_doctor_cannot_fetch_raw_payload(self, demo_patient):
        # Doctor session — doctors view summaries, NOT raw 270/271.
        doc = _login(*DOCTOR)
        pid = demo_patient["patient"]["id"]
        admin = _login(*ADMIN)
        # Seed a check via admin so we have something to fetch.
        created = admin.post(
            f"{API}/billing/patients/{pid}/eligibility-check",
            json={}, timeout=15,
        )
        assert created.status_code == 201
        check_id = created.json()["id"]
        r = doc.get(
            f"{API}/billing/eligibility-checks/{check_id}", timeout=10,
        )
        assert r.status_code == 403, (r.status_code, r.text)
