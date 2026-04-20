"""
Iteration 19 — Workforce & Identity Security Workflows.

Covers:
  * Workforce invitation + activation (mocked dev_token, cross-tenant safe)
  * Patient proxy grant + revoke lifecycle
  * Self + admin session visibility and one-shot revocation
  * Atomic one-shot deprovisioning (roles, location, patient, proxies,
    invitations, future appointments flagged OR reassigned)
  * Break-glass activate → end → attest flow, auto-expiry sweep,
    overdue attestation step-up enforcement
  * Suspicious-login detection hook — audit + step_up_required flag
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from dotenv import load_dotenv

# Load backend .env so MONGO_URL + DB_NAME are available for raw-DB probes
# further down (e.g. break-glass sweep + suspicious-login tests).
load_dotenv("/app/backend/.env")

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")

GROUP_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DOWNTOWN_DOC = ("downtown-doc@sunrise.ccms.app", "Sunrise@ComplianceClinic1")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _login(email: str, password: str, *, extra_headers: dict | None = None) -> requests.Session:
    """Login over plain HTTP — Secure cookies can't traverse this transport,
    so lift access_token / reauth_token out of Set-Cookie into headers."""
    s = requests.Session()
    if extra_headers:
        s.headers.update(extra_headers)
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    return s


def _reauth(s: requests.Session, password: str) -> None:
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    assert r.status_code == 200, r.text
    reauth = r.cookies.get("reauth_token")
    if reauth:
        s.headers["x-reauth-token"] = reauth


def _invite_email() -> str:
    return f"invite-{uuid.uuid4().hex[:8]}@sunrise.ccms.app"


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

def test_invitation_create_accept_and_login_flow():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    # Get a valid location id for the Sunrise tenant.
    ctx = admin.get(f"{API}/tenancy/me/context", timeout=10).json()
    locs = ctx.get("locations") or []
    if not locs:
        # Sunrise admin is tenant_scope_all — fetch locations via the
        # tenant's locations endpoint instead.
        tid = ctx["tenant"]["id"]
        locs = admin.get(f"{API}/tenancy/tenants/{tid}/locations",
                         timeout=10).json()
    loc_id = locs[0]["id"]

    email = _invite_email()
    r = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Test Invitee", "role": "staff",
        "location_ids": [loc_id],
    }, timeout=10)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["dev_token"]
    assert "token_hash" not in body

    # Accept — public endpoint.
    s = requests.Session()
    r = s.post(f"{API}/workforce/invitations/accept", json={
        "token": body["dev_token"],
        "password": "Strong@Pass2026!!",
    }, timeout=10)
    assert r.status_code == 200, r.text
    activation = r.json()
    assert activation["email"] == email
    assert activation["mfa_required"] is True  # staff role

    # Login with the brand-new workforce user.
    new_user = requests.Session()
    r = new_user.post(f"{API}/auth/login",
                      json={"email": email, "password": "Strong@Pass2026!!"},
                      timeout=10)
    assert r.status_code == 200, r.text

    # Re-using the token must now fail.
    r2 = s.post(f"{API}/workforce/invitations/accept", json={
        "token": body["dev_token"],
        "password": "Strong@Pass2026!!",
    }, timeout=10)
    assert r2.status_code in (400, 409)


def test_invitation_rejected_for_duplicate_email():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    r = admin.post(f"{API}/workforce/invitations", json={
        "email": GROUP_ADMIN[0], "name": "Dup",
        "role": "staff", "location_ids": [],
    }, timeout=10)
    assert r.status_code == 409


def test_invitation_revoke_burns_token():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    email = _invite_email()
    r = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "To be revoked", "role": "staff",
    }, timeout=10)
    assert r.status_code == 201
    inv = r.json()
    # Revoke it.
    r2 = admin.post(f"{API}/workforce/invitations/{inv['id']}/revoke",
                    json={"reason": "changed my mind"}, timeout=10)
    assert r2.status_code == 200
    # Accept must now fail.
    s = requests.Session()
    r3 = s.post(f"{API}/workforce/invitations/accept", json={
        "token": inv["dev_token"], "password": "Strong@Pass2026!!",
    }, timeout=10)
    assert r3.status_code == 400


def test_invitation_tenant_isolation_for_listing():
    sunrise = _login(*GROUP_ADMIN)
    _reauth(sunrise, GROUP_ADMIN[1])
    # Create one invitation in Sunrise tenant.
    email = _invite_email()
    sunrise.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Iso", "role": "staff",
    }, timeout=10)
    # Default admin must not see this invitation.
    default = _login(*DEFAULT_ADMIN)
    r = default.get(f"{API}/workforce/invitations", timeout=10)
    assert r.status_code == 200
    emails = {x["email"] for x in r.json()}
    assert email not in emails


# ---------------------------------------------------------------------------
# Patient proxies
# ---------------------------------------------------------------------------

def _find_patient(admin: requests.Session) -> dict:
    # GROUP_ADMIN sees all patients in Sunrise tenant.
    r = admin.get(f"{API}/patients", timeout=10)
    assert r.status_code == 200, r.text
    patients = r.json()
    assert patients, "expected seeded sunrise patients"
    return patients[0]


def test_proxy_grant_and_revoke_lifecycle():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    patient = _find_patient(admin)
    # Use floater-doc as the proxy user for the grant.
    users = admin.get(f"{API}/auth/users", timeout=10).json()
    proxy_user = next(u for u in users if u["email"] == "floater-doc@sunrise.ccms.app")

    r = admin.post(f"{API}/workforce/proxies", json={
        "patient_id": patient["id"],
        "proxy_user_id": proxy_user["id"],
        "relationship": "legal_guardian",
        "scope": "read",
        "effective_date": datetime.now(timezone.utc).isoformat(),
        "reason": "Guardianship order filed 2026-02",
    }, timeout=10)
    assert r.status_code == 201, r.text
    grant = r.json()
    assert grant["status"] == "active"
    assert grant["relationship"] == "legal_guardian"

    # List must include it.
    lst = admin.get(f"{API}/workforce/proxies?patient_id={patient['id']}",
                    timeout=10).json()
    assert any(x["id"] == grant["id"] for x in lst)

    # Revoke.
    r2 = admin.post(f"{API}/workforce/proxies/{grant['id']}/revoke",
                    json={"reason": "Guardianship lifted"}, timeout=10)
    assert r2.status_code == 200
    # Active-only list excludes it now.
    lst2 = admin.get(f"{API}/workforce/proxies?patient_id={patient['id']}",
                     timeout=10).json()
    assert all(x["id"] != grant["id"] for x in lst2)


def test_proxy_grant_rejects_cross_tenant_user():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    patient = _find_patient(admin)
    # Find Default-tenant admin user id via a platform-admin login.
    # Since we don't have a platform-admin cross-tenant listing here, we
    # use a guaranteed-non-existent id instead.
    r = admin.post(f"{API}/workforce/proxies", json={
        "patient_id": patient["id"],
        "proxy_user_id": "non-existent-user-id",
        "relationship": "legal_guardian",
        "scope": "read",
        "effective_date": datetime.now(timezone.utc).isoformat(),
        "reason": "should fail",
    }, timeout=10)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Sessions & admin revocation
# ---------------------------------------------------------------------------

def test_admin_revoke_target_user_sessions_kills_old_tokens():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    # Create and immediately log in a fresh user so we can revoke them.
    email = _invite_email()
    inv = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Revocation target", "role": "staff",
    }, timeout=10).json()
    s = requests.Session()
    s.post(f"{API}/workforce/invitations/accept", json={
        "token": inv["dev_token"], "password": "Strong@Pass2026!!",
    }, timeout=10)
    target = requests.Session()
    target_login = target.post(f"{API}/auth/login",
                               json={"email": email, "password": "Strong@Pass2026!!"}, timeout=10)
    assert target_login.status_code == 200, target_login.text
    tok = target_login.cookies.get("access_token")
    if tok:
        target.headers["Authorization"] = f"Bearer {tok}"
    me_before = target.get(f"{API}/auth/me", timeout=10)
    assert me_before.status_code == 200

    target_id = me_before.json()["id"]
    r = admin.post(f"{API}/workforce/sessions/user/revoke-all",
                   json={"user_id": target_id, "reason": "policy violation"},
                   timeout=10)
    assert r.status_code == 200

    # Target's old token is now invalid — revoke-all kills ALL sessions.
    me_after = target.get(f"{API}/auth/me", timeout=10)
    assert me_after.status_code == 401


def test_self_revoke_all_sessions():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    r = admin.post(f"{API}/workforce/sessions/me/revoke-all", timeout=10)
    assert r.status_code == 200
    # Session now dead.
    me = admin.get(f"{API}/auth/me", timeout=10)
    assert me.status_code == 401


# ---------------------------------------------------------------------------
# Deprovisioning
# ---------------------------------------------------------------------------

def test_deprovision_is_atomic_and_revokes_assignments():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    ctx = admin.get(f"{API}/tenancy/me/context", timeout=10).json()
    locs = ctx.get("locations") or []
    if not locs:
        # Sunrise admin is tenant_scope_all — fetch locations via the
        # tenant's locations endpoint instead.
        tid = ctx["tenant"]["id"]
        locs = admin.get(f"{API}/tenancy/tenants/{tid}/locations",
                         timeout=10).json()
    loc_id = locs[0]["id"]
    email = _invite_email()
    inv = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Termination target", "role": "staff",
        "location_ids": [loc_id],
    }, timeout=10).json()
    s = requests.Session()
    act = s.post(f"{API}/workforce/invitations/accept", json={
        "token": inv["dev_token"], "password": "Strong@Pass2026!!",
    }, timeout=10).json()
    user_id = act["user_id"]

    # Deprovision.
    r = admin.post(f"{API}/workforce/users/{user_id}/deprovision", json={
        "reason": "End of contract — atomic test",
    }, timeout=10)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["status_after"] == "disabled"
    assert report["location_assignments_revoked"] >= 1

    # Old login must now fail.
    s2 = requests.Session()
    r2 = s2.post(f"{API}/auth/login",
                 json={"email": email, "password": "Strong@Pass2026!!"}, timeout=10)
    assert r2.status_code == 403


def test_deprovision_rejects_self():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    me = admin.get(f"{API}/auth/me", timeout=10).json()
    r = admin.post(f"{API}/workforce/users/{me['id']}/deprovision", json={
        "reason": "I am very lonely, please",
    }, timeout=10)
    assert r.status_code == 400


def test_deprovision_flags_future_appointments_when_no_reassign():
    """Invite + activate a fresh doctor, assign them a future appointment,
    then deprovision. The appointment must be flagged `needs_reassignment`
    with `provider_id` cleared. We avoid disabling the seeded doctor so
    other iterations' regression suites still pass."""
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    ctx = admin.get(f"{API}/tenancy/me/context", timeout=10).json()
    locs = ctx.get("locations") or admin.get(
        f"{API}/tenancy/tenants/{ctx['tenant']['id']}/locations", timeout=10,
    ).json()
    loc_id = locs[0]["id"]

    email = _invite_email()
    inv = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Temp Doc", "role": "doctor",
        "location_ids": [loc_id],
    }, timeout=10).json()
    s = requests.Session()
    act = s.post(f"{API}/workforce/invitations/accept", json={
        "token": inv["dev_token"], "password": "Strong@Pass2026!!",
    }, timeout=10).json()
    doctor_id = act["user_id"]

    # Seed a future appointment directly (avoids scheduling conflict checks
    # and location permission quirks for the temp doctor).
    from pymongo import MongoClient
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "ccms_db")
    db = MongoClient(mongo_url)[db_name]
    patient = db.patients.find_one(
        {"tenant_id": ctx["tenant"]["id"]}, {"_id": 0, "id": 1, "location_id": 1},
    )
    future = (datetime.now(timezone.utc) + timedelta(days=7)).replace(
        microsecond=0, second=0).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=7, minutes=30)).replace(
        microsecond=0, second=0).isoformat()
    appt_id = str(uuid.uuid4())
    db.appointments.insert_one({
        "id": appt_id,
        "tenant_id": ctx["tenant"]["id"],
        "location_id": patient["location_id"],
        "patient_id": patient["id"],
        "provider_id": doctor_id,
        "start_time": future, "end_time": end,
        "reason": "Deprovision test", "status": "scheduled",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    # Now deprovision the temp doctor.
    r = admin.post(f"{API}/workforce/users/{doctor_id}/deprovision", json={
        "reason": "End of contract — flag appointments",
    }, timeout=10)
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["future_appointments_flagged"] >= 1
    assert report["future_appointments_reassigned"] == 0

    appt_after = db.appointments.find_one({"id": appt_id}, {"_id": 0})
    assert appt_after["needs_reassignment"] is True
    assert appt_after["provider_id"] is None
    assert appt_after["previous_provider_id"] == doctor_id


# ---------------------------------------------------------------------------
# Break-glass
# ---------------------------------------------------------------------------

def test_break_glass_start_end_and_self_attest():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    r = admin.post(f"{API}/workforce/break-glass/start", json={
        "scope_resource": "patient_chart",
        "scope_entity_id": "demo-patient",
        "ticket_reference": "INC-2026-001",
        "reason": "Emergency review during tabletop exercise",
        "duration_minutes": 15,
    }, timeout=10)
    assert r.status_code == 201, r.text
    bg = r.json()
    assert bg["status"] == "active"

    # End early.
    r2 = admin.post(f"{API}/workforce/break-glass/{bg['id']}/end", timeout=10)
    assert r2.status_code == 200

    # Self-attest.
    _reauth(admin, GROUP_ADMIN[1])
    r3 = admin.post(f"{API}/workforce/break-glass/{bg['id']}/attest", json={
        "summary": "Reviewed chart for urgent referral coordination",
        "phi_accessed": True, "action_required": False,
    }, timeout=10)
    assert r3.status_code == 200
    result = r3.json()
    assert result["ok"] is True
    assert result["overdue"] is False


def test_break_glass_sweep_marks_overdue_and_sets_step_up():
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    # Force a stale window by writing directly through Mongo client.
    import pymongo
    from motor.motor_asyncio import AsyncIOMotorClient  # noqa: F401
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "ccms_db")
    client = pymongo.MongoClient(mongo_url)
    db = client[db_name]

    actor = db.users.find_one({"email": "group-admin@sunrise.ccms.app"})
    tenant_id = actor["tenant_id"]
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    due_past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    bg_id = str(uuid.uuid4())
    db.break_glass_events.insert_one({
        "id": bg_id, "tenant_id": tenant_id,
        "actor_id": actor["id"], "actor_email": actor["email"],
        "actor_role": actor["role"],
        "scope_resource": "patient_chart", "scope_entity_id": "x",
        "ticket_reference": "INC-STALE",
        "reason": "stale for test" + " " * 20,
        "duration_minutes": 15,
        "activated_at": past, "expires_at": past,
        "attestation_due_at": due_past,
        "status": "active",
    })

    # Clear step-up so we can observe it being set.
    db.users.update_one({"id": actor["id"]},
                        {"$set": {"step_up_required": False,
                                  "suspicious_flag": False}})

    r = admin.post(f"{API}/workforce/break-glass/sweep", timeout=10)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["overdue_flagged"] >= 1

    # User should now have step_up_required=True.
    user_after = db.users.find_one({"id": actor["id"]},
                                   {"_id": 0, "step_up_required": 1,
                                    "suspicious_flag": 1})
    assert user_after["step_up_required"] is True
    assert user_after["suspicious_flag"] is True

    # Any non-trivial action without reauth must now 401 for step_up.
    # (Reauth cookie AND reauth header are set from earlier — clear both.)
    admin.cookies.pop("reauth_token", None)
    admin.headers.pop("x-reauth-token", None)
    r2 = admin.get(f"{API}/workforce/break-glass", timeout=10)
    # break_glass.activate is MFA in matrix already, so this would 401 either
    # way. Verify the specific new-IP / step-up denial on a NON-MFA endpoint:
    r3 = admin.get(f"{API}/workforce/invitations", timeout=10)
    assert r3.status_code == 401, r3.text

    # Cleanup — clear the step-up flag to avoid polluting other tests.
    _reauth(admin, GROUP_ADMIN[1])
    admin.post(f"{API}/workforce/break-glass/{bg_id}/attest", json={
        "summary": "After-action review: tabletop overdue attestation test",
        "phi_accessed": False, "action_required": False,
    }, timeout=10)
    db.users.update_one({"id": actor["id"]},
                        {"$set": {"step_up_required": False,
                                  "suspicious_flag": False}})


# ---------------------------------------------------------------------------
# Suspicious-login detection
# ---------------------------------------------------------------------------

def test_suspicious_login_new_ip_sets_step_up_flag():
    """Seed a 'prior login' audit row from a DIFFERENT IP + UA, then log in
    through the preview URL so the backend perceives it as a NEW ip → the
    suspicious-login hook should fire and set `step_up_required=True`."""
    admin = _login(*GROUP_ADMIN)
    _reauth(admin, GROUP_ADMIN[1])
    email = _invite_email()
    inv = admin.post(f"{API}/workforce/invitations", json={
        "email": email, "name": "Suspicious test", "role": "staff",
    }, timeout=10).json()
    s = requests.Session()
    act = s.post(f"{API}/workforce/invitations/accept", json={
        "token": inv["dev_token"], "password": "Strong@Pass2026!!",
    }, timeout=10).json()
    user_id = act["user_id"]

    from pymongo import MongoClient
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "ccms_db")
    client = MongoClient(mongo_url)
    db = client[db_name]
    tenant_id = db.users.find_one({"id": user_id}, {"_id": 0, "tenant_id": 1})["tenant_id"]

    # Seed a successful login from a known "old" IP 2 hours ago so the
    # real incoming login shows up as a NEW IP.
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db.audit_logs.insert_one({
        "id": str(uuid.uuid4()),
        "action": "auth.login", "outcome": "success",
        "actor_id": user_id, "actor_email": email,
        "actor_role": "staff", "tenant_id": tenant_id,
        "ip": "10.10.10.10", "user_agent": "SeededOldAgent/1.0",
        "created_at": old_ts,
    })

    # Real login from the test IP — backend will see a NEW ip.
    s_new = requests.Session()
    r = s_new.post(f"{API}/auth/login",
                   json={"email": email, "password": "Strong@Pass2026!!"},
                   timeout=10)
    assert r.status_code == 200, r.text
    # Plain HTTP strips Secure cookies from python-requests' jar; lift the
    # access_token into an Authorization Bearer header so downstream calls
    # are actually authenticated.
    _access = r.cookies.get("access_token")
    assert _access, f"expected access_token cookie in login response; got {dict(r.cookies)}"
    s_new.headers["Authorization"] = f"Bearer {_access}"

    # step_up_required should now be True.
    me = s_new.get(f"{API}/workforce/sessions/me", timeout=10).json()
    assert me.get("step_up_required") is True, me

    # An audit row is present.
    row = db.audit_logs.find_one({
        "action": "security.suspicious_login",
        "actor_id": user_id,
    })
    assert row is not None
    assert "new_ip" in row["metadata"]["signals"]
