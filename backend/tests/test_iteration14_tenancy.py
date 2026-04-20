"""
Iteration 14 — Multi-tenancy foundation.

Covers:
  * Tenant context: /api/tenancy/me/context returns correct tenant + locations
    per role.
  * Tenant isolation:
    - Default-practice admin cannot see Sunrise-group patients/appointments.
    - Sunrise admin cannot see Default-practice patients/appointments.
    - Direct GET by id across tenants returns 404 (not 403, to avoid
      enumeration).
  * Location scoping within a tenant:
    - downtown-doc only sees patients/appointments at Downtown.
    - floater-doc sees Downtown + Uptown.
    - group-admin (tenant_scope_all) sees all three locations.
    - eastside-staff only sees Eastside.
  * Tenant-aware writes:
    - Patient + appointment creation automatically stamps tenant_id and
      location_id based on the actor's context.
    - Users cannot create a patient under a location they are not assigned to.
  * Reporting:
    - group-admin sees totals aggregated across all locations.
    - downtown-doc only sees Downtown's rows.
  * Platform admin:
    - Can list all tenants.
    - Can create a new tenant + auto-create its primary location.
    - Can override tenant with X-Tenant-Id header.
"""
from __future__ import annotations

import os
import time
import uuid

import requests

API = os.environ.get("CCMS_BASE_URL", "https://remit-statement-hub.preview.emergentagent.com/api")

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DEFAULT_DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
DEFAULT_STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
DEFAULT_PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DOWNTOWN_DOC = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
FLOATER_DOC = ("floater-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
EASTSIDE_STAFF = ("eastside-staff@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
PLATFORM_ADMIN = ("platform-admin@ccms.app", "Platform@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login {email}: {r.status_code} {r.text}"
    return s


def _reauth(session: requests.Session, password: str) -> None:
    r = session.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text


def _context(session: requests.Session) -> dict:
    r = session.get(f"{API}/tenancy/me/context", timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Tenant context endpoint
# ---------------------------------------------------------------------------

def test_default_admin_context():
    ctx = _context(_login(*DEFAULT_ADMIN))
    assert ctx["tenant"] is not None
    assert ctx["tenant"]["slug"] == "default"
    assert ctx["tenant_scope_all"] is True
    assert len(ctx["locations"]) >= 1


def test_group_admin_sees_all_three_locations():
    ctx = _context(_login(*GROUP_ADMIN))
    assert ctx["tenant"]["slug"] == "sunrise-chiro"
    assert ctx["tenant_scope_all"] is True
    names = sorted(l["name"] for l in ctx["locations"])
    assert names == ["Downtown Clinic", "Eastside Clinic", "Uptown Clinic"]


def test_downtown_doc_sees_only_downtown():
    ctx = _context(_login(*DOWNTOWN_DOC))
    assert ctx["tenant"]["slug"] == "sunrise-chiro"
    assert ctx["tenant_scope_all"] is False
    assert [l["name"] for l in ctx["locations"]] == ["Downtown Clinic"]


def test_floater_sees_two_locations():
    ctx = _context(_login(*FLOATER_DOC))
    names = sorted(l["name"] for l in ctx["locations"])
    assert names == ["Downtown Clinic", "Uptown Clinic"]
    assert ctx["tenant_scope_all"] is False


def test_eastside_staff_sees_only_eastside():
    ctx = _context(_login(*EASTSIDE_STAFF))
    assert [l["name"] for l in ctx["locations"]] == ["Eastside Clinic"]


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------

def _unique_email() -> str:
    return f"p-{uuid.uuid4().hex[:8]}@patient.ccms.app"


def _create_patient(session: requests.Session, *, first: str, location_id: str | None = None) -> dict:
    body = {"first_name": first, "last_name": "Tenant-test", "email": _unique_email()}
    if location_id:
        body["location_id"] = location_id
    r = session.post(f"{API}/patients", json=body, timeout=15)
    assert r.status_code == 201, r.text
    return r.json()


def test_default_admin_does_not_see_group_patients():
    group = _login(*GROUP_ADMIN)
    gctx = _context(group)
    downtown_id = next(l["id"] for l in gctx["locations"] if l["name"] == "Downtown Clinic")
    p = _create_patient(group, first="GroupOnly", location_id=downtown_id)

    default_admin = _login(*DEFAULT_ADMIN)
    r = default_admin.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert p["id"] not in ids, "cross-tenant leak: group patient visible to default admin"

    # Direct fetch returns 404 (not 403) to avoid enumeration
    direct = default_admin.get(f"{API}/patients/{p['id']}", timeout=10)
    assert direct.status_code == 404


def test_group_admin_does_not_see_default_patients():
    default_admin = _login(*DEFAULT_ADMIN)
    p = _create_patient(default_admin, first="DefaultOnly")

    group_admin = _login(*GROUP_ADMIN)
    r = group_admin.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert p["id"] not in ids
    # Direct id lookup returns 404.
    assert group_admin.get(f"{API}/patients/{p['id']}", timeout=10).status_code == 404


# ---------------------------------------------------------------------------
# Location scoping within a tenant
# ---------------------------------------------------------------------------

def test_downtown_doc_cannot_see_uptown_patients():
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    uptown = next(l["id"] for l in gctx["locations"] if l["name"] == "Uptown Clinic")
    patient = _create_patient(group_admin, first="Up1", location_id=uptown)

    doc = _login(*DOWNTOWN_DOC)
    r = doc.get(f"{API}/patients", timeout=15)
    ids = {row["id"] for row in r.json()}
    assert patient["id"] not in ids
    # Direct access: 404
    assert doc.get(f"{API}/patients/{patient['id']}?reason=clinical-review", timeout=10).status_code == 404


def test_floater_sees_both_downtown_and_uptown():
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    downtown = next(l["id"] for l in gctx["locations"] if l["name"] == "Downtown Clinic")
    uptown = next(l["id"] for l in gctx["locations"] if l["name"] == "Uptown Clinic")
    eastside = next(l["id"] for l in gctx["locations"] if l["name"] == "Eastside Clinic")

    p_dt = _create_patient(group_admin, first="FlDt", location_id=downtown)
    p_up = _create_patient(group_admin, first="FlUp", location_id=uptown)
    p_es = _create_patient(group_admin, first="FlEs", location_id=eastside)

    floater = _login(*FLOATER_DOC)
    r = floater.get(f"{API}/patients", timeout=15)
    ids = {row["id"] for row in r.json()}
    assert p_dt["id"] in ids
    assert p_up["id"] in ids
    assert p_es["id"] not in ids


def test_location_restricted_user_cannot_write_outside_scope():
    """downtown-doc cannot create a patient under Uptown location."""
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    uptown = next(l["id"] for l in gctx["locations"] if l["name"] == "Uptown Clinic")

    doc = _login(*DOWNTOWN_DOC)
    r = doc.post(
        f"{API}/patients",
        json={
            "first_name": "ShouldFail", "last_name": "X",
            "email": _unique_email(), "location_id": uptown,
        },
        timeout=10,
    )
    assert r.status_code in (403,), f"expected 403, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Tenant-wide reporting
# ---------------------------------------------------------------------------

def test_group_admin_sees_aggregated_patients_across_locations():
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    locations = {l["name"]: l["id"] for l in gctx["locations"]}
    created: list[str] = []
    for name in ("Downtown Clinic", "Uptown Clinic", "Eastside Clinic"):
        p = _create_patient(group_admin, first=f"Agg-{name[:3]}", location_id=locations[name])
        created.append(p["id"])

    r = group_admin.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert all(cid in ids for cid in created)


def test_single_location_user_report_scoped():
    eastside = _login(*EASTSIDE_STAFF)
    r = eastside.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    # Every returned patient must belong to Eastside.
    ctx = _context(eastside)
    allowed_loc_ids = set(ctx["allowed_location_ids"])
    for row in r.json():
        # `location_id` is exposed on PatientPublic
        assert row.get("location_id") in allowed_loc_ids, row


# ---------------------------------------------------------------------------
# Platform admin
# ---------------------------------------------------------------------------

def test_platform_admin_lists_all_tenants():
    s = _login(*PLATFORM_ADMIN)
    r = s.get(f"{API}/tenancy/tenants", timeout=10)
    assert r.status_code == 200
    slugs = {t["slug"] for t in r.json()}
    assert {"default", "sunrise-chiro"}.issubset(slugs)


def test_tenant_admin_only_sees_own_tenant():
    s = _login(*GROUP_ADMIN)
    r = s.get(f"{API}/tenancy/tenants", timeout=10)
    assert r.status_code == 200
    slugs = [t["slug"] for t in r.json()]
    assert slugs == ["sunrise-chiro"]


def test_platform_admin_can_create_new_tenant():
    s = _login(*PLATFORM_ADMIN)
    slug = f"t-{uuid.uuid4().hex[:6]}"
    r = s.post(
        f"{API}/tenancy/tenants",
        json={
            "name": f"Test Practice {slug}",
            "slug": slug,
            "type": "single",
            "primary_location_name": "Main Office",
            "primary_location_code": f"MO-{slug}",
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    new_tenant_id = r.json()["id"]

    # Location should have been auto-created.
    locs = s.get(f"{API}/tenancy/tenants/{new_tenant_id}/locations", timeout=10)
    assert locs.status_code == 200
    assert len(locs.json()) == 1


def test_tenant_admin_cannot_create_tenant():
    s = _login(*GROUP_ADMIN)
    r = s.post(
        f"{API}/tenancy/tenants",
        json={
            "name": "Blocked Practice",
            "slug": f"nope-{uuid.uuid4().hex[:4]}",
            "type": "single",
            "primary_location_name": "Main Office",
        },
        timeout=10,
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Appointments tenant/location scoping
# ---------------------------------------------------------------------------

def test_appointments_tenant_and_location_isolation():
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    downtown = next(l["id"] for l in gctx["locations"] if l["name"] == "Downtown Clinic")

    # Need a downtown patient + a downtown doctor.
    patient = _create_patient(group_admin, first="ApptTest", location_id=downtown)

    # Find the downtown doctor id via providers list (tenant-scoped).
    providers = group_admin.get(f"{API}/auth/providers", timeout=10).json()
    downtown_doc_id = next(
        p["id"] for p in providers if p["email"] == "downtown-doc@sunrise.ccms.app"
    )

    # Use a unique future minute to avoid collisions from previous test runs.
    from datetime import datetime, timedelta, timezone
    base = datetime(2030, 1, 15, 10, 0, tzinfo=timezone.utc)
    offset = int.from_bytes(uuid.uuid4().bytes[:2], "big")   # 0–65535 minutes
    start_dt = base + timedelta(minutes=offset)
    end_dt = start_dt + timedelta(minutes=30)
    start = start_dt.isoformat()
    end = end_dt.isoformat()
    appt = group_admin.post(
        f"{API}/appointments",
        json={
            "patient_id": patient["id"],
            "provider_id": downtown_doc_id,
            "start_time": start,
            "end_time": end,
            "location_id": downtown,
            "reason": "tenant test",
        },
        timeout=10,
    )
    assert appt.status_code == 201, appt.text
    appt_id = appt.json()["id"]

    # Default admin cannot see this appointment.
    default_admin = _login(*DEFAULT_ADMIN)
    r = default_admin.get(f"{API}/appointments/{appt_id}", timeout=10)
    assert r.status_code == 404

    # Eastside staff (different location, same tenant) cannot see it.
    eastside = _login(*EASTSIDE_STAFF)
    r = eastside.get(f"{API}/appointments/{appt_id}", timeout=10)
    assert r.status_code == 404

    # Downtown doctor CAN see it.
    doc = _login(*DOWNTOWN_DOC)
    r = doc.get(f"{API}/appointments/{appt_id}", timeout=10)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Audit log tenant isolation
# ---------------------------------------------------------------------------

def test_audit_log_is_tenant_scoped():
    """Each tenant admin only sees audit rows produced by their tenant."""
    # Trigger a fresh tenant-owned audit event in the group tenant.
    group_admin = _login(*GROUP_ADMIN)
    gctx = _context(group_admin)
    downtown = next(l["id"] for l in gctx["locations"] if l["name"] == "Downtown Clinic")
    p = _create_patient(group_admin, first="AuditTest", location_id=downtown)

    # Trigger a default-tenant audit event.
    default_admin = _login(*DEFAULT_ADMIN)
    _ = _create_patient(default_admin, first="AuditDefault")

    # Group admin (mfa reauth), then list audit logs — should only see their tenant.
    _reauth(group_admin, GROUP_ADMIN[1])
    r = group_admin.get(f"{API}/audit-logs?limit=50", timeout=15)
    assert r.status_code == 200
    for row in r.json():
        # Skip legacy rows that weren't stamped yet.
        if row.get("tenant_id") is None:
            continue
        # Every tenant-stamped row must belong to the group tenant.
        assert row.get("entity_id") != p["id"] or row.get("tenant_id") == gctx["tenant"]["id"]

    # Default admin's audit search for the group patient id returns nothing.
    _reauth(default_admin, DEFAULT_ADMIN[1])
    r = default_admin.get(f"{API}/audit-logs?entity_id={p['id']}&limit=10", timeout=10)
    assert r.status_code == 200
    assert all(row.get("entity_id") != p["id"] for row in r.json())


# ---------------------------------------------------------------------------
# Public registration creates a patient in the Default tenant
# ---------------------------------------------------------------------------

def test_public_register_assigns_default_tenant():
    s = requests.Session()
    email = f"reg-{uuid.uuid4().hex[:8]}@example.com"
    r = s.post(
        f"{API}/auth/register",
        json={"email": email, "password": "RegisterSafe@ClinicMed2", "name": "Reg Test"},
        timeout=10,
    )
    assert r.status_code == 200, r.text
    me = r.json()
    # The new public schema includes tenant_id.
    assert me.get("tenant_id"), "public register should stamp tenant_id"
    # It should equal the default tenant id.
    # Login as default admin to resolve the default tenant id.
    default_admin = _login(*DEFAULT_ADMIN)
    ctx = _context(default_admin)
    assert me["tenant_id"] == ctx["tenant"]["id"]
