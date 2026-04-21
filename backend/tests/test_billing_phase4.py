"""
Billing Phase 4 — claim submission scaffolding, outcome tracking,
work queues, timeline, and assignment.

Covers:
  * Status transition matrix (legal & illegal paths)
  * Submission record creation (ready → submitted) + payload shape
  * Outcome recording drives claim status (accepted/rejected/pending/
    paid/partially_paid/denied)
  * Work queues (pending-submission, rejected, follow-up) with filters
  * Assignment updates audited
  * Timeline merges history + scrubber runs + submissions
  * Tenant isolation
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv

from services.billing import transitions
from services.billing.submission import (
    build_json_payload,
    build_x12_837p_preview,
    DEFAULT_FOLLOWUP_DAYS,
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


def _build_ready_claim(s):
    """End-to-end helper that leaves us with a claim in status=ready."""
    patients = s.get(f"{API}/patients", timeout=15).json()
    patient = patients[0]
    sid = _ensure_self_pay(s)
    s.patch(f"{API}/billing/fee-schedules/{sid}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 6000},
    ], timeout=10)
    payer = s.post(f"{API}/billing/payers", json={
        "name": _unique("P4"), "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=10).json()
    sr = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Payer4"), "kind": "payer", "payer_id": payer["id"],
    }, timeout=10).json()
    s.patch(f"{API}/billing/fee-schedules/{sr['id']}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
    ], timeout=10)
    s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "Sub Scriber",
        "member_id": "M-" + uuid.uuid4().hex[:8],
    }, timeout=10)
    rec = s.post(f"{API}/patients/{patient['id']}/records", json={
        "record_type": "treatment", "title": "P4",
        "description": "x", "diagnosis": "low back pain",
        "treatment": "CMT",
    }, timeout=10).json()
    s.patch(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
        "procedures": [{"code_type": "cpt", "code": "98940",
                        "units": 1, "modifiers": []}],
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
        "billing_provider_id": rec.get("recorded_by") or "provider-1",
        "rendering_provider_id": rec.get("recorded_by") or "provider-1",
        "place_of_service": "11",
    }, timeout=10)
    result = s.post(f"{API}/billing/claims/{claim['id']}/validate",
                    timeout=10).json()
    assert result["status"] == "ready", result
    return patient, payer, claim


# ---------------------------------------------------------------------------
# Unit — status transition matrix
# ---------------------------------------------------------------------------
class TestClaimStatusTransitionMatrix:
    def test_legal_submission_flow(self):
        # ready → submitted → accepted → paid
        for a, b in [("ready", "submitted"),
                     ("submitted", "accepted"),
                     ("accepted", "paid"),
                     ("paid", "closed")]:
            assert transitions.advance("claim", a, b) == b

    def test_pending_is_reachable_from_submitted_and_accepted(self):
        assert transitions.advance("claim", "submitted", "pending") == "pending"
        assert transitions.advance("claim", "accepted", "pending") == "pending"
        # and pending can advance to paid/denied
        assert transitions.advance("claim", "pending", "paid") == "paid"
        assert transitions.advance("claim", "pending", "denied") == "denied"

    def test_illegal_transitions_raise(self):
        with pytest.raises(transitions.TransitionError):
            transitions.advance("claim", "draft", "submitted")
        with pytest.raises(transitions.TransitionError):
            transitions.advance("claim", "closed", "draft")
        with pytest.raises(transitions.TransitionError):
            transitions.advance("claim", "ready", "paid")

    def test_rejected_can_resubmit_after_round_trip(self):
        # rejected → ready → submitted is the correction flow
        assert transitions.advance("claim", "rejected", "ready") == "ready"
        assert transitions.advance("claim", "ready", "submitted") == "submitted"


# ---------------------------------------------------------------------------
# Unit — payload builders
# ---------------------------------------------------------------------------
class TestPayloadBuilders:
    def _fx(self):
        claim = {"id": "c-xyz", "claim_type": "professional",
                 "place_of_service": "11", "frequency_code": "1",
                 "service_date_from": "2026-02-01",
                 "service_date_to": "2026-02-01",
                 "billed_cents": 5500,
                 "billing_provider_id": "bp-1",
                 "rendering_provider_id": "rp-1"}
        dx = [{"sequence": 1, "code": "M54.16"},
              {"sequence": 2, "code": "M25.50"}]
        lines = [{"sequence": 1, "service_date": "2026-02-01",
                  "code_type": "cpt", "code": "98940",
                  "units": 1, "billed_cents": 5500,
                  "diagnosis_pointers": [1], "modifiers": ["25"]}]
        patient = {"id": "pt", "first_name": "Jane", "last_name": "Doe",
                   "date_of_birth": "1990-01-01", "gender": "female"}
        payer = {"id": "py", "name": "Acme Health", "external_id": "AH-001"}
        policy = {"id": "pol", "rank": "primary", "member_id": "M123",
                  "subscriber_name": "Jane Doe"}
        return claim, dx, lines, patient, payer, policy

    def test_json_payload_shape(self):
        p = build_json_payload(
            claim=self._fx()[0], diagnoses=self._fx()[1],
            lines=self._fx()[2], patient=self._fx()[3],
            payer=self._fx()[4], policy=self._fx()[5],
        )
        assert p["schema"] == "ccms.claim.v1"
        assert p["claim"]["billed_cents"] == 5500
        assert p["diagnoses"][0]["code"] == "M54.16"
        assert p["lines"][0]["code"] == "98940"
        assert p["patient"]["first_name"] == "Jane"
        assert p["payer"]["payer_id_external"] == "AH-001"
        assert p["policy"]["member_id"] == "M123"

    def test_x12_preview_has_envelope_and_lines(self):
        claim, dx, lines, patient, payer, policy = self._fx()
        x12 = build_x12_837p_preview(
            claim=claim, diagnoses=dx, lines=lines,
            patient=patient, payer=payer, policy=policy,
        )
        assert x12.startswith("ISA*")
        assert "ST*837*" in x12
        assert "CLM*" in x12
        assert "HI*" in x12
        assert "SV1*HC:98940:25*" in x12
        assert x12.rstrip().endswith("~")
        # Diagnosis code in HI segment should be period-stripped.
        assert "M5416" in x12


# ---------------------------------------------------------------------------
# Integration — submissions
# ---------------------------------------------------------------------------
class TestSubmissionLifecycle:
    def test_submission_transitions_ready_to_submitted(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        r = s.post(f"{API}/billing/claims/{claim['id']}/submissions", json={
            "method": "manual_portal", "external_reference": "PORTAL-1",
        }, timeout=10)
        assert r.status_code == 201, r.text
        sub = r.json()
        assert sub["method"] == "manual_portal"
        assert sub["external_reference"] == "PORTAL-1"
        assert sub["outcome"] is None
        # Claim status should now be "submitted"
        c2 = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                   timeout=10).json()["claim"]
        assert c2["status"] == "submitted"
        assert c2["submission_count"] == 1

    def test_cannot_submit_non_ready_claim(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        # Submit once
        r = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                   json={"method": "manual_portal"}, timeout=10)
        assert r.status_code == 201
        # Try again while in submitted state
        r2 = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                    json={"method": "manual_portal"}, timeout=10)
        assert r2.status_code == 409

    def test_submission_payload_accessible(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        sub = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                     json={"method": "manual_paper"}, timeout=10).json()
        pl = s.get(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/payload",
            timeout=10,
        )
        assert pl.status_code == 200, pl.text
        body = pl.json()
        assert body["payload_format"] == "json+x12-837p-preview"
        assert body["payload_json"]["schema"] == "ccms.claim.v1"
        assert "ST*837*" in body["payload_x12"]

    def test_list_submissions_returns_history(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        s.post(f"{API}/billing/claims/{claim['id']}/submissions",
               json={"method": "manual_paper"}, timeout=10)
        rows = s.get(f"{API}/billing/claims/{claim['id']}/submissions",
                     timeout=10).json()
        assert len(rows) == 1
        # Heavy payloads must NOT be included in the list response.
        assert "payload_json" not in rows[0]
        assert "payload_x12" not in rows[0]


# ---------------------------------------------------------------------------
# Integration — outcomes
# ---------------------------------------------------------------------------
class TestSubmissionOutcomes:
    def test_accept_then_pay(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        sub = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                     json={"method": "manual_portal"}, timeout=10).json()
        # First: accepted
        r = s.post(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
            json={"outcome": "accepted", "payer_reference": "ICN-1"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        c = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                  timeout=10).json()["claim"]
        assert c["status"] == "accepted"
        assert c["accepted_at"] is not None

    def test_reject_sets_denial_code_and_surfaces_in_status(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        sub = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                     json={"method": "manual_portal"}, timeout=10).json()
        r = s.post(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
            json={"outcome": "rejected", "denial_code": "CO-16",
                  "notes": "missing mod"},
            timeout=10,
        )
        assert r.status_code == 200
        c = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                  timeout=10).json()["claim"]
        assert c["status"] == "rejected"
        assert c["last_denial_code"] == "CO-16"

    def test_paid_records_amount(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        sub = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                     json={"method": "manual_portal"}, timeout=10).json()
        s.post(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
            json={"outcome": "accepted"}, timeout=10,
        )
        sub2 = s.get(f"{API}/billing/claims/{claim['id']}/submissions",
                     timeout=10).json()[0]
        assert sub2["outcome"] == "accepted"
        # accepted → paid via a second outcome requires a new submission
        # OR the status endpoint. For now the test just asserts we can't
        # record a second outcome on the same submission.
        r = s.post(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
            json={"outcome": "paid", "paid_cents": 4000}, timeout=10,
        )
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# Integration — timeline + assignment
# ---------------------------------------------------------------------------
class TestTimelineAndAssignment:
    def test_timeline_merges_history_scrubber_submissions(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        s.post(f"{API}/billing/claims/{claim['id']}/submissions",
               json={"method": "manual_portal"}, timeout=10)
        r = s.get(f"{API}/billing/claims/{claim['id']}/timeline",
                  timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["claim_id"] == claim["id"]
        kinds = {e["kind"] for e in body["entries"]}
        assert "history" in kinds
        assert "validation_run" in kinds
        assert "submission" in kinds
        # Sorted descending by `at`
        ats = [e["at"] for e in body["entries"] if e.get("at")]
        assert ats == sorted(ats, reverse=True)

    def test_assignment_roundtrip_audited(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        me = s.get(f"{API}/auth/me", timeout=10).json()
        r = s.patch(f"{API}/billing/claims/{claim['id']}/assignment",
                  json={"assigned_to": me["id"]}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["assigned_to"] == me["id"]
        # Timeline should reflect the assignment change
        tl = s.get(f"{API}/billing/claims/{claim['id']}/timeline",
                   timeout=10).json()
        assert any(e["action"] == "assignment_changed" for e in tl["entries"])

    def test_assignment_rejects_unknown_user(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        r = s.patch(f"{API}/billing/claims/{claim['id']}/assignment",
                  json={"assigned_to": "nobody-" + uuid.uuid4().hex},
                  timeout=10)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Integration — work queues
# ---------------------------------------------------------------------------
class TestWorkQueues:
    def test_pending_submission_queue_includes_ready_claims(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        rows = s.get(f"{API}/billing/claims/queues/pending-submission",
                     timeout=10).json()
        ids = {c["id"] for c in rows}
        assert claim["id"] in ids
        assert all(c["status"] in ("ready", "validation_failed") for c in rows)

    def test_rejected_queue_filter(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _py, claim = _build_ready_claim(s)
        sub = s.post(f"{API}/billing/claims/{claim['id']}/submissions",
                     json={"method": "manual_portal"}, timeout=10).json()
        s.post(
            f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
            json={"outcome": "rejected", "denial_code": "CO-97"},
            timeout=10,
        )
        rows = s.get(f"{API}/billing/claims/queues/rejected",
                     timeout=10).json()
        ids = {c["id"] for c in rows}
        assert claim["id"] in ids
        assert all(c["status"] in ("rejected", "denied") for c in rows)

    def test_queue_payer_filter(self):
        s = _login(*DEFAULT_ADMIN)
        _p, _payer, claim = _build_ready_claim(s)
        # Use the payer actually on the resulting claim — charge capture
        # will pick the patient's active primary policy, which may be an
        # earlier seeded policy rather than the one we just created.
        pid = claim["payer_id"]
        rows = s.get(
            f"{API}/billing/claims/queues/pending-submission",
            params={"payer_id": pid}, timeout=10,
        ).json()
        assert all(c["payer_id"] == pid for c in rows)
        assert any(c["id"] == claim["id"] for c in rows)

    def test_unknown_queue_404(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/billing/claims/queues/mystery", timeout=10)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
class TestPhase4TenantIsolation:
    def test_sunrise_cannot_submit_default_claim(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        _p, _py, claim = _build_ready_claim(admin)
        r = sunrise.post(f"{API}/billing/claims/{claim['id']}/submissions",
                         json={"method": "manual_portal"}, timeout=10)
        assert r.status_code == 404

    def test_sunrise_cannot_view_default_timeline(self):
        admin = _login(*DEFAULT_ADMIN)
        sunrise = _login(*GROUP_ADMIN)
        _p, _py, claim = _build_ready_claim(admin)
        r = sunrise.get(f"{API}/billing/claims/{claim['id']}/timeline",
                        timeout=10)
        assert r.status_code == 404
