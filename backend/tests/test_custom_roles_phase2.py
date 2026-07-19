"""
Phase 2 — Custom roles CRUD tests.

Covers:
  * POST /api/authz/roles — create custom role with permission keys
  * POST /api/authz/roles/{key}/clone — clone a system role
  * PATCH /api/authz/roles/{key} — edit name / description / permissions
  * DELETE /api/authz/roles/{key} — archive with in-use guard + force
  * GET /api/authz/roles?include_user_counts=true — user_count +
    is_custom shape
  * System roles are read-only (PATCH/DELETE → 409)
  * Empty permission_keys on create → 400
  * Session epoch bump on role update + force delete
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email: str, password: str, *, reauth: bool = True) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
        if r.status_code == 200:
            rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
            if rt:
                s.headers["x-reauth-token"] = rt
    return s


def _cleanup_role(s, key: str) -> None:
    try:
        s.delete(f"{API}/authz/roles/{key}", params={"force": "true"}, timeout=10)
    except Exception:
        pass


# Test-fixture leakage guard: at the start AND end of this module's test
# session, force-delete any leftover custom roles whose name matches a
# pattern this test file is known to create. Without this, a partial run
# can leave a role like `inuse_test_*` attached to the admin and shrink
# the admin's effective permissions on subsequent runs (which then makes
# unrelated tests fail with mysterious 401/403 results).
import pytest as _pytest

_LEAKED_NAME_PREFIXES = (
    "test role ", "filter test ", "cloned front desk ", "patch test ",
    "empty patch ", "delete test ", "inuse test ", "counttest ",
    "renamed role",
)


def _purge_leaked_test_roles() -> None:
    try:
        s = _login(*DEFAULT_ADMIN)
    except Exception:
        return
    try:
        rows = s.get(f"{API}/authz/roles", timeout=10).json()
    except Exception:
        return
    for r in rows or []:
        if not r.get("is_custom"):
            continue
        name = (r.get("name") or "").strip().lower()
        if any(name.startswith(p) for p in _LEAKED_NAME_PREFIXES):
            _cleanup_role(s, r.get("key"))


@_pytest.fixture(scope="module", autouse=True)
def _purge_test_role_leaks():
    _purge_leaked_test_roles()
    yield
    _purge_leaked_test_roles()


def test_list_roles_includes_is_custom_and_user_counts():
    s = _login(*DEFAULT_ADMIN)
    r = s.get(f"{API}/authz/roles", params={"include_user_counts": "true"}, timeout=15)
    assert r.status_code == 200, r.text
    roles = r.json()
    assert len(roles) >= 11  # 11 baselines
    for r_ in roles:
        assert "is_custom" in r_
        assert "user_count" in r_


def test_create_custom_role_happy_path():
    s = _login(*DEFAULT_ADMIN)
    name = f"Test Role {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/authz/roles", json={
        "name": name,
        "description": "Automated test role",
        "permission_keys": ["patient.read", "appointment.read", "dashboard.read"],
    }, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == name
    assert body["is_custom"] is True
    assert body["is_system"] is False
    key = body["key"]
    try:
        # Fetch back + verify grants present
        roles = s.get(f"{API}/authz/roles", timeout=10).json()
        match = next((x for x in roles if x["key"] == key), None)
        assert match is not None
        grant_keys = {g["permission_key"] for g in match["grants"]}
        assert grant_keys == {"patient.read", "appointment.read", "dashboard.read"}
    finally:
        _cleanup_role(s, key)


def test_create_custom_role_rejects_empty_permissions():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/authz/roles", json={
        "name": "Empty Role",
        "permission_keys": [],
    }, timeout=10)
    assert r.status_code == 400, r.text


def test_create_custom_role_ignores_unknown_permission_keys():
    """Invalid permission keys are silently dropped (defensive)."""
    s = _login(*DEFAULT_ADMIN)
    name = f"Filter Test {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/authz/roles", json={
        "name": name,
        "permission_keys": ["patient.read", "bogus.permission"],
    }, timeout=10)
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    try:
        roles = s.get(f"{API}/authz/roles", timeout=10).json()
        match = next((x for x in roles if x["key"] == key), None)
        grant_keys = {g["permission_key"] for g in match["grants"]}
        assert grant_keys == {"patient.read"}
    finally:
        _cleanup_role(s, key)


def test_clone_system_role():
    s = _login(*DEFAULT_ADMIN)
    name = f"Cloned Front Desk {uuid.uuid4().hex[:6]}"
    r = s.post(f"{API}/authz/roles/front_desk/clone", json={
        "name": name,
        "description": "Cloned for custom tweaks",
    }, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_custom"] is True
    assert body["cloned_from"] == "front_desk"
    key = body["key"]
    try:
        # Verify grants were copied over.
        roles = s.get(f"{API}/authz/roles", timeout=10).json()
        match = next((x for x in roles if x["key"] == key), None)
        assert match is not None
        source = next((x for x in roles if x["key"] == "front_desk"), None)
        src_keys = {g["permission_key"] for g in source["grants"]}
        dst_keys = {g["permission_key"] for g in match["grants"]}
        assert dst_keys == src_keys
    finally:
        _cleanup_role(s, key)


def test_clone_requires_name():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/authz/roles/front_desk/clone", json={}, timeout=10)
    assert r.status_code == 400


def test_clone_unknown_role_404():
    s = _login(*DEFAULT_ADMIN)
    r = s.post(f"{API}/authz/roles/bogus_role_xyz/clone", json={"name": "X"}, timeout=10)
    assert r.status_code == 404


def test_patch_custom_role_updates_name_and_permissions():
    s = _login(*DEFAULT_ADMIN)
    create = s.post(f"{API}/authz/roles", json={
        "name": f"Patch Test {uuid.uuid4().hex[:6]}",
        "permission_keys": ["patient.read"],
    }, timeout=10).json()
    key = create["key"]
    try:
        r = s.patch(f"{API}/authz/roles/{key}", json={
            "name": "Renamed role",
            "description": "Updated desc",
            "permission_keys": ["patient.read", "patient.update",
                                "appointment.read"],
        }, timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Renamed role"
        assert body["description"] == "Updated desc"
        grant_keys = {g["permission_key"] for g in body["grants"]}
        assert grant_keys == {"patient.read", "patient.update", "appointment.read"}
    finally:
        _cleanup_role(s, key)


def test_patch_system_role_blocked_409():
    s = _login(*DEFAULT_ADMIN)
    r = s.patch(f"{API}/authz/roles/front_desk",
                json={"name": "Hacked name"}, timeout=10)
    assert r.status_code == 409


def test_patch_custom_role_empty_permissions_400():
    s = _login(*DEFAULT_ADMIN)
    create = s.post(f"{API}/authz/roles", json={
        "name": f"Empty Patch {uuid.uuid4().hex[:6]}",
        "permission_keys": ["patient.read"],
    }, timeout=10).json()
    key = create["key"]
    try:
        r = s.patch(f"{API}/authz/roles/{key}",
                    json={"permission_keys": []}, timeout=10)
        assert r.status_code == 400
    finally:
        _cleanup_role(s, key)


def test_delete_system_role_blocked_409():
    s = _login(*DEFAULT_ADMIN)
    r = s.delete(f"{API}/authz/roles/front_desk", timeout=10)
    assert r.status_code == 409


def test_delete_unused_custom_role_ok():
    s = _login(*DEFAULT_ADMIN)
    create = s.post(f"{API}/authz/roles", json={
        "name": f"Delete Test {uuid.uuid4().hex[:6]}",
        "permission_keys": ["dashboard.read"],
    }, timeout=10).json()
    key = create["key"]
    r = s.delete(f"{API}/authz/roles/{key}", timeout=10)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Verify it's gone
    roles = s.get(f"{API}/authz/roles", timeout=10).json()
    assert not [x for x in roles if x["key"] == key]


def test_delete_in_use_role_requires_force():
    """A role with an assigned user → 409 without force; 200 with force +
    user assignment revoked."""
    s = _login(*DEFAULT_ADMIN)
    name = f"InUse Test {uuid.uuid4().hex[:6]}"
    create = s.post(f"{API}/authz/roles", json={
        "name": name,
        "permission_keys": ["dashboard.read"],
    }, timeout=10).json()
    key = create["key"]
    try:
        # Assign to admin itself (not a great practice but sufficient for test).
        me = s.get(f"{API}/auth/me", timeout=10).json()
        r = s.post(f"{API}/authz/users/{me['id']}/roles",
                   json={"role_key": key}, timeout=10)
        assert r.status_code in (200, 201), r.text
        # Role-assign bumps admin's session_epoch — re-login.
        s = _login(*DEFAULT_ADMIN)

        # Non-forced delete → 409
        r = s.delete(f"{API}/authz/roles/{key}", timeout=10)
        assert r.status_code == 409, r.text
        assert "1" in r.text

        # Forced delete → 200, 1 user unassigned
        r = s.delete(f"{API}/authz/roles/{key}",
                     params={"force": "true"}, timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["users_unassigned"] == 1
    finally:
        s = _login(*DEFAULT_ADMIN)
        _cleanup_role(s, key)


def test_list_roles_user_count_reflects_assignment():
    s = _login(*DEFAULT_ADMIN)
    name = f"CountTest {uuid.uuid4().hex[:6]}"
    create = s.post(f"{API}/authz/roles", json={
        "name": name,
        "permission_keys": ["dashboard.read"],
    }, timeout=10).json()
    key = create["key"]
    try:
        roles = s.get(f"{API}/authz/roles",
                      params={"include_user_counts": "true"}, timeout=10).json()
        match = next((x for x in roles if x["key"] == key), None)
        assert match["user_count"] == 0

        me = s.get(f"{API}/auth/me", timeout=10).json()
        s.post(f"{API}/authz/users/{me['id']}/roles",
               json={"role_key": key}, timeout=10)
        # re-login after epoch bump
        s = _login(*DEFAULT_ADMIN)
        roles = s.get(f"{API}/authz/roles",
                      params={"include_user_counts": "true"}, timeout=10).json()
        match = next((x for x in roles if x["key"] == key), None)
        assert match["user_count"] == 1
    finally:
        s = _login(*DEFAULT_ADMIN)
        _cleanup_role(s, key)
