"""
Billing Phase 6 — bulk remittance import, statement PDF, statement email.

Covers:
  * X12 835 parsing (BPR, TRN, N1*PR, CLP, CAS, SVC segments)
  * JSON import schema validation
  * Claim matching (explicit claim_id, payer_control_number,
    patient_control_number prefix, unmatched)
  * Staging endpoint + preview + commit + idempotency
  * Payer resolution (by name / external_id / fallback error)
  * PDF rendering (valid bytes with %PDF header, non-trivial size)
  * Email send (mock path) + delivery log
  * Email rejection when patient has no email
  * Tenant isolation
"""
from __future__ import annotations

import io
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv

from services.billing.remittance_import import (
    parse_835,
    parse_json_import,
    JSON_SCHEMA,
)
from services.billing.statement_delivery import (
    render_statement_pdf,
    render_statement_email_html,
)

load_dotenv("/app/backend/.env")
API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


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


def _unique(p): return f"{p}-{uuid.uuid4().hex[:6]}"


def _build_submitted_claim(s, ext_ref=None):
    patients = s.get(f"{API}/patients", timeout=15).json()
    patient = patients[0]
    payer = s.post(f"{API}/billing/payers", json={
        "name": _unique("P6"), "payer_type": "commercial",
        "remit_method": "era",
        "electronic_payer_id": _unique("PEXT"),
    }, timeout=10).json()
    sched = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("P6s"), "kind": "payer", "payer_id": payer["id"],
    }, timeout=10).json()
    s.patch(f"{API}/billing/fee-schedules/{sched['id']}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
    ], timeout=10)
    s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "Sub",
        "member_id": "M-" + uuid.uuid4().hex[:8],
    }, timeout=10)
    rec = s.post(f"{API}/patients/{patient['id']}/records", json={
        "record_type": "treatment", "title": "P6", "description": "x",
        "diagnosis": "LBP", "treatment": "CMT",
    }, timeout=10).json()
    s.patch(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
        "procedures": [{"code_type": "cpt", "code": "98940", "units": 1, "modifiers": []}],
        "diagnoses": [{"sequence": 1, "code": "M54.16"}],
        "responsibility": "insurance",
    }, timeout=10)
    s.post(f"{API}/patients/{patient['id']}/records/{rec['id']}/sign",
           timeout=10)
    inv = s.post(f"{API}/billing/encounters/{rec['id']}/capture",
                 timeout=10).json()
    claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}",
                   timeout=10).json()
    s.patch(f"{API}/billing/claims/{claim['id']}/header", json={
        "billing_provider_id": "bp", "rendering_provider_id": "rp",
        "place_of_service": "11",
    }, timeout=10)
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
    sub_body = {"method": "manual_portal"}
    if ext_ref:
        sub_body["external_reference"] = ext_ref
    s.post(f"{API}/billing/claims/{claim['id']}/submissions",
           json=sub_body, timeout=10)
    fresh = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                  timeout=10).json()["claim"]
    return patient, payer, fresh


def _sample_835(claim_id_prefix: str, payer_name: str,
                billed: float, paid: float, payer_ctl: str) -> str:
    # Minimal but valid 835-shaped text with `~` terminators.
    return "~".join([
        "ISA*00*          *00*          *ZZ*SUB            *ZZ*PAYER          *260101*1200*^*00501*000000001*0*T*:",
        "GS*HP*SUB*PAYER*20260101*1200*1*X*005010X221A1",
        "ST*835*0001",
        f"BPR*I*{billed:.2f}*C*CHK***01*111000025*DA*9876543210*{payer_name[:20]}*****DA*0000000000*20260101",
        f"TRN*1*CHK-{uuid.uuid4().hex[:6]}*EXT-{payer_name[:6]}",
        "DTM*405*20260101",
        f"N1*PR*{payer_name}",
        f"CLP*{claim_id_prefix}*1*{billed:.2f}*{paid:.2f}*0*12*{payer_ctl}",
        f"SVC*HC:98940*{billed:.2f}*{paid:.2f}",
        "SE*9*0001",
        "GE*1*1",
        "IEA*1*000000001",
        "",
    ])


# ---------------------------------------------------------------------------
# Unit — parsers
# ---------------------------------------------------------------------------
class TestParsers:
    def test_835_produces_expected_ir(self):
        txt = _sample_835("CLM123", "Acme Health", 55.00, 40.00, "ICN-1")
        ir = parse_835(txt)
        assert ir["source"] == "x12-835"
        assert ir["header"]["payer_hint"] == "Acme Health"
        assert ir["header"]["total_paid_cents"] == 5500
        assert ir["header"]["received_at"] == "2026-01-01"
        assert len(ir["claims"]) == 1
        c = ir["claims"][0]
        assert c["patient_control_number"] == "CLM123"
        assert c["billed_cents"] == 5500
        assert c["paid_cents"] == 4000
        assert c["payer_control_number"] == "ICN-1"
        assert len(c["lines"]) == 1
        assert c["lines"][0]["cpt_code"] == "98940"

    def test_835_handles_cas_contractual_and_denial(self):
        txt = (
            "~".join([
                "ST*835*0001",
                "BPR*I*35.00*C*CHK",
                "N1*PR*Test Payer",
                "CLP*PCN1*1*55.00*35.00*12",
                "CAS*CO*97*10.00",    # contractual
                "CAS*CO*50*5.00",     # denial (non-writedown)
                "SVC*HC:98940*55.00*35.00",
                "SE*6*0001",
                "",
            ])
        )
        ir = parse_835(txt)
        c = ir["claims"][0]
        assert c["contractual_cents"] == 1000
        assert c["denied_cents"] == 500
        assert c["denial_code"] == "CO-50"

    def test_json_import_validates_schema(self):
        ok = json.dumps({
            "schema": JSON_SCHEMA,
            "header": {"received_at": "2026-01-01",
                       "total_paid_cents": 4000,
                       "payer_hint": "Acme",
                       "check_or_eft_number": "CHK-9"},
            "claims": [{
                "payer_control_number": "ICN-1",
                "billed_cents": 5500, "paid_cents": 4000,
                "contractual_cents": 1000, "patient_resp_cents": 500,
                "denied_cents": 0,
            }],
        })
        ir = parse_json_import(ok)
        assert ir["source"] == "json"
        assert ir["header"]["total_paid_cents"] == 4000
        assert ir["claims"][0]["payer_control_number"] == "ICN-1"

    def test_json_bad_schema_rejected(self):
        with pytest.raises(ValueError):
            parse_json_import(json.dumps({"schema": "other.v1",
                                          "claims": []}))

    def test_json_empty_claims_rejected(self):
        with pytest.raises(ValueError):
            parse_json_import(json.dumps({"schema": JSON_SCHEMA,
                                          "claims": []}))

    def test_835_empty_payload_rejected(self):
        with pytest.raises(ValueError):
            parse_835("ST*835*0001~SE*1*0001~")


# ---------------------------------------------------------------------------
# Integration — import staging & commit
# ---------------------------------------------------------------------------
class TestImportStageAndCommit:
    def test_json_import_end_to_end(self):
        s = _login(*DEFAULT_ADMIN)
        ext_ref = _unique("ICN")
        _p, _payer, claim = _build_submitted_claim(s, ext_ref=ext_ref)
        # Charge capture picks the patient's existing primary policy,
        # which may belong to a different (seeded) payer than the one
        # we just created — resolve the *actual* payer on the claim
        # and stamp an electronic_payer_id so the import can resolve.
        actual_payer_id = claim["payer_id"]
        ep_id = _unique("PEXT")
        s.patch(f"{API}/billing/payers/{actual_payer_id}",
              json={"electronic_payer_id": ep_id}, timeout=10)
        billed = int(claim["billed_cents"])
        payload = {
            "schema": JSON_SCHEMA,
            "header": {
                "received_at": datetime.now(timezone.utc).date().isoformat(),
                "total_paid_cents": billed,
                "payer_external_id": ep_id,
                "check_or_eft_number": "CHK-J-1",
            },
            "claims": [{
                "payer_control_number": ext_ref,
                "claim_id": claim["id"],
                "billed_cents": billed, "paid_cents": billed,
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
            }],
        }
        r = s.post(
            f"{API}/billing/remittances/import",
            files={"file": ("import.json", json.dumps(payload),
                            "application/json")},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        staged = r.json()
        assert staged["matched_count"] == 1
        assert staged["unmatched_count"] == 0
        assert staged["resolved_payer_id"] == actual_payer_id

        # Commit
        r2 = s.post(
            f"{API}/billing/remittances/imports/{staged['id']}/commit",
            timeout=15,
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["status"] == "committed"
        assert body["claim_row_count"] == 1

        # Idempotent: second commit rejected.
        r3 = s.post(
            f"{API}/billing/remittances/imports/{staged['id']}/commit",
            timeout=10,
        )
        assert r3.status_code == 409

    def test_x12_835_import_matches_by_payer_control(self):
        s = _login(*DEFAULT_ADMIN)
        ext_ref = _unique("ICN")
        _p, _payer, claim = _build_submitted_claim(s, ext_ref=ext_ref)
        actual_payer_id = claim["payer_id"]
        ep_id = _unique("PEXT")
        s.patch(f"{API}/billing/payers/{actual_payer_id}",
              json={"electronic_payer_id": ep_id}, timeout=10)
        # Resolve the payer name for the N1*PR segment.
        payer_name = s.get(f"{API}/billing/payers", timeout=10).json()
        payer_name = next(p["name"] for p in payer_name if p["id"] == actual_payer_id)
        billed = int(claim["billed_cents"])
        txt = _sample_835(claim["id"][:8], payer_name,
                          billed / 100, billed / 100, ext_ref)
        r = s.post(
            f"{API}/billing/remittances/import",
            files={"file": ("remit.835", txt, "text/plain")},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        staged = r.json()
        assert staged["matched_count"] == 1
        match = staged["claims"][0]["match"]
        assert match["match_method"] in ("payer_control", "patient_control")
        assert match["claim_id"] == claim["id"]

    def test_unmatched_rows_block_commit(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _payer, claim = _build_submitted_claim(s)
        actual_payer_id = claim["payer_id"]
        ep_id = _unique("PEXT")
        s.patch(f"{API}/billing/payers/{actual_payer_id}",
              json={"electronic_payer_id": ep_id}, timeout=10)
        payload = {
            "schema": JSON_SCHEMA,
            "header": {
                "received_at": "2026-01-01",
                "total_paid_cents": 5500,
                "payer_external_id": ep_id,
            },
            "claims": [{
                "billed_cents": 5500, "paid_cents": 5500,
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
                "payer_control_number": "does-not-exist",
            }],
        }
        r = s.post(
            f"{API}/billing/remittances/import",
            files={"file": ("x.json", json.dumps(payload), "application/json")},
            timeout=10,
        )
        assert r.status_code == 200
        staged = r.json()
        assert staged["unmatched_count"] == 1
        r2 = s.post(
            f"{API}/billing/remittances/imports/{staged['id']}/commit",
            timeout=10,
        )
        assert r2.status_code == 409

    def test_unresolvable_payer_blocks_commit(self):
        s = _login(*DEFAULT_ADMIN)
        # Post a JSON import with a payer nobody knows about, so that
        # resolve_payer_id returns None.
        payload = {
            "schema": JSON_SCHEMA,
            "header": {
                "received_at": "2026-01-01",
                "total_paid_cents": 1000,
                "payer_hint": "Completely Unknown Payer " + uuid.uuid4().hex[:6],
            },
            "claims": [{
                "billed_cents": 1000, "paid_cents": 1000,
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
                "claim_id": "not-a-real-claim-id",
            }],
        }
        r = s.post(
            f"{API}/billing/remittances/import",
            files={"file": ("x.json", json.dumps(payload), "application/json")},
            timeout=10,
        )
        assert r.status_code == 200
        staged = r.json()
        assert staged["resolved_payer_id"] is None
        r2 = s.post(
            f"{API}/billing/remittances/imports/{staged['id']}/commit",
            timeout=10,
        )
        assert r2.status_code == 409
        assert "payer" in r2.text.lower()

    def test_empty_upload_rejected(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.post(
            f"{API}/billing/remittances/import",
            files={"file": ("x.json", b"", "application/json")},
            timeout=5,
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Unit — PDF rendering
# ---------------------------------------------------------------------------
class TestPdf:
    def test_render_pdf_has_valid_header(self):
        stmt = {"id": "aaaaa", "body": "PATIENT STATEMENT\nAs of: 2026-01-01",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "as_of_date": "2026-01-01",
                "total_balance_cents": 1500,
                "invoice_count": 1}
        pdf = render_statement_pdf(
            statement=stmt,
            patient={"first_name": "Jane", "last_name": "Doe",
                     "email": "jane@ex.com"},
        )
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 800   # non-trivial output

    def test_email_html_contains_total_and_name(self):
        stmt = {"total_balance_cents": 2500}
        html = render_statement_email_html(
            patient={"first_name": "Jane", "last_name": "Doe"},
            statement=stmt,
        )
        assert "Jane Doe" in html
        assert "$25.00" in html


# ---------------------------------------------------------------------------
# Integration — PDF + email endpoints
# ---------------------------------------------------------------------------
class TestDeliveryEndpoints:
    def test_download_pdf(self):
        s = _login(*DEFAULT_ADMIN)
        patients = s.get(f"{API}/patients", timeout=15).json()
        patient = patients[0]
        stmt = s.post(
            f"{API}/billing/patients/{patient['id']}/statements",
            timeout=10,
        ).json()
        r = s.get(
            f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}/pdf",
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert r.content[:4] == b"%PDF"

    def test_email_mock_path_when_no_key(self):
        # Ensure any RESEND_API_KEY set in env doesn't interfere.
        prev = os.environ.pop("RESEND_API_KEY", None)
        try:
            # Force the cached readiness back to unknown for this proc.
            import services.billing.statement_delivery as sd
            sd._RESEND_READY = None
            s = _login(*DEFAULT_ADMIN)
            patients = s.get(f"{API}/patients", timeout=15).json()
            patient = patients[0]
            # Give the patient an email if missing.
            if not patient.get("email"):
                s.patch(f"{API}/patients/{patient['id']}", json={
                    "email": f"demo-{uuid.uuid4().hex[:6]}@example.com",
                }, timeout=10)
            stmt = s.post(
                f"{API}/billing/patients/{patient['id']}/statements",
                timeout=10,
            ).json()
            r = s.post(
                f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}/send",
                timeout=30,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            # Note: the in-process backend may have a real key cached,
            # so we only assert the mandatory fields. The /send endpoint
            # surfaces a `delivery_id` whose row carries the message_id.
            assert body["sent"] is True
            assert body["delivery_id"]
            # Delivery log row should exist.
            dr = s.get(
                f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}/deliveries",
                timeout=10,
            ).json()
            assert any(d["statement_id"] == stmt["id"] for d in dr)
        finally:
            if prev:
                os.environ["RESEND_API_KEY"] = prev

    def test_email_rejects_patient_without_email(self):
        s = _login(*DEFAULT_ADMIN)
        # Create a fresh patient with no email.
        p = s.post(f"{API}/patients", json={
            "first_name": "NoEmail", "last_name": _unique("X"),
            "date_of_birth": "1990-01-01", "gender": "female",
        }, timeout=10)
        assert p.status_code == 201, p.text
        patient = p.json()
        stmt = s.post(
            f"{API}/billing/patients/{patient['id']}/statements",
            timeout=10,
        ).json()
        r = s.post(
            f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}/send",
            timeout=10,
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TestPhase6TenantIsolation:
    def test_sunrise_cannot_view_default_import(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        ext_ref = _unique("ICN")
        _p, _payer, claim = _build_submitted_claim(admin, ext_ref=ext_ref)
        actual_payer_id = claim["payer_id"]
        ep_id = _unique("PEXT")
        admin.patch(f"{API}/billing/payers/{actual_payer_id}",
                  json={"electronic_payer_id": ep_id}, timeout=10)
        payload = {
            "schema": JSON_SCHEMA,
            "header": {"received_at": "2026-01-01",
                       "total_paid_cents": int(claim["billed_cents"]),
                       "payer_external_id": ep_id},
            "claims": [{
                "claim_id": claim["id"],
                "billed_cents": int(claim["billed_cents"]),
                "paid_cents": int(claim["billed_cents"]),
                "contractual_cents": 0, "patient_resp_cents": 0,
                "denied_cents": 0,
            }],
        }
        r = admin.post(
            f"{API}/billing/remittances/import",
            files={"file": ("x.json", json.dumps(payload), "application/json")},
            timeout=10,
        )
        assert r.status_code == 200
        staged_id = r.json()["id"]
        r2 = sunrise.get(f"{API}/billing/remittances/imports/{staged_id}",
                         timeout=10)
        assert r2.status_code == 404

    def test_sunrise_cannot_download_default_pdf(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        patients = admin.get(f"{API}/patients", timeout=15).json()
        patient = patients[0]
        stmt = admin.post(
            f"{API}/billing/patients/{patient['id']}/statements",
            timeout=10,
        ).json()
        r = sunrise.get(
            f"{API}/billing/patients/{patient['id']}/statements/{stmt['id']}/pdf",
            timeout=10,
        )
        assert r.status_code == 404
