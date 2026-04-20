"""
Iteration 18 — Compliance-ops backbone.

Covers:
  * Dashboard aggregation with representative seed data.
  * Multi-framework control mapping roundtrip.
  * Evidence integrity hash + legal-hold toggle + tamper-resistance
    (integrity_sha256 and history are not in the allowed patch set).
  * Privilege review overdue flag auto-computed.
  * Incident workflow — status transition appends to history.
  * Tenant isolation — Sunrise items invisible to Default admin.
  * Generic field-patch allow-list rejects unknown fields.
"""
from __future__ import annotations

import os
import time
import uuid

import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    return s


def _reauth(s, password):
    s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)


# ---------------------------------------------------------------------------
# Dashboard + seed fidelity
# ---------------------------------------------------------------------------

def test_dashboard_reflects_seed():
    s = _login(*GROUP_ADMIN)
    d = s.get(f"{API}/compliance-ops/dashboard", timeout=10).json()
    assert d["controls"]["total"] >= 7
    assert d["policies"]["overdue"] >= 1
    assert d["vendors"]["baa_missing"] >= 1
    assert d["access_reviews"]["overdue"] >= 1
    assert d["evidence"]["total"] >= 1


def test_controls_list_filter_by_framework():
    s = _login(*GROUP_ADMIN)
    r = s.get(f"{API}/compliance-ops/controls?framework=HIPAA", timeout=10)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 4  # at least the 4 HIPAA-tagged seed controls
    for row in rows:
        assert "HIPAA" in row["framework_mappings"]


def test_control_multi_framework_mapping_roundtrip():
    s = _login(*GROUP_ADMIN)
    r = s.post(f"{API}/compliance-ops/controls", json={
        "name": f"Test ctrl {uuid.uuid4().hex[:6]}",
        "family": "access_control",
        "description": "Test control for multi-framework mapping.",
        "framework_mappings": {
            "HIPAA": ["164.312(b)"],
            "SOC2": ["CC6.1"],
            "ISO27001": ["A.9.4"],
            "CCPA": ["1798.100"],
        },
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body["framework_mappings"]) == {"HIPAA", "SOC2", "ISO27001", "CCPA"}


# ---------------------------------------------------------------------------
# Evidence integrity + legal hold + tamper resistance
# ---------------------------------------------------------------------------

def test_evidence_integrity_hash_and_legal_hold():
    s = _login(*GROUP_ADMIN)
    # Grab any control.
    ctrl = s.get(f"{API}/compliance-ops/controls", timeout=10).json()[0]
    body = {
        "control_id": ctrl["id"],
        "evidence_type": "audit_log",
        "source_system": "ccms.audit",
        "source_reference": "audit_logs last_30d",
        "content_summary": "snapshot",
        "coverage_period_start": "2026-01-01T00:00:00Z",
        "coverage_period_end": "2026-01-31T23:59:59Z",
    }
    r = s.post(f"{API}/compliance-ops/evidence", json=body, timeout=10)
    assert r.status_code == 201, r.text
    ev = r.json()
    assert len(ev["integrity_sha256"]) == 64
    assert ev["legal_hold"] is False
    # Legal hold requires MFA reauth (uses reporting.export).
    _reauth(s, GROUP_ADMIN[1])
    r = s.post(f"{API}/compliance-ops/evidence/{ev['id']}/legal-hold?on=true", timeout=10)
    assert r.status_code == 200
    assert r.json()["legal_hold"] is True


def test_evidence_patch_rejects_integrity_fields():
    s = _login(*GROUP_ADMIN)
    ev = s.get(f"{API}/compliance-ops/evidence", timeout=10).json()[0]
    # Try to overwrite the hash — should be rejected.
    r = s.patch(
        f"{API}/compliance-ops/evidence/{ev['id']}",
        json={"fields": {"integrity_sha256": "deadbeef"}}, timeout=10,
    )
    assert r.status_code == 400
    assert "not editable" in r.text

    # History also cannot be directly edited.
    r = s.patch(
        f"{API}/compliance-ops/evidence/{ev['id']}",
        json={"fields": {"history": []}}, timeout=10,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------

def test_compliance_is_tenant_scoped():
    default = _login(*DEFAULT_ADMIN)
    group = _login(*GROUP_ADMIN)
    # Create a risk in group tenant.
    r = group.post(f"{API}/compliance-ops/risks", json={
        "title": f"XT risk {uuid.uuid4().hex[:6]}",
        "description": "x", "asset": "A", "threat": "T",
        "vulnerability": "V", "likelihood": 3, "impact": 3,
    }, timeout=10)
    assert r.status_code == 201
    rid = r.json()["id"]

    # Default admin cannot see it.
    rows = default.get(f"{API}/compliance-ops/risks", timeout=10).json()
    assert all(row["id"] != rid for row in rows)


# ---------------------------------------------------------------------------
# Access review overdue auto-flag
# ---------------------------------------------------------------------------

def test_access_review_overdue_auto_flag():
    s = _login(*GROUP_ADMIN)
    rows = s.get(f"{API}/compliance-ops/access-reviews", timeout=10).json()
    # At least one seeded review is overdue (due_at = now-7d, status=scheduled).
    assert any(r["status"] == "overdue" for r in rows)


# ---------------------------------------------------------------------------
# Incident workflow + history append
# ---------------------------------------------------------------------------

def test_incident_status_change_appends_history():
    s = _login(*GROUP_ADMIN)
    _reauth(s, GROUP_ADMIN[1])
    r = s.post(f"{API}/compliance-ops/incidents", json={
        "title": f"Sev-test {uuid.uuid4().hex[:6]}",
        "severity": "high",
        "incident_type": "availability",
        "summary": "Test",
        "detected_at": "2026-02-20T00:00:00Z",
    }, timeout=10)
    assert r.status_code == 201, r.text
    iid = r.json()["id"]

    # Transition triage → investigating.
    r = s.post(f"{API}/compliance-ops/incident/{iid}/status",
               json={"new_status": "investigating", "note": "tabletop"},
               timeout=10)
    assert r.status_code == 200

    # GET by id returns the raw document including history.
    raw = s.get(f"{API}/compliance-ops/incident/{iid}", timeout=10).json()
    assert raw["status"] == "investigating"
    actions = [h["action"] for h in raw.get("history", [])]
    assert "created" in actions
    assert "status_change" in actions


# ---------------------------------------------------------------------------
# Field-patch allow-list rejects unknown entity type
# ---------------------------------------------------------------------------

def test_patch_rejects_unknown_entity_type():
    s = _login(*GROUP_ADMIN)
    r = s.patch(
        f"{API}/compliance-ops/unknown/some-id",
        json={"fields": {"x": 1}}, timeout=10,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Vendor baa_in_place=False auto-flags dashboard
# ---------------------------------------------------------------------------

def test_baa_missing_counted():
    s = _login(*GROUP_ADMIN)
    d = s.get(f"{API}/compliance-ops/dashboard", timeout=10).json()
    # Seeded Twilio vendor has baa_required=True, baa_in_place=False.
    assert d["vendors"]["baa_missing"] >= 1
