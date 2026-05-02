"""Phase 2 compliance-ops UI backbone — direct API tests for the
endpoints that the new Compliance.jsx tabbed UI consumes.

Uses HTTPS preview URL so the secure cookie set on auth/login persists
across requests.
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/frontend/.env")

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE}/api"

ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = False) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    if reauth:
        r2 = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        assert r2.status_code == 200, r2.text
    return s


def test_dashboard_shape_for_ui_tiles():
    s = _login(*ADMIN)
    d = s.get(f"{API}/compliance-ops/dashboard", timeout=10).json()
    for key in ("controls", "risks", "incidents", "policies", "vendors",
                "access_reviews", "privacy_requests", "evidence"):
        assert key in d, f"missing {key}"
    assert "total" in d["controls"]
    assert "open" in d["risks"]
    assert "high_severity_open" in d["risks"]
    assert "open" in d["incidents"]
    assert "overdue" in d["policies"]
    assert "baa_missing" in d["vendors"]
    assert "scheduled" in d["access_reviews"]
    assert "overdue" in d["access_reviews"]
    assert "total" in d["evidence"]
    assert "last_90_days" in d["evidence"]


def test_policy_create_and_status_lifecycle():
    s = _login(*ADMIN)
    name = f"UI Policy {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/compliance-ops/policies", json={
        "name": name, "version": "0.1",
        "summary": "Created via UI tests for the policy register.",
        "effective_date": "2026-02-01T00:00:00Z",
        "review_date": "2027-02-01T00:00:00Z",
    }, timeout=10)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    # Status change draft -> approved.
    r = s.post(f"{API}/compliance-ops/policy/{pid}/status",
               json={"new_status": "approved", "note": "ratified"}, timeout=10)
    assert r.status_code == 200
    assert r.json()["new_status"] == "approved"

    # Raw doc carries history.
    raw = s.get(f"{API}/compliance-ops/policy/{pid}", timeout=10).json()
    actions = [h["action"] for h in raw.get("history", [])]
    assert "created" in actions
    assert "status_change" in actions


def test_risk_create_and_inherent_score_math():
    s = _login(*ADMIN)
    r = s.post(f"{API}/compliance-ops/risks", json={
        "title": f"UI risk {uuid.uuid4().hex[:6]}",
        "description": "x", "asset": "Identity",
        "threat": "credential theft", "vulnerability": "shared admin",
        "likelihood": 4, "impact": 5,
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["inherent_score"] == 20
    assert body["status"] == "open"


def test_evidence_create_seals_integrity_hash():
    s = _login(*ADMIN)
    ctrl = s.get(f"{API}/compliance-ops/controls", timeout=10).json()[0]
    r = s.post(f"{API}/compliance-ops/evidence", json={
        "control_id": ctrl["id"],
        "evidence_type": "manual_upload",
        "source_system": "ui-test",
        "source_reference": f"upload-{uuid.uuid4().hex[:6]}",
        "content_summary": "captured via the new UI evidence form",
        "coverage_period_start": "2026-01-01T00:00:00Z",
        "coverage_period_end": "2026-01-31T23:59:59Z",
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["integrity_sha256"]) == 64
    assert body["legal_hold"] is False
    assert body["control_id"] == ctrl["id"]


def test_evidence_legal_hold_requires_reauth_and_toggles():
    s = _login(*ADMIN, reauth=True)
    rows = s.get(f"{API}/compliance-ops/evidence", timeout=10).json()
    assert rows, "expected seeded evidence rows"
    target = rows[0]
    r = s.post(f"{API}/compliance-ops/evidence/{target['id']}/legal-hold",
               params={"on": "true"}, timeout=10)
    assert r.status_code == 200
    assert r.json()["legal_hold"] is True
    # Toggle off again.
    r = s.post(f"{API}/compliance-ops/evidence/{target['id']}/legal-hold",
               params={"on": "false"}, timeout=10)
    assert r.status_code == 200
    assert r.json()["legal_hold"] is False


def test_access_review_complete_action_pipeline():
    s = _login(*ADMIN)
    r = s.post(f"{API}/compliance-ops/access-reviews", json={
        "name": f"UI AR {uuid.uuid4().hex[:6]}",
        "scope": "tenant_admins",
        "due_at": "2026-12-31T00:00:00Z",
    }, timeout=10)
    assert r.status_code == 201, r.text
    arid = r.json()["id"]

    # Patch with completion fields (mirrors the UI Complete button).
    r = s.patch(f"{API}/compliance-ops/access_review/{arid}",
                json={"fields": {"completed_at": "2026-02-15T00:00:00Z",
                                 "decision": "no_changes"}}, timeout=10)
    assert r.status_code == 200, r.text

    r = s.post(f"{API}/compliance-ops/access_review/{arid}/status",
               json={"new_status": "complete"}, timeout=10)
    assert r.status_code == 200

    raw = s.get(f"{API}/compliance-ops/access_review/{arid}", timeout=10).json()
    assert raw["status"] == "complete"
    assert raw["decision"] == "no_changes"


def test_incident_create_requires_reauth():
    s = _login(*ADMIN, reauth=True)
    r = s.post(f"{API}/compliance-ops/incidents", json={
        "title": f"UI inc {uuid.uuid4().hex[:6]}",
        "severity": "high",
        "incident_type": "phi_exposure",
        "summary": "Detected misconfigured S3 bucket exposed audit logs.",
        "detected_at": "2026-02-15T12:00:00Z",
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["notification_required"] is True  # high severity
    assert body["status"] == "triage"


def test_vendor_create_baa_flag_logic():
    s = _login(*ADMIN)
    # baa_required=True, baa_in_place=False → status under_review.
    r = s.post(f"{API}/compliance-ops/vendors", json={
        "name": f"UI Vendor {uuid.uuid4().hex[:6]}",
        "service_provided": "Audit log archive",
        "data_categories": ["audit logs"],
        "baa_required": True, "baa_in_place": False,
    }, timeout=10)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "under_review"

    # baa_required=False → status active.
    r = s.post(f"{API}/compliance-ops/vendors", json={
        "name": f"UI Vendor {uuid.uuid4().hex[:6]}",
        "service_provided": "Brand monitoring",
        "data_categories": ["public data"],
        "baa_required": False, "baa_in_place": False,
    }, timeout=10)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "active"
