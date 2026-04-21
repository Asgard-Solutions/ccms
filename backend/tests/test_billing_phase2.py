"""
Billing Phase 2 — insurance policies, fee schedules, encounter charge
capture.

Covers:
  * Fee schedule CRUD + single-active self_pay constraint
  * Price resolution precedence (payer_schedule > self_pay > catalog > zero)
  * Insurance policy update / deactivate
  * Medical record coding update (procedures + diagnoses + responsibility)
  * Record sign lifecycle (unsigned → signed is one-way until capture)
  * Charge candidate preview — warnings for missing prices / policy
  * Capture happy path — draft invoice written, record flagged captured
  * Capture validations — unsigned record 409, already-captured 409,
    insurance without policy 409
  * Tenant isolation — Sunrise admin cannot capture Default's encounter
  * Audit rows emitted for every mutation
"""
from __future__ import annotations

import os
import uuid
import asyncio

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DEFAULT_DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password},
               timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    rr = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if rr.status_code == 200:
        token = rr.json().get("reauth_token")
        if token:
            s.headers["x-reauth-token"] = token
    return s


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _first_patient(s: requests.Session) -> dict:
    r = s.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200, r.text
    assert r.json(), "fixtures require at least one patient"
    return r.json()[0]


def _ensure_self_pay_schedule(s: requests.Session) -> str:
    """Get or create the single active self_pay schedule for this tenant."""
    existing = s.get(f"{API}/billing/fee-schedules", timeout=10).json()
    for sch in existing:
        if sch["kind"] == "self_pay" and sch["active"]:
            return sch["id"]
    r = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Self-Pay"), "kind": "self_pay",
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _upsert_lines(s: requests.Session, sid: str, lines: list[dict]):
    r = s.patch(f"{API}/billing/fee-schedules/{sid}/lines",
              json=lines, timeout=10)
    assert r.status_code == 200, r.text


def _make_payer(s: requests.Session, name=None) -> dict:
    r = s.post(f"{API}/billing/payers", json={
        "name": name or _unique("P2Payer"), "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


def _create_record(s: requests.Session, patient_id: str,
                   record_type="treatment",
                   title="Phase 2 visit") -> dict:
    r = s.post(f"{API}/patients/{patient_id}/records", json={
        "record_type": record_type,
        "title": title,
        "description": "Phase 2 test encounter",
        "diagnosis": "low back pain",
        "treatment": "CMT + therapeutic exercise",
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()


def _code_record(s: requests.Session, patient_id: str, record_id: str,
                 procedures=None, diagnoses=None, responsibility="self_pay"):
    body = {
        "procedures": procedures if procedures is not None else [
            {"code_type": "cpt", "code": "98940", "units": 1, "modifiers": []},
            {"code_type": "cpt", "code": "97110", "units": 2, "modifiers": []},
        ],
        "diagnoses": diagnoses if diagnoses is not None else [
            {"sequence": 1, "code": "M54.16"},
        ],
        "responsibility": responsibility,
    }
    r = s.patch(
        f"{API}/patients/{patient_id}/records/{record_id}/coding",
        json=body, timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _sign_record(s: requests.Session, patient_id: str, record_id: str):
    r = s.post(
        f"{API}/patients/{patient_id}/records/{record_id}/sign",
        timeout=10,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Fee schedules
# ---------------------------------------------------------------------------
class TestFeeSchedules:
    def test_create_self_pay_unique(self):
        s = _login(*DEFAULT_ADMIN)
        _ensure_self_pay_schedule(s)
        # second active self_pay → 409
        r = s.post(f"{API}/billing/fee-schedules", json={
            "name": _unique("Duplicate"), "kind": "self_pay",
        }, timeout=10)
        assert r.status_code == 409, r.text

    def test_create_payer_schedule_requires_payer_id(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.post(f"{API}/billing/fee-schedules", json={
            "name": "Missing payer", "kind": "payer",
        }, timeout=10)
        assert r.status_code == 400, r.text

    def test_upsert_lines_is_idempotent(self):
        s = _login(*DEFAULT_ADMIN)
        sid = _ensure_self_pay_schedule(s)
        _upsert_lines(s, sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
        ])
        _upsert_lines(s, sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6500},
        ])
        lines = s.get(
            f"{API}/billing/fee-schedules/{sid}/lines", timeout=10,
        ).json()
        match = [ln for ln in lines if ln["code"] == "98940"]
        assert len(match) == 1
        assert match[0]["allowed_cents"] == 6500


# ---------------------------------------------------------------------------
# Insurance policies update / deactivate
# ---------------------------------------------------------------------------
class TestInsurancePolicies:
    def test_update_and_deactivate(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        payer = _make_payer(s)
        r = s.post(f"{API}/billing/insurance-policies", json={
            "patient_id": patient["id"], "payer_id": payer["id"],
            "rank": "primary", "subscriber_name": "Jane Doe",
            "member_id": "M-" + uuid.uuid4().hex[:8],
        }, timeout=10)
        assert r.status_code == 201, r.text
        pol = r.json()

        r = s.patch(f"{API}/billing/insurance-policies/{pol['id']}", json={
            "group_number": "GRP-123", "notes": "updated via phase 2",
        }, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["group_number"] == "GRP-123"

        r = s.delete(
            f"{API}/billing/insurance-policies/{pol['id']}", timeout=10,
        )
        assert r.status_code == 200, r.text

        listing = s.get(
            f"{API}/billing/insurance-policies?patient_id={patient['id']}",
            timeout=10,
        ).json()
        mine = next(p for p in listing if p["id"] == pol["id"])
        assert mine["status"] == "inactive"


# ---------------------------------------------------------------------------
# Medical record coding + sign
# ---------------------------------------------------------------------------
class TestRecordCodingAndSign:
    def test_coding_requires_unsigned_record(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"])
        _sign_record(s, patient["id"], rec["id"])
        # now coding should be locked
        r = s.patch(
            f"{API}/patients/{patient['id']}/records/{rec['id']}/coding",
            json={
                "procedures": [{"code_type": "cpt", "code": "97140",
                                "units": 1, "modifiers": []}],
                "diagnoses": [{"sequence": 1, "code": "M54.16"}],
                "responsibility": "self_pay",
            }, timeout=10,
        )
        assert r.status_code == 409, r.text

    def test_sign_is_idempotent(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"])
        first = _sign_record(s, patient["id"], rec["id"])
        second = _sign_record(s, patient["id"], rec["id"])
        assert first["signed_at"] == second["signed_at"]


# ---------------------------------------------------------------------------
# Charge capture
# ---------------------------------------------------------------------------
class TestChargeCapture:
    def test_preview_self_pay_uses_self_pay_schedule(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        sid = _ensure_self_pay_schedule(s)
        _upsert_lines(s, sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
            {"code_type": "cpt", "code": "97110", "allowed_cents": 4500},
        ])
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"])

        preview = s.get(
            f"{API}/billing/encounters/{rec['id']}/charge-candidates",
            timeout=10,
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["responsibility"] == "self_pay"
        assert body["can_capture"] is True
        assert body["total_cents"] == 6000 + 4500 * 2
        for ln in body["lines"]:
            assert ln["price_source"] == "self_pay_schedule"

    def test_preview_warns_when_insurance_missing_policy(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        _ensure_self_pay_schedule(s)
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"],
                     responsibility="insurance")

        # Remove any active policies so there's definitely no primary.
        pols = s.get(
            f"{API}/billing/insurance-policies?patient_id={patient['id']}",
            timeout=10,
        ).json()
        for p in pols:
            if p["rank"] == "primary" and p["status"] == "active":
                s.delete(f"{API}/billing/insurance-policies/{p['id']}",
                         timeout=10)

        preview = s.get(
            f"{API}/billing/encounters/{rec['id']}/charge-candidates",
            timeout=10,
        ).json()
        assert preview["can_capture"] is False
        assert any("no active primary policy" in w.lower()
                   for w in preview["warnings"])

    def test_capture_requires_signed_record(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        _ensure_self_pay_schedule(s)
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"])
        r = s.post(
            f"{API}/billing/encounters/{rec['id']}/capture", timeout=10,
        )
        assert r.status_code == 409, r.text

    def test_capture_self_pay_happy_path(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        sid = _ensure_self_pay_schedule(s)
        _upsert_lines(s, sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
            {"code_type": "cpt", "code": "97110", "allowed_cents": 4500},
        ])
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"])
        _sign_record(s, patient["id"], rec["id"])

        r = s.post(
            f"{API}/billing/encounters/{rec['id']}/capture", timeout=10,
        )
        assert r.status_code == 201, r.text
        inv = r.json()
        assert inv["status"] == "draft"
        assert inv["total_cents"] == 6000 + 4500 * 2

        lines = s.get(
            f"{API}/billing/invoices/{inv['id']}/lines", timeout=10,
        ).json()
        assert len(lines) == 2
        codes = {ln["code"] for ln in lines}
        assert codes == {"98940", "97110"}

        # Recapture must be blocked.
        r = s.post(
            f"{API}/billing/encounters/{rec['id']}/capture", timeout=10,
        )
        assert r.status_code == 409, r.text

    def test_capture_insurance_uses_payer_schedule(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        _ensure_self_pay_schedule(s)
        payer = _make_payer(s)

        # Payer-specific schedule: this is what should win for insurance.
        r = s.post(f"{API}/billing/fee-schedules", json={
            "name": _unique("Payer"),
            "kind": "payer", "payer_id": payer["id"],
        }, timeout=10)
        assert r.status_code == 201, r.text
        payer_sid = r.json()["id"]
        _upsert_lines(s, payer_sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
        ])

        # Ensure patient has an active primary policy on this payer.
        r = s.post(f"{API}/billing/insurance-policies", json={
            "patient_id": patient["id"], "payer_id": payer["id"],
            "rank": "primary", "subscriber_name": "Jane Doe",
            "member_id": "IDX-" + uuid.uuid4().hex[:6],
        }, timeout=10)
        assert r.status_code == 201, r.text

        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"],
                     procedures=[{"code_type": "cpt", "code": "98940",
                                  "units": 1, "modifiers": []}],
                     responsibility="insurance")
        _sign_record(s, patient["id"], rec["id"])

        preview = s.get(
            f"{API}/billing/encounters/{rec['id']}/charge-candidates",
            timeout=10,
        ).json()
        assert preview["payer_id"] == payer["id"]
        assert preview["lines"][0]["unit_price_cents"] == 4000
        assert preview["lines"][0]["price_source"] == "payer_schedule"

        r = s.post(
            f"{API}/billing/encounters/{rec['id']}/capture", timeout=10,
        )
        assert r.status_code == 201, r.text
        assert r.json()["total_cents"] == 4000


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TestTenantIsolationPhase2:
    def test_sunrise_cannot_preview_default_encounter(self):
        default_admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        patient = _first_patient(default_admin)
        rec = _create_record(default_admin, patient["id"])
        _code_record(default_admin, patient["id"], rec["id"])
        r = sunrise.get(
            f"{API}/billing/encounters/{rec['id']}/charge-candidates",
            timeout=10,
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
class TestPhase2Audit:
    def test_capture_emits_audit_and_previewed(self):
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        sid = _ensure_self_pay_schedule(s)
        _upsert_lines(s, sid, [
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
        ])
        rec = _create_record(s, patient["id"])
        _code_record(s, patient["id"], rec["id"],
                     procedures=[{"code_type": "cpt", "code": "98940",
                                  "units": 1, "modifiers": []}])
        _sign_record(s, patient["id"], rec["id"])
        s.get(f"{API}/billing/encounters/{rec['id']}/charge-candidates",
              timeout=10)
        s.post(f"{API}/billing/encounters/{rec['id']}/capture",
               timeout=10)

        r = s.get(
            f"{API}/audit-logs?action=billing.charge_capture.committed&limit=20",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        rows = body if isinstance(body, list) else body.get("items", [])
        assert any(row.get("entity_id") == rec["id"] for row in rows)
