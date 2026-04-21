"""
Billing Phase 5 — Remittance posting, denials, AR aging, statements.

Covers:
  * Remittance posting math (full pay, partial pay + contractual, denial)
  * Patient balance roll-forward (invoice.balance_cents updates correctly)
  * Denial work items auto-created on denied line/claim
  * Denial work item mutations (status + assignment + notes) audited
  * AR aging bucket calculations
  * Statement generation snapshot
  * Tenant isolation
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv

from services.billing.remittance import (
    AGING_BUCKETS,
    _bucket_for_days,
    _days_between,
    compute_ar_buckets,
    render_statement_body,
)

load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    rr = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if rr.status_code == 200:
        tok = rr.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _ensure_self_pay(s):
    existing = s.get(f"{API}/billing/fee-schedules", timeout=10).json()
    for sch in existing:
        if sch["kind"] == "self_pay" and sch["active"]:
            return sch["id"]
    r = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Self-Pay"), "kind": "self_pay",
    }, timeout=10)
    return r.json()["id"]


def _build_submitted_claim(s):
    """Build an end-to-end insurance claim and advance to `submitted`.

    Returns (patient, payer_used_by_claim, claim, invoice).
    """
    patients = s.get(f"{API}/patients", timeout=15).json()
    patient = patients[0]
    _ensure_self_pay(s)
    payer = s.post(f"{API}/billing/payers", json={
        "name": _unique("P5"), "payer_type": "commercial", "remit_method": "era",
    }, timeout=10).json()
    sched = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Payer5"), "kind": "payer", "payer_id": payer["id"],
    }, timeout=10).json()
    s.patch(f"{API}/billing/fee-schedules/{sched['id']}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
    ], timeout=10)
    s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "Sub Scriber",
        "member_id": "M-" + uuid.uuid4().hex[:8],
    }, timeout=10)
    rec = s.post(f"{API}/patients/{patient['id']}/records", json={
        "record_type": "treatment", "title": "P5", "description": "x",
        "diagnosis": "LBP", "treatment": "CMT",
    }, timeout=10).json()
    s.patch(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
        "procedures": [{"code_type": "cpt", "code": "98940", "units": 1, "modifiers": []}],
        "diagnoses": [{"sequence": 1, "code": "M54.16"}],
        "responsibility": "insurance",
    }, timeout=10)
    s.post(f"{API}/patients/{patient['id']}/records/{rec['id']}/sign", timeout=10)
    inv = s.post(f"{API}/billing/encounters/{rec['id']}/capture", timeout=10).json()
    claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}", timeout=10).json()
    s.patch(f"{API}/billing/claims/{claim['id']}/header", json={
        "billing_provider_id": "bp-1", "rendering_provider_id": "rp-1",
        "place_of_service": "11",
    }, timeout=10)
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
    s.post(f"{API}/billing/claims/{claim['id']}/submissions",
           json={"method": "manual_portal"}, timeout=10)
    fresh_claim = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                        timeout=10).json()["claim"]
    fresh_inv = s.get(f"{API}/billing/invoices/{inv['id']}", timeout=10).json()
    return patient, fresh_claim["payer_id"], fresh_claim, fresh_inv


# ---------------------------------------------------------------------------
# Unit — aging math
# ---------------------------------------------------------------------------
class TestAgingMath:
    def test_bucket_boundaries(self):
        assert _bucket_for_days(0) == "0-30"
        assert _bucket_for_days(30) == "0-30"
        assert _bucket_for_days(31) == "31-60"
        assert _bucket_for_days(60) == "31-60"
        assert _bucket_for_days(61) == "61-90"
        assert _bucket_for_days(91) == "91-120"
        assert _bucket_for_days(121) == "120+"
        assert _bucket_for_days(999) == "120+"

    def test_days_between_handles_z_suffix(self):
        a = "2026-01-01T00:00:00Z"
        b = "2026-01-31T00:00:00Z"
        assert _days_between(a, b) == 30

    def test_compute_ar_buckets_rolls_up_correctly(self):
        as_of = "2026-02-01T00:00:00+00:00"
        invoices = [
            {"status": "issued", "balance_cents": 10000,
             "issued_at": "2026-01-15T00:00:00+00:00"},  # 17 days -> 0-30
            {"status": "issued", "balance_cents": 5000,
             "issued_at": "2025-12-15T00:00:00+00:00"},  # 48 days -> 31-60
            {"status": "issued", "balance_cents": 3000,
             "issued_at": "2025-09-01T00:00:00+00:00"},  # 153 days -> 120+
            {"status": "void", "balance_cents": 99999,  # excluded
             "issued_at": "2025-09-01T00:00:00+00:00"},
            {"status": "issued", "balance_cents": 0,    # excluded
             "issued_at": "2026-01-15T00:00:00+00:00"},
        ]
        r = compute_ar_buckets(invoices, as_of_iso=as_of)
        assert r["total_balance_cents"] == 18000
        assert r["total_invoice_count"] == 3
        by = {b["bucket"]: b for b in r["buckets"]}
        assert by["0-30"]["balance_cents"] == 10000
        assert by["31-60"]["balance_cents"] == 5000
        assert by["120+"]["balance_cents"] == 3000
        assert by["61-90"]["balance_cents"] == 0

    def test_statement_body_deterministic(self):
        patient = {"id": "pt-abc123456", "first_name": "Jane", "last_name": "Doe"}
        invoices = [
            {"id": "inv-aaaaaaaa", "balance_cents": 4500,
             "issued_at": "2026-01-10T00:00:00+00:00"},
            {"id": "inv-bbbbbbbb", "balance_cents": 2000,
             "issued_at": "2026-01-15T00:00:00+00:00"},
        ]
        body = render_statement_body(
            patient=patient, invoices=invoices,
            as_of_iso="2026-02-01T00:00:00+00:00",
        )
        assert "PATIENT STATEMENT" in body
        assert "Jane Doe" in body
        assert "TOTAL DUE: $65.00" in body
        assert "balance $45.00" in body


# ---------------------------------------------------------------------------
# Integration — remittance posting math + balance roll-forward
# ---------------------------------------------------------------------------
class TestRemittancePosting:
    def test_full_pay_closes_invoice(self):
        s = _login(*DEFAULT_ADMIN)
        _p, payer_id, claim, inv = _build_submitted_claim(s)
        billed = int(claim["billed_cents"])
        # Pay the full billed amount — no contractual, no denial.
        r = s.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "check_or_eft_number": "CHK-P5-FULL",
            "total_paid_cents": billed,
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": billed, "paid_cents": billed,
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
            }],
        }, timeout=10)
        assert r.status_code == 201, r.text
        # Invoice balance must be zero; claim status must be paid.
        inv2 = s.get(f"{API}/billing/invoices/{inv['id']}", timeout=10).json()
        assert inv2["balance_cents"] == 0
        claim2 = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                       timeout=10).json()["claim"]
        assert claim2["status"] == "paid"
        assert claim2["paid_cents"] == billed

    def test_partial_pay_with_contractual_leaves_balance_for_patient(self):
        s = _login(*DEFAULT_ADMIN)
        _p, payer_id, claim, inv = _build_submitted_claim(s)
        billed = int(claim["billed_cents"])
        # Payer pays 60% of billed, contractual 20%, patient gets 20%.
        paid = billed * 60 // 100
        contractual = billed * 20 // 100
        patient_resp = billed - paid - contractual
        r = s.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": paid,
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": billed, "paid_cents": paid,
                "contractual_cents": contractual,
                "patient_resp_cents": patient_resp,
                "denied_cents": 0,
            }],
        }, timeout=10)
        assert r.status_code == 201, r.text
        inv2 = s.get(f"{API}/billing/invoices/{inv['id']}", timeout=10).json()
        # Per user choice 1b: invoice balance == patient_resp (billed -
        # paid - contractual). No new line is minted.
        assert inv2["balance_cents"] == patient_resp
        claim2 = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                       timeout=10).json()["claim"]
        assert claim2["status"] == "partially_paid"
        assert claim2["paid_cents"] == paid

    def test_denial_opens_work_item(self):
        s = _login(*DEFAULT_ADMIN)
        _p, payer_id, claim, inv = _build_submitted_claim(s)
        billed = int(claim["billed_cents"])
        r = s.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": 0,
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": billed, "paid_cents": 0,
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": billed, "denial_code": "CO-97",
            }],
        }, timeout=10)
        assert r.status_code == 201, r.text
        claim2 = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                       timeout=10).json()["claim"]
        assert claim2["status"] == "denied"
        # Denial work item must exist with our claim_id.
        items = s.get(f"{API}/billing/denial-work-items", timeout=10).json()
        mine = [i for i in items if i["claim_id"] == claim["id"]]
        assert len(mine) == 1
        assert mine[0]["denial_code"] == "CO-97"
        assert mine[0]["amount_cents"] == billed
        assert mine[0]["status"] == "open"
        assert mine[0]["assigned_to_id"] is None   # per user choice #2

    def test_remittance_rejects_mismatched_total(self):
        s = _login(*DEFAULT_ADMIN)
        _p, payer_id, claim, _inv = _build_submitted_claim(s)
        r = s.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": 999,  # lies
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": int(claim["billed_cents"]),
                "paid_cents": int(claim["billed_cents"]),
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
            }],
        }, timeout=10)
        assert r.status_code == 409
        assert "sum of claim paid" in r.text.lower()

    def test_remittance_rejects_cross_payer_claim(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _payer_id, claim, _inv = _build_submitted_claim(s)
        # Use a DIFFERENT payer in the header.
        other = s.post(f"{API}/billing/payers", json={
            "name": _unique("Other"), "payer_type": "commercial",
            "remit_method": "era",
        }, timeout=10).json()
        r = s.post(f"{API}/billing/remittances", json={
            "payer_id": other["id"],
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": 0,
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": int(claim["billed_cents"]),
                "paid_cents": 0, "contractual_cents": 0,
                "patient_resp_cents": 0, "denied_cents": 0,
            }],
        }, timeout=10)
        assert r.status_code == 409
        assert "different payer" in r.text.lower()


# ---------------------------------------------------------------------------
# Integration — denial work-item mutations
# ---------------------------------------------------------------------------
class TestDenialMutations:
    def _open_denial(self, s):
        _p, payer_id, claim, _inv = _build_submitted_claim(s)
        s.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": 0,
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": int(claim["billed_cents"]),
                "paid_cents": 0, "contractual_cents": 0,
                "patient_resp_cents": 0,
                "denied_cents": int(claim["billed_cents"]),
                "denial_code": "CO-45",
            }],
        }, timeout=10)
        items = s.get(f"{API}/billing/denial-work-items", timeout=10).json()
        mine = [i for i in items if i["claim_id"] == claim["id"]]
        return mine[0]

    def test_assign_and_progress_status(self):
        s = _login(*DEFAULT_ADMIN)
        item = self._open_denial(s)
        me = s.get(f"{API}/auth/me", timeout=10).json()
        r = s.patch(f"{API}/billing/denial-work-items/{item['id']}", json={
            "status": "in_progress", "assigned_to_id": me["id"],
            "resolution_notes": "working the denial",
        }, timeout=10)
        assert r.status_code == 200, r.text
        upd = r.json()
        assert upd["status"] == "in_progress"
        assert upd["assigned_to_id"] == me["id"]
        assert "working the denial" in (upd["resolution_notes"] or "")

    def test_illegal_transition_rejected(self):
        s = _login(*DEFAULT_ADMIN)
        item = self._open_denial(s)
        # open -> resolved is NOT allowed directly (must go via in_progress).
        r = s.patch(f"{API}/billing/denial-work-items/{item['id']}",
                  json={"status": "resolved"}, timeout=10)
        assert r.status_code in (400, 409), r.text

    def test_unknown_assignee_rejected(self):
        s = _login(*DEFAULT_ADMIN)
        item = self._open_denial(s)
        r = s.patch(f"{API}/billing/denial-work-items/{item['id']}", json={
            "assigned_to_id": "missing-" + uuid.uuid4().hex,
        }, timeout=10)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Integration — AR aging endpoint
# ---------------------------------------------------------------------------
class TestARAgingEndpoint:
    def test_aging_endpoint_returns_all_buckets(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/billing/ar/aging", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "total_balance_cents" in body
        assert "total_invoice_count" in body
        assert len(body["buckets"]) == len(AGING_BUCKETS)
        labels = {b["bucket"] for b in body["buckets"]}
        assert labels == {lbl for lbl, _, _ in AGING_BUCKETS}

    def test_aging_by_payer_groups_correctly(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/billing/ar/aging/by-payer", timeout=10)
        assert r.status_code == 200
        rows = r.json()["rows"]
        # Each row has its own bucket breakdown and a payer label.
        for row in rows:
            assert "payer_id" in row
            assert "payer_name" in row
            assert "total_balance_cents" in row
            assert len(row["buckets"]) == len(AGING_BUCKETS)


# ---------------------------------------------------------------------------
# Integration — statements
# ---------------------------------------------------------------------------
class TestStatements:
    def test_generate_read_list_statement(self):
        s = _login(*DEFAULT_ADMIN)
        patients = s.get(f"{API}/patients", timeout=15).json()
        patient = patients[0]
        r = s.post(f"{API}/billing/patients/{patient['id']}/statements",
                   timeout=10)
        assert r.status_code == 201, r.text
        stmt = r.json()
        assert stmt["patient_id"] == patient["id"]
        assert "PATIENT STATEMENT" in stmt["body"]
        # Listing includes it.
        rows = s.get(f"{API}/billing/patients/{patient['id']}/statements",
                     timeout=10).json()
        assert any(row["id"] == stmt["id"] for row in rows)
        # Read-one.
        r2 = s.get(
            f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}",
            timeout=10,
        )
        assert r2.status_code == 200
        assert r2.json()["id"] == stmt["id"]


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TestPhase5TenantIsolation:
    def test_sunrise_cannot_post_against_default_claim(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        _p, payer_id, claim, _inv = _build_submitted_claim(admin)
        r = sunrise.post(f"{API}/billing/remittances", json={
            "payer_id": payer_id,
            "received_at": datetime.now(timezone.utc).date().isoformat(),
            "total_paid_cents": int(claim["billed_cents"]),
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": int(claim["billed_cents"]),
                "paid_cents": int(claim["billed_cents"]),
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
            }],
        }, timeout=10)
        assert r.status_code in (404, 409), r.text

    def test_sunrise_cannot_read_default_statement(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        patients = admin.get(f"{API}/patients", timeout=15).json()
        patient = patients[0]
        stmt = admin.post(
            f"{API}/billing/patients/{patient['id']}/statements",
            timeout=10,
        ).json()
        r = sunrise.get(
            f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}",
            timeout=10,
        )
        assert r.status_code == 404
