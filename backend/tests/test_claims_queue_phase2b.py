"""
Phase 2b — Claims queue enhancements.

Covers:
  * `needs-fixes` named queue returns only `validation_failed` claims.
  * Existing `pending-submission` / `rejected` / `follow-up` queues
    continue to work unchanged.
  * Queue rows include the `last_event` / `last_event_at` fields
    sourced from the `claim_events` stream.
  * Unknown queue name still returns 404.
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers (shared with phase2a test; duplicated here to keep files standalone)
# ---------------------------------------------------------------------------
def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


def _seed_claim_with_status(s: requests.Session, status_target: str) -> dict:
    """Create a patient + payer + insurance + claim in the tenant.

    Returns the fresh claim dict. `status_target` is one of
    `{"draft", "validation_failed"}`; other targets require a running
    scrubber pass or submission — out of scope for this test.
    """
    # Payer
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"Queue Test Payer {uuid.uuid4().hex[:8]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15).json()

    # Patient
    pt = s.post(f"{API}/patients", json={
        "first_name": "Queue", "last_name": f"Test{uuid.uuid4().hex[:6]}",
        "date_of_birth": "1990-01-01",
        "email": f"queue-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()

    # Policy
    pol = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": pt["id"],
        "payer_id": payer["id"],
        "rank": "primary",
        "subscriber_name": "Queue Test",
        "relationship_to_subscriber": "self",
        "member_id": f"MEM-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()

    # Claim — intentionally crafted with an invalid place_of_service so
    # the scrubber flags it as `validation_failed`.
    pos = "" if status_target == "validation_failed" else "11"
    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": pt["id"],
        "payer_id": payer["id"],
        "policy_id": pol["id"],
        "claim_type": "professional",
        "place_of_service": pos or None,
        "frequency_code": "1",
        "billing_provider_id": "BP-TEST",
        "rendering_provider_id": "RP-TEST",
        "service_date_from": "2026-04-01",
        "service_date_to": "2026-04-01",
        "diagnoses": [{"sequence": 1, "code": "M54.5"}],
        "lines": [{
            "sequence": 1,
            "service_date": "2026-04-01",
            "code_type": "cpt",
            "code": "98940",
            "units": 1,
            "billed_cents": 5500,
            "diagnosis_pointers": [1],
        }],
    }, timeout=15).json()

    if status_target == "validation_failed":
        # Trigger the scrubber so the status transitions to
        # validation_failed (the scrubber will fail on missing POS).
        s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)

    fresh = s.get(
        f"{API}/billing/claims/{claim['id']}/detail", timeout=10,
    ).json()
    return fresh["claim"]


# ---------------------------------------------------------------------------
# Needs Fixes queue
# ---------------------------------------------------------------------------
def test_needs_fixes_queue_returns_validation_failed_claims():
    s = _login(*ADMIN)
    claim = _seed_claim_with_status(s, "validation_failed")
    assert claim["status"] == "validation_failed", claim

    r = s.get(f"{API}/billing/claims/queues/needs-fixes", timeout=15)
    assert r.status_code == 200, r.text
    rows = r.json()
    # The claim we just failed must show up.
    ids = {row["id"] for row in rows}
    assert claim["id"] in ids
    # Every row must be validation_failed — the queue is canonical.
    for row in rows:
        assert row["status"] == "validation_failed", row


def test_needs_fixes_queue_excludes_ready_claims():
    """The Needs Fixes queue must NOT include `ready` claims — those
    belong to Pending submission."""
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/claims/queues/needs-fixes", timeout=15)
    assert r.status_code == 200, r.text
    rows = r.json()
    for row in rows:
        assert row["status"] != "ready", row


# ---------------------------------------------------------------------------
# Row enrichment — last_event / last_event_at
# ---------------------------------------------------------------------------
def test_queue_rows_include_last_event_fields():
    s = _login(*ADMIN)
    claim = _seed_claim_with_status(s, "validation_failed")

    r = s.get(f"{API}/billing/claims/queues/needs-fixes", timeout=15)
    rows = r.json()
    row = next((x for x in rows if x["id"] == claim["id"]), None)
    assert row is not None, "seeded claim not in queue"
    # Created + validated events were emitted in Phase 2a. The most
    # recent should be `validated` (we ran the scrubber after create).
    assert "last_event" in row
    assert "last_event_at" in row
    assert row["last_event"] in ("validated", "created"), row["last_event"]
    assert row["last_event_at"] is not None


def test_claim_events_endpoint_returns_created_and_validated():
    """The per-claim event stream must reflect both creation and the
    subsequent validation run."""
    s = _login(*ADMIN)
    claim = _seed_claim_with_status(s, "validation_failed")

    r = s.get(f"{API}/billing/claims/{claim['id']}/events", timeout=10)
    assert r.status_code == 200, r.text
    events = r.json()
    types = {e["event_type"] for e in events}
    assert "created" in types, types
    assert "validated" in types, types


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------
def test_existing_queues_still_work():
    s = _login(*ADMIN)
    for qname in ("pending-submission", "rejected", "follow-up"):
        r = s.get(f"{API}/billing/claims/queues/{qname}", timeout=15)
        assert r.status_code == 200, (qname, r.text)
        rows = r.json()
        # Shape invariants — every row carries the enrichment fields
        # (may be null for queues without any prior events).
        for row in rows[:5]:
            assert "last_event" in row
            assert "last_event_at" in row


def test_unknown_queue_returns_404():
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/claims/queues/does-not-exist", timeout=10)
    assert r.status_code == 404, r.text
