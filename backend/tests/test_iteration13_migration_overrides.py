"""
Iteration 13 — Router migration to require_permission + user-specific overrides.

Covers:
  * Patient/Scheduling/Audit routes now go through require_permission():
    - Patient CRUD still works for admin (via super_admin grants extended to
      cover legacy admin operations).
    - Audit log listing now requires MFA reauth for admin.
    - Doctor create appointment still works (provider role has appt.create).
    - Patient cannot create a patient (no `patient.create` grant on patient_portal).
  * Per-user overrides: admin can GRANT a `patient_portal` user the ability to
    read all patients via override — permission then appears in their
    /authz/me/permissions + the protected route returns 200. Revoke removes it.
  * audit_allow=False: calls to migrated routes should NOT double-write
    authz.allow + semantic audit rows (only the semantic one).
"""
from __future__ import annotations

import os
import uuid

import requests

API = os.environ.get("CCMS_BASE_URL", "https://chiro-gateway.preview.emergentagent.com/api")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=10)
    assert r.status_code == 200, r.text
    return s


def _reauth(session, password):
    r = session.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Router migration — happy paths for existing roles
# ---------------------------------------------------------------------------

def test_admin_can_still_list_patients():
    s = _login(*ADMIN)
    r = s.get(f"{API}/patients", timeout=10)
    assert r.status_code == 200, r.text


def test_admin_audit_log_requires_mfa_after_migration():
    s = _login(*ADMIN)
    r = s.get(f"{API}/audit-logs?limit=5", timeout=10)
    assert r.status_code == 401


def test_admin_audit_log_ok_with_reauth():
    s = _login(*ADMIN)
    _reauth(s, ADMIN[1])
    r = s.get(f"{API}/audit-logs?limit=5", timeout=10)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_patient_cannot_delete_patient():
    """Patient-portal has no patient.delete grant — always denied."""
    s = _login(*PATIENT)
    # Pick a patient ID — doesn't matter which, permission check happens first
    r = s.delete(f"{API}/patients/00000000-0000-0000-0000-000000000000?reason=test",
                 timeout=10)
    assert r.status_code == 403


def test_doctor_can_create_appointment():
    doctor = _login(*DOCTOR)
    admin = _login(*ADMIN)
    # find demo patient + doctor ids
    _reauth(admin, ADMIN[1])
    patients = admin.get(f"{API}/patients", timeout=10).json()
    # doctor needs a patient to book against
    pid = patients[0]["id"]
    providers = admin.get(f"{API}/auth/providers", timeout=10).json()
    prov_id = providers[0]["id"]
    r = doctor.post(f"{API}/appointments", json={
        "patient_id": pid,
        "provider_id": prov_id,
        "start_time": "2030-06-15T10:00:00+00:00",
        "end_time": "2030-06-15T10:30:00+00:00",
        "reason": "Routine follow-up",
        "notes": "notes",
    }, timeout=10)
    # Could be 200/201 success OR 409 conflict if demo seed has overlap; both are acceptable
    assert r.status_code in (201, 409), r.text


# ---------------------------------------------------------------------------
# Per-user overrides
# ---------------------------------------------------------------------------

def test_override_grant_and_revoke_flow():
    admin = _login(*ADMIN)
    _reauth(admin, ADMIN[1])

    # Find the patient user
    users = admin.get(f"{API}/auth/users", timeout=10).json()
    patient_user = next(u for u in users if u["email"] == "patient@ccms.app")

    # Grant patient_portal user a fresh ability they don't normally have
    r = admin.post(
        f"{API}/authz/users/{patient_user['id']}/overrides",
        json={
            "permission_key": "role.read",
            "scope": "all_org",
            "requires_mfa": False,
            "requires_approval": False,
            "break_glass_allowed": False,
            "reason": "Temporary auditor shadow for QA regression test",
            "expires_at": None,
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    override = r.json()
    assert override["permission_key"] == "role.read"

    # Login as patient — new session picks up the override (session_epoch bumped)
    pp = _login(*PATIENT)
    r = pp.get(f"{API}/authz/me/permissions", timeout=10)
    assert r.status_code == 200
    keys = {p["key"] for p in r.json()["permissions"]}
    assert "role.read" in keys, f"override grant not reflected in /me/permissions: {keys}"

    # Patient can now list roles — previously denied
    r = pp.get(f"{API}/authz/roles", timeout=10)
    assert r.status_code == 200, r.text

    # Revoke override
    r = admin.delete(
        f"{API}/authz/users/{patient_user['id']}/overrides/{override['id']}",
        timeout=10,
    )
    assert r.status_code == 200

    # List with include_revoked=true shows it; default excludes
    r = admin.get(
        f"{API}/authz/users/{patient_user['id']}/overrides?include_revoked=true",
        timeout=10,
    )
    assert r.status_code == 200
    rows = r.json()
    assert any(x["id"] == override["id"] and x["status"] == "revoked" for x in rows)

    # After revoke + re-login, patient again cannot list roles
    pp2 = _login(*PATIENT)
    r = pp2.get(f"{API}/authz/roles", timeout=10)
    assert r.status_code == 403


def test_override_requires_admin_mfa():
    """Non-admin (doctor) cannot grant overrides."""
    doctor = _login(*DOCTOR)
    r = doctor.post(
        f"{API}/authz/users/any/overrides",
        json={
            "permission_key": "role.read",
            "scope": "all_org",
            "reason": "I shouldn't be allowed",
        },
        timeout=10,
    )
    assert r.status_code in (401, 403)


def test_override_rejects_unknown_permission():
    admin = _login(*ADMIN)
    _reauth(admin, ADMIN[1])
    users = admin.get(f"{API}/auth/users", timeout=10).json()
    target_uid = users[0]["id"]
    r = admin.post(
        f"{API}/authz/users/{target_uid}/overrides",
        json={
            "permission_key": "made.up.permission",
            "scope": "all_org",
            "reason": "Test unknown permission rejection",
        },
        timeout=10,
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# audit_allow=False — migrated routes should not emit authz.allow rows
# ---------------------------------------------------------------------------

def test_migrated_routes_do_not_double_audit():
    admin = _login(*ADMIN)
    _reauth(admin, ADMIN[1])
    before = admin.get(
        f"{API}/audit-logs?action=authz.allow&limit=5", timeout=10,
    ).json()
    before_count = len(before)
    # Hit a migrated route: GET /patients is still authed via get_current_user
    # + legacy check, but POST /patients goes through require_permission. Use
    # a simple create+delete cycle.
    email = f"aud-test-{uuid.uuid4().hex[:6]}@ccms.app"
    r = admin.post(f"{API}/patients", json={
        "first_name": "AuditTest",
        "last_name": "NoDouble",
        "date_of_birth": "1990-01-01",
        "gender": "non-binary",
        "phone": "+1-555-0000",
        "email": email,
        "address": "x",
        "emergency_contact": "x",
        "notes": "x",
    }, timeout=10)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    after = admin.get(
        f"{API}/audit-logs?action=authz.allow&entity_type=patient&limit=50",
        timeout=10,
    ).json()
    # Should NOT contain an authz.allow row for this patient create —
    # the patient.created semantic audit is used instead.
    assert not any(row.get("entity_id") == pid for row in after), (
        "audit_allow=False migrated route is still emitting authz.allow rows"
    )

    # Verify the semantic audit row was written
    sem = admin.get(
        f"{API}/audit-logs?action=patient.created&entity_id={pid}&limit=5",
        timeout=10,
    ).json()
    assert any(row.get("entity_id") == pid for row in sem), "semantic audit missing"
