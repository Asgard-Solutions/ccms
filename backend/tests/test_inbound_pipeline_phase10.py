"""
Phase 10 — inbound response/report handling + operational follow-up.

Covers:
  * POST /billing/clearinghouse/reports/ingest
      - 277CA accepted  → claim flips submitted → accepted + event
      - 277CA rejected  → claim flips submitted → rejected + event +
        auto follow-up flag
      - 999  accepted/rejected — same pattern, different event type
      - ERA receipt (era_835_receipt) — stores row, emits era_posted
        event, does NOT flip claim status (posting is remittance flow)
      - batch_ack without claim_id / submission_id is accepted
      - Payload matched via adapter_external_id when claim_id omitted
      - Status-ineligible claims (already paid/closed) are NOT flipped
  * GET /billing/clearinghouse/reports lists with filters
  * POST /billing/claims/{id}/flag-followup stores reason, surfaces
    the claim on the `follow-up` queue tab with `followup_flag=True`
    and a computed `next_action_at`
  * DELETE /billing/claims/{id}/flag-followup clears the flag; claim
    no longer appears in the follow-up tab (unless stale / partially
    paid / appealed independently)
  * Claims Queue rows now carry Phase 10 enrichment:
      - aging_basis, aging_basis_at, aging_days
      - last_activity_at
      - followup_flag, followup_reason, next_action_at
      - assignee_name (existing)
  * Rejected ack → claim also surfaces in the `rejected` tab
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

_BACKEND_DIR = "/app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login() -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": ADMIN[0], "password": ADMIN[1]}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth",
               json={"password": ADMIN[1]}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


def _seed_submitted_claim(s):
    """Seed a claim, validate, submit — returns the latest submission."""
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"P10 Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial", "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
    }, timeout=15).json()
    patient = s.post(f"{API}/patients", json={
        "first_name": "P10", "last_name": f"In{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"p10-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    policy = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "P10 Subscriber",
        "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()
    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "policy_id": policy["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567893",
        "rendering_provider_id": "1234567893",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
            "modifiers": ["AT"],
        }],
    }, timeout=15).json()
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)
    r = s.post(
        f"{API}/billing/claims/{claim['id']}/submissions",
        json={"method": "batch_file"}, timeout=15,
    )
    assert r.status_code == 201, r.text
    return claim, r.json()


def _claim_status(s, claim_id: str) -> str:
    d = s.get(f"{API}/billing/claims/{claim_id}/detail", timeout=10).json()
    return d.get("claim", {}).get("status") or d.get("status") or ""


# ---------------------------------------------------------------------------
# 1. 277CA accepted flips submitted → accepted + emits timeline event
# ---------------------------------------------------------------------------
def test_ingest_277ca_accepted_advances_claim_to_accepted():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    assert _claim_status(s, claim["id"]) == "submitted"

    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "accepted",
        "claim_id": claim["id"],
        "submission_id": sub["id"],
        "notes": "Payer acknowledged 277CA",
        "parsed": {"category_code": "A2", "status_code": "20"},
    }, timeout=15)
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["report_type"] == "277ca"
    assert report["status"] == "accepted"
    assert report["claim_id"] == claim["id"]

    assert _claim_status(s, claim["id"]) == "accepted"

    events = s.get(
        f"{API}/billing/claims/{claim['id']}/events", timeout=10,
    ).json()
    kinds = [e["event_type"] for e in events]
    assert "ack_277ca_accepted" in kinds


# ---------------------------------------------------------------------------
# 2. 277CA rejected flips submitted → rejected + auto follow-up flag
# ---------------------------------------------------------------------------
def test_ingest_277ca_rejected_moves_claim_to_rejected_and_autoflags_followup():
    s = _login()
    claim, sub = _seed_submitted_claim(s)

    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "rejected",
        "claim_id": claim["id"],
        "submission_id": sub["id"],
        "denial_code": "16",
        "notes": "Missing subscriber id (CARC 16)",
    }, timeout=15)
    assert r.status_code == 201, r.text

    assert _claim_status(s, claim["id"]) == "rejected"

    # Follow-up flag auto-set by the inbound pipeline.
    detail = s.get(
        f"{API}/billing/claims/{claim['id']}/detail", timeout=10,
    ).json().get("claim", {})
    assert detail.get("followup_flag") is True
    assert "277CA rejection" in (detail.get("followup_reason") or "")
    assert detail.get("next_action_at")

    # Event stream carries the rejection with the CARC attached.
    events = s.get(
        f"{API}/billing/claims/{claim['id']}/events", timeout=10,
    ).json()
    rej = [e for e in events if e["event_type"] == "ack_277ca_rejected"]
    assert rej and rej[0].get("denial_code") == "16"


# ---------------------------------------------------------------------------
# 3. Resolution via adapter_external_id (no claim_id in payload)
# ---------------------------------------------------------------------------
def test_ingest_resolves_claim_via_adapter_external_id_only():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    external = sub["adapter_external_id"]
    assert external and external.startswith("chc-sbx-")

    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "999",
        "status": "accepted",
        "adapter_external_id": external,
    }, timeout=15)
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["claim_id"] == claim["id"]
    assert report["submission_id"] == sub["id"]
    assert report["external_id"] == external
    assert _claim_status(s, claim["id"]) == "accepted"


# ---------------------------------------------------------------------------
# 4. ERA receipt stores row + event but does NOT flip claim status
# ---------------------------------------------------------------------------
def test_ingest_era_receipt_emits_era_posted_event_without_status_flip():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    assert _claim_status(s, claim["id"]) == "submitted"

    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "era_835_receipt",
        "status": "info",
        "claim_id": claim["id"],
        "submission_id": sub["id"],
        "raw_content": "ISA*00*          *00*...",   # synthetic
        "notes": "ERA file received, pending post",
    }, timeout=15)
    assert r.status_code == 201, r.text
    # Claim stays submitted — posting owned by remittance flow.
    assert _claim_status(s, claim["id"]) == "submitted"

    events = s.get(
        f"{API}/billing/claims/{claim['id']}/events", timeout=10,
    ).json()
    assert "era_posted" in {e["event_type"] for e in events}


# ---------------------------------------------------------------------------
# 5. Batch ack without a claim link is accepted
# ---------------------------------------------------------------------------
def test_ingest_batch_ack_without_claim_is_accepted():
    s = _login()
    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "batch_ack",
        "status": "accepted",
        "notes": "Batch TA1 accepted",
    }, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["claim_id"] is None
    assert body["report_type"] == "batch_ack"


def test_ingest_with_no_claim_reference_is_rejected_for_claim_level_reports():
    s = _login()
    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "rejected",
        # No claim_id / submission_id / adapter_external_id.
    }, timeout=15)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 6. Reports listing
# ---------------------------------------------------------------------------
def test_list_clearinghouse_reports_filters_by_claim_and_type():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "999",
        "status": "accepted",
        "claim_id": claim["id"],
        "submission_id": sub["id"],
    }, timeout=15)
    # 277 on the SAME claim to test type filter.
    s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "accepted",
        "claim_id": claim["id"],
    }, timeout=15)
    r = s.get(
        f"{API}/billing/clearinghouse/reports?claim_id={claim['id']}",
        timeout=10,
    )
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    assert all(row["claim_id"] == claim["id"] for row in rows)
    # Narrow by type.
    r = s.get(
        f"{API}/billing/clearinghouse/reports?claim_id={claim['id']}"
        "&report_type=999", timeout=10,
    )
    rows = r.json()
    assert rows and all(row["report_type"] == "999" for row in rows)


# ---------------------------------------------------------------------------
# 7. Manual flag-for-follow-up + queue surfacing
# ---------------------------------------------------------------------------
def _find_claim_in_queue(s, tab: str, claim_id: str) -> dict | None:
    # Page through up to 200 rows — the polluted test DB may have many.
    r = s.get(f"{API}/billing/claims/queue?tab={tab}&page_size=200",
              timeout=15)
    assert r.status_code == 200, r.text
    for row in r.json().get("rows", []):
        if row["id"] == claim_id:
            return row
    return None


def test_manual_followup_flag_surfaces_claim_on_followup_tab():
    s = _login()
    claim, _ = _seed_submitted_claim(s)
    # Manually flag with a reason.
    r = s.post(f"{API}/billing/claims/{claim['id']}/flag-followup", json={
        "reason": "Subscriber id corrected in Member Portal, needs resubmit",
        "next_action_at": "2026-04-25T10:00:00+00:00",
    }, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["followup_flag"] is True
    assert body["next_action_at"].startswith("2026-04-25")

    row = _find_claim_in_queue(s, "follow-up", claim["id"])
    assert row is not None, "flagged claim should be on follow-up tab"
    assert row["followup_flag"] is True
    assert row["followup_reason"].startswith("Subscriber id corrected")
    assert row["next_action_at"].startswith("2026-04-25")
    assert row["canonical_status"] in ("follow_up", "submitted")


def test_clear_followup_flag_removes_manual_branch_from_tab():
    s = _login()
    claim, _ = _seed_submitted_claim(s)
    s.post(f"{API}/billing/claims/{claim['id']}/flag-followup", json={
        "reason": "Review with provider",
    }, timeout=15)
    assert _find_claim_in_queue(s, "follow-up", claim["id"]) is not None

    r = s.delete(f"{API}/billing/claims/{claim['id']}/flag-followup",
                  timeout=15)
    assert r.status_code == 200, r.text
    # The claim may still surface in follow-up via staleness; but the
    # manual flag itself is cleared — verify via detail.
    detail = s.get(
        f"{API}/billing/claims/{claim['id']}/detail", timeout=10,
    ).json().get("claim", {})
    assert detail.get("followup_flag") is False
    assert detail.get("followup_reason") is None
    assert detail.get("next_action_at") is None


# ---------------------------------------------------------------------------
# 8. Queue row carries Phase 10 enrichment
# ---------------------------------------------------------------------------
def test_queue_row_includes_aging_and_last_activity_fields():
    s = _login()
    claim, _ = _seed_submitted_claim(s)
    row = _find_claim_in_queue(s, "all", claim["id"])
    assert row is not None
    # Aging enrichment is always present — never None on a submitted
    # claim because `last_submission_at` is stamped at submit time.
    assert row["aging_basis"] in ("last_submission_at", "submitted_at",
                                   "updated_at", "created_at")
    assert row["aging_basis_at"]
    assert row["aging_days"] is not None
    assert row["aging_days"] >= 0
    assert row["last_activity_at"]
    # Follow-up fields default to safe values.
    assert row["followup_flag"] is False
    assert row["followup_reason"] is None
    # Canonical status is always tagged.
    assert row["canonical_status"]


# ---------------------------------------------------------------------------
# 9. Rejected ack lands the claim on the `rejected` tab
# ---------------------------------------------------------------------------
def test_rejected_ack_surfaces_claim_on_rejected_tab():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "rejected",
        "claim_id": claim["id"],
        "submission_id": sub["id"],
        "denial_code": "96",
        "notes": "Non-covered charge (CARC 96)",
    }, timeout=15)
    row = _find_claim_in_queue(s, "rejected", claim["id"])
    assert row is not None
    assert row["status"] == "rejected"
    # Auto-flagged for follow-up as well.
    assert row["followup_flag"] is True


# ---------------------------------------------------------------------------
# 10. Paid / closed claim is NOT flipped by a late-arriving 277CA
# ---------------------------------------------------------------------------
def test_already_paid_claim_is_not_reverted_by_late_ack():
    """Status-map gating — once a claim is in `paid` / `closed` an
    inbound 277CA rejection ingests as a report + event but never
    flips the claim back. This keeps the operator in control."""
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    # Advance submitted → accepted via a 277CA accept, then accepted
    # → paid via the Phase 4 outcome endpoint.
    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca", "status": "accepted",
        "claim_id": claim["id"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    assert _claim_status(s, claim["id"]) == "accepted"
    r = s.post(
        f"{API}/billing/claims/{claim['id']}/submissions/{sub['id']}/outcome",
        json={"outcome": "paid", "paid_cents": 5500}, timeout=15,
    )
    assert r.status_code in (200, 201), r.text
    assert _claim_status(s, claim["id"]) == "paid"

    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "rejected",
        "claim_id": claim["id"],
        "notes": "Late-arriving rejection",
    }, timeout=15)
    assert r.status_code == 201, r.text
    # Claim status must remain `paid`.
    assert _claim_status(s, claim["id"]) == "paid"


# ---------------------------------------------------------------------------
# 11. Hash/receipt — raw content is persisted deterministically
# ---------------------------------------------------------------------------
def test_raw_content_sha256_persisted_on_report_row():
    s = _login()
    claim, sub = _seed_submitted_claim(s)
    raw = "ST*277*0001~BHT*0085*08*12345*20260422*1030*TH~"
    r = s.post(f"{API}/billing/clearinghouse/reports/ingest", json={
        "clearinghouse": "change_healthcare",
        "report_type": "277ca",
        "status": "accepted",
        "claim_id": claim["id"],
        "raw_content": raw,
    }, timeout=15)
    assert r.status_code == 201
    import hashlib
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # Fetch directly from Mongo — the `raw_hash` field is stored on
    # the report row but not exposed on the public model.
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _fetch():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            return await c[os.environ["DB_NAME"]].clearinghouse_reports.find_one(
                {"id": r.json()["id"]}, {"_id": 0},
            )
        finally:
            c.close()

    row = asyncio.run(_fetch())
    assert row["raw_hash"] == expected
    assert row["raw_content"] == raw
