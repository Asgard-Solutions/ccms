"""
Iteration 12 — Authorization system (RBAC + scopes + policy overlays).

Uses `requests` like existing iteration tests (no asyncio harness).
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _session_for(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _reauth(session, password):
    r = session.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 1. Seed + matrix basics
# ---------------------------------------------------------------------------

def test_matrix_shape():
    s = _session_for(*ADMIN)
    r = s.get(f"{API}/authz/matrix", timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["roles"]) == 11
    assert len(body["permissions"]) >= 110
    assert len(body["grants_by_role"]) == 11


def test_roles_catalogue():
    s = _session_for(*ADMIN)
    r = s.get(f"{API}/authz/roles", timeout=10)
    assert r.status_code == 200
    keys = {r["key"] for r in r.json()}
    for required in [
        "super_admin", "org_owner", "compliance_officer", "clinic_manager",
        "front_desk", "provider", "clinical_staff", "billing_specialist",
        "auditor", "patient_portal", "integration_account",
    ]:
        assert required in keys


def test_permissions_catalogue():
    s = _session_for(*ADMIN)
    r = s.get(f"{API}/authz/permissions", timeout=10)
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()}
    for k in [
        "patient.read", "patient_chart.read", "soap_note.sign",
        "audit_log.read", "audit_log.export", "role.assign",
        "api_key.rotate", "break_glass.activate",
        "privacy_request.fulfill_export", "reporting.export_phi",
        "patient.hard_delete",
    ]:
        assert k in keys, f"missing {k}"


# ---------------------------------------------------------------------------
# 2. /authz/me/permissions for legacy users (dual-run shim)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("creds,expected_role_key", [
    (ADMIN, "super_admin"),
    (DOCTOR, "provider"),
    (STAFF, "clinical_staff"),
    (PATIENT, "patient_portal"),
])
def test_me_permissions_for_legacy_roles(creds, expected_role_key):
    s = _session_for(*creds)
    r = s.get(f"{API}/authz/me/permissions", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert expected_role_key in data["role_keys"]
    assert len(data["permissions"]) > 0
    if expected_role_key == "patient_portal":
        for p in data["permissions"]:
            assert p["scope"] == "self", p


# ---------------------------------------------------------------------------
# 3. require_permission() — default-deny paths
# ---------------------------------------------------------------------------

def test_patient_cannot_list_roles():
    s = _session_for(*PATIENT)
    r = s.get(f"{API}/authz/roles", timeout=10)
    assert r.status_code == 403


def test_doctor_cannot_read_privileged_report():
    s = _session_for(*DOCTOR)
    r = s.get(f"{API}/access/reports/privileged-users", timeout=10)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 4. MFA gate
# ---------------------------------------------------------------------------

def test_admin_audit_report_requires_mfa():
    s = _session_for(*ADMIN)
    r = s.get(f"{API}/access/reports/access-review", timeout=10)
    assert r.status_code == 401


def test_admin_audit_report_ok_with_reauth():
    s = _session_for(*ADMIN)
    _reauth(s, ADMIN[1])
    r = s.get(f"{API}/access/reports/access-review", timeout=10)
    assert r.status_code == 200
    body = r.json()
    assert "users" in body and "phi_reads_7d" in body


# ---------------------------------------------------------------------------
# 5. Elevation — separation of duties
# ---------------------------------------------------------------------------

def test_elevation_flow_and_separation_of_duties():
    doc = _session_for(*DOCTOR)
    admin = _session_for(*ADMIN)
    r = doc.post(f"{API}/authz/elevation/request", json={
        "permission_key": "reporting.export_phi",
        "reason": "Quarterly HIPAA audit preparation — case #12",
        "ttl_minutes": 30,
    }, timeout=10)
    assert r.status_code == 201, r.text
    req = r.json()

    _reauth(admin, ADMIN[1])
    r = admin.post(
        f"{API}/authz/elevation/{req['id']}/decision",
        json={"decision": "approve", "reason": "OK"},
        timeout=10,
    )
    assert r.status_code == 200

    # Admin self-approval rejected
    r = admin.post(f"{API}/authz/elevation/request", json={
        "permission_key": "audit_log.export",
        "reason": "Separation-of-duties self-approval test",
        "ttl_minutes": 15,
    }, timeout=10)
    assert r.status_code == 201
    self_req = r.json()
    r = admin.post(
        f"{API}/authz/elevation/{self_req['id']}/decision",
        json={"decision": "approve"},
        timeout=10,
    )
    assert r.status_code == 400
    assert "Separation" in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# 6. Role assign / revoke + session epoch bump
# ---------------------------------------------------------------------------

def test_role_assign_and_revoke():
    admin = _session_for(*ADMIN)
    _reauth(admin, ADMIN[1])
    email = f"au-test-{uuid.uuid4().hex[:8]}@ccms.app"
    r = admin.post(f"{API}/auth/users", json={
        "email": email,
        "password": "Testing@ComplianceClinic1",
        "name": "Role Test User",
        "role": "staff",
        "phone": None,
    }, timeout=10)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]

    r = admin.post(f"{API}/authz/users/{uid}/roles", json={"role_key": "auditor"}, timeout=10)
    assert r.status_code == 200
    assert r.json().get("sessions_revoked") is True

    r = admin.delete(f"{API}/authz/users/{uid}/roles/auditor", timeout=10)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 7. Reporting endpoints
# ---------------------------------------------------------------------------

def test_reports_smoke():
    s = _session_for(*ADMIN)
    _reauth(s, ADMIN[1])
    for path in [
        "/access/reports/users-by-role",
        "/access/reports/permissions-by-role",
        "/access/reports/privileged-users",
        "/access/reports/recent-role-changes",
        "/access/reports/phi-access-history",
        "/access/reports/export-history",
        "/access/reports/break-glass-history",
        "/access/reports/failed-authz",
        "/access/reports/access-review",
    ]:
        r = s.get(f"{API}{path}", timeout=10)
        assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"


# ---------------------------------------------------------------------------
# 8. Denial is audited
# ---------------------------------------------------------------------------

def test_denied_is_audited():
    pp = _session_for(*PATIENT)
    r = pp.get(f"{API}/authz/roles", timeout=10)
    assert r.status_code == 403

    admin = _session_for(*ADMIN)
    _reauth(admin, ADMIN[1])
    r = admin.get(f"{API}/audit-logs?action=authz.denied&limit=10", timeout=10)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert any(row.get("action") == "authz.denied" for row in rows)
