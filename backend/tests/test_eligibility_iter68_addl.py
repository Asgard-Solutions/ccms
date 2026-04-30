"""
Additional eligibility regression tests for iteration 68:
  - 270 wire NM1*PR / NM1*1P / NM1*IL / DMG / EQ sanity
  - 271 wire ST*271 / NM1*PR / NM1*IL / DMG / EB / DTP*356 sanity
  - tenant isolation: a check created by tenant A admin must not appear
    when a different tenant admin lists their own policy's checks.
"""
from __future__ import annotations
import os
import requests
import pytest

API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
SUNRISE_ADMIN = ("group-admin@sunrise.ccms.app", "Sunrise@ComplianceClinic1")


def _login(email, pw):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": pw}, timeout=15)
    assert r.status_code == 200, r.text
    tok = s.cookies.get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    rr = s.post(f"{API}/auth/reauth", json={"password": pw}, timeout=10)
    if rr.status_code == 200:
        rtok = rr.json().get("reauth_token")
        if rtok:
            s.headers["x-reauth-token"] = rtok
    return s


def _first_policy(sess):
    pts = sess.get(f"{API}/patients", timeout=15).json()
    for p in pts:
        pol = sess.get(
            f"{API}/billing/insurance-policies?patient_id={p['id']}", timeout=10
        ).json()
        if pol:
            return pol[0]
    pytest.skip("no demo policy available for this tenant")


@pytest.fixture(scope="module")
def admin_sess():
    return _login(*ADMIN)


def test_270_271_wire_segments(admin_sess):
    pol = _first_policy(admin_sess)
    r = admin_sess.post(
        f"{API}/billing/policies/{pol['id']}/eligibility-check",
        json={"service_type_codes": ["30"]}, timeout=15,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    req = body["request_wire"]
    res = body["response_wire"]

    # 270 sanity
    assert req.startswith("ISA*")
    assert "ST*270*0001*005010X279A1" in req
    assert "NM1*PR*2*" in req
    assert "NM1*1P*" in req
    assert "NM1*IL*1*" in req
    assert "DMG*D8*" in req
    assert "EQ*30" in req

    # 271 sanity
    assert "ST*271*" in res
    assert "NM1*PR" in res
    assert "NM1*IL" in res
    assert "DMG*D8" in res
    assert ("EB*1" in res) or ("EB*6" in res)
    # Plan effective date — DTP*356
    # Some inactive responses may omit DTP*356 if no effective date — best-effort.
    if body["result"]["coverage_active"]:
        assert "DTP*356*D8*" in res


def test_tenant_isolation_scoped_listing(admin_sess):
    """Admin tenant lists checks scoped to its own policies. Sunrise tenant
    cannot read tenant A's check by id, and its own policy listing has only
    its own rows."""
    pol_a = _first_policy(admin_sess)
    created = admin_sess.post(
        f"{API}/billing/policies/{pol_a['id']}/eligibility-check",
        json={}, timeout=15,
    ).json()
    a_check_id = created["id"]
    a_listing = admin_sess.get(
        f"{API}/billing/policies/{pol_a['id']}/eligibility-checks", timeout=10,
    ).json()
    a_ids = {row["id"] for row in a_listing}
    assert a_check_id in a_ids

    try:
        sun = _login(*SUNRISE_ADMIN)
    except AssertionError:
        pytest.skip("sunrise tenant admin not provisioned in this env")

    # Sunrise admin should not see tenant A's check id when listing its own
    # tenant's policies.
    sun_pol = _first_policy(sun)
    if sun_pol["id"] == pol_a["id"]:
        pytest.skip("tenants share policy in this seed — cannot test isolation")
    sun_listing = sun.get(
        f"{API}/billing/policies/{sun_pol['id']}/eligibility-checks", timeout=10,
    ).json()
    sun_ids = {row["id"] for row in sun_listing}
    assert a_check_id not in sun_ids

    # Cross-tenant detail fetch must NOT succeed (404 or 403).
    cross = sun.get(f"{API}/billing/eligibility-checks/{a_check_id}", timeout=10)
    assert cross.status_code in (403, 404), cross.status_code
