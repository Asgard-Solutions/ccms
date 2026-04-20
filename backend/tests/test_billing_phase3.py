"""
Billing Phase 3 — claim draft builder + scrubber.

Covers:
  * Scrubber unit tests on pure rules (no DB)
  * from-invoice builder happy path + guard rails
  * validate endpoint: auto-transitions draft → ready when clean,
    draft → validation_failed when errors exist
  * Replace header / diagnoses / lines only on editable statuses
  * Tenant isolation
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

from services.billing.scrubber import (
    DEFAULT_RULES, ScrubberContext,
    rule_has_patient, rule_has_payer, rule_has_active_policy,
    rule_diagnoses_present, rule_line_diagnosis_pointers,
    rule_required_header_fields, rule_line_units_and_billed,
    rule_billed_total_matches_header,
    run_rules,
)

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    rr = s.post(f"{API}/auth/reauth",
                json={"password": password}, timeout=10)
    if rr.status_code == 200:
        tok = rr.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _first_patient(s):
    r = s.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    return r.json()[0]


def _ensure_self_pay(s):
    existing = s.get(f"{API}/billing/fee-schedules", timeout=10).json()
    for sch in existing:
        if sch["kind"] == "self_pay" and sch["active"]:
            return sch["id"]
    r = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Self-Pay"), "kind": "self_pay",
    }, timeout=10)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _make_payer(s):
    r = s.post(f"{API}/billing/payers", json={
        "name": _unique("P3"), "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=10)
    return r.json()


def _prime_insurance_invoice(s):
    """Build a captured insurance invoice end-to-end for from-invoice tests."""
    patient = _first_patient(s)
    sid = _ensure_self_pay(s)
    s.put(f"{API}/billing/fee-schedules/{sid}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
    ], timeout=10)
    payer = _make_payer(s)
    # payer schedule so pricing matches insurance
    r = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Payer"), "kind": "payer", "payer_id": payer["id"],
    }, timeout=10)
    payer_sid = r.json()["id"]
    s.put(f"{API}/billing/fee-schedules/{payer_sid}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
    ], timeout=10)
    s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "Sub Scriber",
        "member_id": "M-" + uuid.uuid4().hex[:8],
    }, timeout=10)
    rec = s.post(f"{API}/patients/{patient['id']}/records", json={
        "record_type": "treatment", "title": "P3",
        "description": "x", "diagnosis": "low back pain",
        "treatment": "CMT",
    }, timeout=10).json()
    s.put(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
        "procedures": [{"code_type": "cpt", "code": "98940",
                        "units": 1, "modifiers": []}],
        "diagnoses": [{"sequence": 1, "code": "M54.16"}],
        "responsibility": "insurance",
    }, timeout=10)
    s.post(f"{API}/patients/{patient['id']}/records/{rec['id']}/sign",
           timeout=10)
    inv = s.post(f"{API}/billing/encounters/{rec['id']}/capture",
                 timeout=10).json()
    return patient, payer, rec, inv


# ---------------------------------------------------------------------------
# Scrubber rule unit tests (pure; no DB)
# ---------------------------------------------------------------------------
def _ctx(claim=None, dx=None, lines=None, mods=None,
         patient=None, payer=None, policy=None):
    return ScrubberContext(
        claim=claim or {}, diagnoses=dx or [], lines=lines or [],
        line_modifiers_by_line=mods or {},
        patient=patient, payer=payer, policy=policy,
    )


class TestScrubberRules:
    def test_missing_patient_payer_policy(self):
        c = _ctx(claim={"id": "c1"})
        assert rule_has_patient(c)[0].code == "PATIENT_MISSING"
        assert rule_has_payer(c)[0].code == "PAYER_MISSING"
        assert rule_has_active_policy(c)[0].code == "POLICY_MISSING"

    def test_policy_mismatch(self):
        c = _ctx(
            payer={"id": "payer-a"},
            policy={"payer_id": "payer-b", "status": "active",
                    "member_id": "M1"},
        )
        codes = {f.code for f in rule_has_active_policy(c)}
        assert "POLICY_PAYER_MISMATCH" in codes

    def test_required_header_fields(self):
        c = _ctx(claim={})
        codes = {f.code for f in rule_required_header_fields(c)}
        assert "BILLING_PROVIDER_MISSING" in codes
        assert "PLACE_OF_SERVICE_MISSING" in codes

    def test_diagnoses_present(self):
        assert rule_diagnoses_present(_ctx(dx=[]))[0].code == "DIAGNOSES_MISSING"
        dup = rule_diagnoses_present(_ctx(dx=[
            {"sequence": 1, "code": "M54.16"},
            {"sequence": 1, "code": "M54.5"},
        ]))
        assert dup[0].code == "DIAGNOSIS_SEQUENCE_DUPLICATE"

    def test_line_diagnosis_pointers(self):
        dx = [{"sequence": 1, "code": "M54.16"}]
        lines = [{"id": "L1", "sequence": 1, "diagnosis_pointers": []},
                 {"id": "L2", "sequence": 2, "diagnosis_pointers": [9]}]
        codes = {f.code for f in rule_line_diagnosis_pointers(_ctx(dx=dx, lines=lines))}
        assert "LINE_DX_POINTER_MISSING" in codes
        assert "LINE_DX_POINTER_INVALID" in codes

    def test_line_units_and_billed(self):
        lines = [{"id": "L1", "sequence": 1, "units": 0,
                  "billed_cents": 0, "code": ""}]
        codes = {f.code for f in rule_line_units_and_billed(_ctx(lines=lines))}
        assert "LINE_UNITS_NONPOSITIVE" in codes
        assert "LINE_BILLED_ZERO" in codes
        assert "LINE_CODE_MISSING" in codes

    def test_billed_total_mismatch_is_warning(self):
        lines = [{"units": 1, "billed_cents": 100}]
        c = _ctx(claim={"billed_cents": 999}, lines=lines)
        finds = rule_billed_total_matches_header(c)
        assert finds and finds[0].severity == "warning"

    def test_run_rules_clean_claim(self):
        # A synthetic claim that should satisfy every rule.
        dx = [{"sequence": 1, "code": "M54.16"}]
        lines = [{"id": "L1", "sequence": 1,
                  "units": 1, "billed_cents": 5500,
                  "code": "98940", "diagnosis_pointers": [1]}]
        claim = {
            "id": "c1", "billed_cents": 5500,
            "billing_provider_id": "p1", "rendering_provider_id": "r1",
            "place_of_service": "11",
            "service_date_from": "2026-02-01",
            "service_date_to": "2026-02-01",
        }
        res = run_rules(_ctx(
            claim=claim, dx=dx, lines=lines,
            patient={"id": "pt"},
            payer={"id": "pa"},
            policy={"payer_id": "pa", "status": "active", "member_id": "M"},
        ))
        assert res["passed"] is True, res["errors"]


# ---------------------------------------------------------------------------
# Integration — from-invoice builder
# ---------------------------------------------------------------------------
class TestFromInvoiceBuilder:
    def test_drafts_claim_from_insurance_invoice(self):
        s = _login(*DEFAULT_ADMIN)
        _patient, _payer, _rec, inv = _prime_insurance_invoice(s)
        r = s.post(
            f"{API}/billing/claims/from-invoice/{inv['id']}", timeout=10,
        )
        assert r.status_code == 201, r.text
        claim = r.json()
        assert claim["status"] == "draft"
        assert claim["source_invoice_id"] == inv["id"]
        assert claim["billed_cents"] == inv["total_cents"]
        assert claim["place_of_service"] == "11"

    def test_rejects_self_pay_invoice(self):
        """Invoices with self_pay responsibility can't become insurance claims."""
        s = _login(*DEFAULT_ADMIN)
        patient = _first_patient(s)
        sid = _ensure_self_pay(s)
        s.put(f"{API}/billing/fee-schedules/{sid}/lines", json=[
            {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
        ], timeout=10)
        rec = s.post(f"{API}/patients/{patient['id']}/records", json={
            "record_type": "treatment", "title": "self pay",
            "description": "x",
        }, timeout=10).json()
        s.put(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
            "procedures": [{"code_type": "cpt", "code": "98940",
                            "units": 1, "modifiers": []}],
            "diagnoses": [{"sequence": 1, "code": "M54.16"}],
            "responsibility": "self_pay",
        }, timeout=10)
        s.post(f"{API}/patients/{patient['id']}/records/{rec['id']}/sign",
               timeout=10)
        inv = s.post(f"{API}/billing/encounters/{rec['id']}/capture",
                     timeout=10).json()
        r = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                   timeout=10)
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Integration — validate endpoint
# ---------------------------------------------------------------------------
class TestValidate:
    def test_validate_finds_missing_fields_and_blocks_ready(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, _rec, inv = _prime_insurance_invoice(s)
        claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                       timeout=10).json()
        # Default place_of_service=11 is set; billing_provider is None →
        # scrubber should flag BILLING_PROVIDER_MISSING.
        r = s.post(f"{API}/billing/claims/{claim['id']}/validate",
                   timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["passed"] is False
        assert body["status"] == "validation_failed"
        assert any(e["code"] == "BILLING_PROVIDER_MISSING" for e in body["errors"])

    def test_validate_clean_claim_advances_to_ready(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, rec, inv = _prime_insurance_invoice(s)
        claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                       timeout=10).json()
        # Fill the required header fields.
        r = s.put(f"{API}/billing/claims/{claim['id']}/header", json={
            "billing_provider_id": rec.get("recorded_by") or "provider-1",
            "rendering_provider_id": rec.get("recorded_by") or "provider-1",
            "place_of_service": "11",
        }, timeout=10)
        assert r.status_code == 200, r.text
        # Validate
        r = s.post(f"{API}/billing/claims/{claim['id']}/validate",
                   timeout=10).json()
        assert r["passed"] is True, r["errors"]
        assert r["status"] == "ready"

    def test_validation_run_history_persisted(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, _rec, inv = _prime_insurance_invoice(s)
        claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                       timeout=10).json()
        s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
        s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
        runs = s.get(f"{API}/billing/claims/{claim['id']}/validations",
                     timeout=10).json()
        assert len(runs) >= 2
        # Latest first
        assert runs[0]["run_at"] >= runs[1]["run_at"]


# ---------------------------------------------------------------------------
# Integration — edit endpoints
# ---------------------------------------------------------------------------
class TestEditEndpoints:
    def test_replace_diagnoses_and_lines(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, rec, inv = _prime_insurance_invoice(s)
        claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                       timeout=10).json()
        r = s.put(f"{API}/billing/claims/{claim['id']}/diagnoses", json=[
            {"sequence": 1, "code": "M54.16"},
            {"sequence": 2, "code": "M25.50"},
        ], timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 2
        r = s.put(f"{API}/billing/claims/{claim['id']}/lines", json=[
            {"sequence": 1, "service_date": "2026-02-01",
             "code_type": "cpt", "code": "98941",
             "units": 1, "billed_cents": 7500,
             "diagnosis_pointers": [1, 2], "modifiers": ["25"]},
        ], timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1
        assert r.json()["billed_cents"] == 7500

    def test_cannot_edit_after_submitted(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, rec, inv = _prime_insurance_invoice(s)
        claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                       timeout=10).json()
        # force it to ready via validate after filling header
        s.put(f"{API}/billing/claims/{claim['id']}/header", json={
            "billing_provider_id": rec.get("recorded_by") or "p1",
            "rendering_provider_id": rec.get("recorded_by") or "p1",
            "place_of_service": "11",
        }, timeout=10)
        s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
        s.post(f"{API}/billing/claims/{claim['id']}/submit", timeout=10)
        r = s.put(f"{API}/billing/claims/{claim['id']}/header",
                  json={"notes": "too late"}, timeout=10)
        assert r.status_code == 409, r.text


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TestPhase3TenantIsolation:
    def test_sunrise_cannot_validate_default_claim(self):
        default_admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        _p, _py, _rec, inv = _prime_insurance_invoice(default_admin)
        claim = default_admin.post(
            f"{API}/billing/claims/from-invoice/{inv['id']}", timeout=10,
        ).json()
        r = sunrise.post(
            f"{API}/billing/claims/{claim['id']}/validate", timeout=10,
        )
        assert r.status_code == 404, r.text
