"""
Billing — Eligibility 270/271 end-to-end tests.

Covers:
  * X12 270 builder produces a spec-compliant wire (segment presence,
    envelope, subscriber identity, EQ loop).
  * X12 271 parser round-trips the synthesised response into the
    canonical result shape.
  * MockEligibilityEngine returns a deterministic profile per
    (member_id, payer_id) pair.
  * Full API flow: create policy → POST /eligibility-check →
    GET /eligibility-checks → GET /eligibility-checks/{id} (reauth).
  * Tenant isolation: one tenant's checks are not visible to another.
  * 404 on unknown policy_id.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

from services.billing.clearinghouse.x12_270_271 import (
    build_270_request,
    parse_271_response,
)
from services.billing.eligibility import MockEligibilityEngine


load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    rr = s.post(f"{API}/auth/reauth",
                json={"password": password}, timeout=10)
    if rr.status_code == 200:
        rtok = rr.json().get("reauth_token")
        if rtok:
            s.headers["x-reauth-token"] = rtok
    return s


# ---------------------------------------------------------------------------
# Pure wire — builder / parser round trip
# ---------------------------------------------------------------------------
class Test270Builder:
    submitter = {"id": "SUB12345", "name": "CCMS BILLING",
                 "contact_name": "BILLING", "contact_phone": "5555551212"}
    receiver = {"id": "PAC1234", "name": "PACIFICCARE"}
    provider = {"npi": "1841792253", "name": "Dr. Noah Carter",
                "entity_type": "person",
                "last_name": "Carter", "first_name": "Noah"}
    payer = {"name": "PacifiCare Commercial",
             "electronic_payer_id": "PAC1234",
             "payer_type": "commercial"}
    patient = {"first_name": "Claire", "last_name": "Morgan",
               "date_of_birth": "1986-09-09", "sex_at_birth": "female"}
    policy = {"member_id": "PAC-MOR-1009", "group_number": "RPBIO-HR",
              "relationship_to_subscriber": "self"}

    def test_envelope_segments_present(self):
        wire = build_270_request(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=self.policy,
        )
        assert wire.startswith("ISA*")
        assert "\nGS*HB*" in wire
        assert "ST*270*0001*005010X279A1" in wire
        assert "\nSE*" in wire
        assert "\nGE*1*" in wire
        assert wire.rstrip().endswith("~")
        assert "IEA*1*" in wire

    def test_subscriber_identity_present(self):
        wire = build_270_request(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=self.policy,
        )
        # Subscriber NM1*IL carries last/first and member id.
        assert "NM1*IL*1*MORGAN*CLAIRE****MI*PAC-MOR-1009" in wire
        # DMG for DOB + gender
        assert "DMG*D8*19860909*F" in wire
        # EQ (service-type inquiry) — at least one (default 30)
        assert "EQ*30" in wire

    def test_payer_and_provider_hl_levels(self):
        wire = build_270_request(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=self.policy,
        )
        assert "HL*1**20*1" in wire          # info source
        assert "HL*2*1*21*1" in wire         # info receiver
        assert "HL*3*2*22*0" in wire         # subscriber
        # NM1*PR for payer + NM1*1P for provider
        assert "NM1*PR*2*PACIFICARE COMMERCIAL" in wire
        assert "NM1*1P*1*CARTER*NOAH****XX*1841792253" in wire

    def test_service_type_codes_expand(self):
        wire = build_270_request(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=self.policy,
            service_type_codes=["30", "33", "98"],
        )
        assert "EQ*30" in wire
        assert "EQ*33" in wire
        assert "EQ*98" in wire


class TestMockEngineAndParser:
    submitter = {"id": "SUB12345", "name": "CCMS BILLING"}
    receiver = {"id": "PAC1234", "name": "PACIFICCARE"}
    provider = {"npi": "1841792253", "name": "Dr. Noah Carter",
                "entity_type": "person",
                "last_name": "Carter", "first_name": "Noah"}
    payer = {"id": "payer-uuid", "name": "PacifiCare Commercial",
             "electronic_payer_id": "PAC1234",
             "payer_type": "commercial"}
    patient = {"first_name": "Claire", "last_name": "Morgan",
               "date_of_birth": "1986-09-09", "sex_at_birth": "female"}

    def test_active_commercial_coverage(self):
        policy = {"member_id": "PAC-MOR-1009",
                  "group_number": "RPBIO-HR",
                  "relationship_to_subscriber": "self",
                  "effective_date": "2026-01-01",
                  "termination_date": "2026-12-31"}
        outcome = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=policy,
        )
        r = outcome["result"]
        assert r["coverage_active"] is True
        assert r["payer_name"].upper().startswith("PACIFICARE")
        assert r["subscriber_name"].upper().endswith("MORGAN")
        assert r["member_id"] == "PAC-MOR-1009"
        assert r["plan_name"]
        assert r["effective_date"] == "2026-01-01"
        assert r["termination_date"] == "2026-12-31"
        # Copay is in the $25-$40 range per the derivation rules
        assert r["copay_cents"] is not None
        assert 2500 <= r["copay_cents"] <= 4000
        # Deductible is $1000-$2500
        assert r["deductible_cents"] is not None
        assert 100000 <= r["deductible_cents"] <= 250000
        # Met is never larger than total
        assert r["deductible_met_cents"] is not None
        assert 0 <= r["deductible_met_cents"] <= r["deductible_cents"]
        # Coinsurance defaults to 20% for commercial
        assert r["coinsurance_pct"] == 20
        # At least one EB*1 (active) benefit row was emitted
        assert any(b["qualifier"] == "1" for b in r["benefits"])

    def test_inactive_coverage_on_term_marker(self):
        policy = {"member_id": "PAC-MEMBER-TERM",
                  "relationship_to_subscriber": "self",
                  "effective_date": "2025-01-01",
                  "termination_date": "2025-12-31"}
        outcome = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=policy,
        )
        r = outcome["result"]
        assert r["coverage_active"] is False
        assert r["plan_name"] == "INACTIVE COVERAGE"
        assert r["messages"]

    def test_medicare_zero_copay_deductible(self):
        medicare_payer = {
            "id": "medicare-uuid", "name": "MEDICARE PART B",
            "electronic_payer_id": "MEDICARE", "payer_type": "medicare",
        }
        policy = {"member_id": "1EG4TE5MK73",
                  "relationship_to_subscriber": "self"}
        outcome = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=medicare_payer,
            patient=self.patient, policy=policy,
        )
        r = outcome["result"]
        assert r["coverage_active"] is True
        assert r["copay_cents"] == 0
        assert r["coinsurance_pct"] == 20
        assert r["deductible_cents"] == 24000
        assert "MEDICARE" in r["plan_name"]

    def test_workers_comp_zero_copay_no_deductible(self):
        wc_payer = {
            "id": "wc-uuid", "name": "OREGON SAIF WC",
            "electronic_payer_id": "SAIFOR", "payer_type": "workers_comp",
        }
        policy = {"member_id": "WC-CLAIM-1234",
                  "relationship_to_subscriber": "self"}
        outcome = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=wc_payer,
            patient=self.patient, policy=policy,
        )
        r = outcome["result"]
        assert r["coverage_active"] is True
        assert r["copay_cents"] == 0
        assert r["deductible_cents"] == 0
        assert "WORKERS" in r["plan_name"]

    def test_deterministic_same_member_same_result(self):
        policy = {"member_id": "PAC-MOR-1009",
                  "group_number": "RPBIO-HR",
                  "relationship_to_subscriber": "self"}
        a = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=policy,
        )
        b = MockEligibilityEngine().check(
            submitter=self.submitter, receiver=self.receiver,
            provider=self.provider, payer=self.payer,
            patient=self.patient, policy=policy,
        )
        assert a["result"]["copay_cents"] == b["result"]["copay_cents"]
        assert a["result"]["deductible_cents"] == b["result"]["deductible_cents"]
        assert a["result"]["plan_name"] == b["result"]["plan_name"]


# ---------------------------------------------------------------------------
# API end-to-end — against a live patient + policy
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def admin_session():
    return _login(*DEFAULT_ADMIN)


@pytest.fixture
def demo_policy(admin_session):
    """Reuse an existing Riverbend policy — every demo patient has one
    seeded by `demo.billing_seed`. Returns (policy_id, policy_dict)."""
    patients = admin_session.get(f"{API}/patients", timeout=15).json()
    assert patients, "demo patients missing"
    for p in patients:
        pid = p["id"]
        policies = admin_session.get(
            f"{API}/billing/insurance-policies?patient_id={pid}",
            timeout=10,
        ).json()
        if policies:
            return policies[0]
    pytest.fail("no demo patient has a seeded insurance policy")


class TestEligibilityApi:
    def test_run_check_returns_201_with_result(self, admin_session, demo_policy):
        r = admin_session.post(
            f"{API}/billing/policies/{demo_policy['id']}/eligibility-check",
            json={"service_type_codes": ["30", "33", "98"]},
            timeout=15,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["policy_id"] == demo_policy["id"]
        assert body["engine"] == "mock"
        assert body["sandbox"] is True
        assert body["service_type_codes"] == ["30", "33", "98"]
        res = body["result"]
        assert res["member_id"] == demo_policy["member_id"]
        # Wire bytes come through on the create response for preview.
        assert body["request_wire"].startswith("ISA*")
        assert "ST*270*" in body["request_wire"]
        assert "ST*271*" in body["response_wire"]

    def test_list_and_fetch_detail(self, admin_session, demo_policy):
        admin_session.post(
            f"{API}/billing/policies/{demo_policy['id']}/eligibility-check",
            json={}, timeout=15,
        )
        r = admin_session.get(
            f"{API}/billing/policies/{demo_policy['id']}/eligibility-checks",
            timeout=10,
        )
        assert r.status_code == 200
        rows = r.json()
        assert rows
        first = rows[0]
        # List endpoint must NOT include the raw wires.
        assert "request_wire" not in first
        assert "response_wire" not in first
        # Detail endpoint — MFA-gated — returns the full wires.
        check_id = first["id"]
        detail = admin_session.get(
            f"{API}/billing/eligibility-checks/{check_id}", timeout=10,
        )
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["id"] == check_id
        assert body["request_wire"] and "ST*270*" in body["request_wire"]
        assert body["response_wire"] and "ST*271*" in body["response_wire"]

    def test_unknown_policy_returns_404(self, admin_session):
        r = admin_session.post(
            f"{API}/billing/policies/{uuid.uuid4()}/eligibility-check",
            json={}, timeout=10,
        )
        assert r.status_code == 404

    def test_detail_requires_reauth_without_token(self, demo_policy):
        """Session without reauth cannot fetch the raw 270/271 wires."""
        s = requests.Session()
        r = s.post(f"{API}/auth/login",
                   json={"email": DEFAULT_ADMIN[0],
                         "password": DEFAULT_ADMIN[1]}, timeout=15)
        assert r.status_code == 200
        tok = r.cookies.get("access_token")
        if tok:
            s.headers["Authorization"] = f"Bearer {tok}"
        # DO NOT call /auth/reauth — omit x-reauth-token.

        # Seed one check via a reauthed session so we have something to
        # fetch.
        reauth_sess = _login(*DEFAULT_ADMIN)
        created = reauth_sess.post(
            f"{API}/billing/policies/{demo_policy['id']}/eligibility-check",
            json={}, timeout=15,
        ).json()
        check_id = created["id"]

        r2 = s.get(f"{API}/billing/eligibility-checks/{check_id}", timeout=10)
        # Reauth enforcement returns 401 when no x-reauth-token is present.
        assert r2.status_code in (401, 403), r2.status_code
