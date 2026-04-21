"""
Iteration 11: Security Response Headers Middleware + /api/compliance/transport.

Validates:
  1. Every API response carries the required security headers.
  2. HSTS is NOT present in dev (APP_ENV not set to 'production').
  3. /api/compliance/transport: 401 anon, 403 non-admin, 200 admin with expected keys.
  4. Quick regression smoke of prior phases (login/me/patients/audit/privacy/compliance).
"""
import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://chiro-checkout-flow.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=20)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text[:200]}"
    body = r.json()
    if body.get("mfa_required"):
        pytest.skip(f"{email} requires MFA — cannot test headless")
    return s


@pytest.fixture(scope="module")
def admin_session() -> requests.Session:
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def patient_session() -> requests.Session:
    return _login(*PATIENT)


# ---------- Security headers on every API response ----------

REQUIRED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-site",
}


def _assert_security_headers(resp: requests.Response, label: str = ""):
    for k, v in REQUIRED_HEADERS.items():
        got = resp.headers.get(k)
        assert got == v, f"{label}: header {k} expected={v!r} got={got!r}"
    # Permissions-Policy just needs to be present and non-empty
    pp = resp.headers.get("Permissions-Policy", "")
    assert "geolocation=()" in pp, f"{label}: Permissions-Policy missing geolocation=(): {pp!r}"
    # CSP must contain default-src 'self' and frame-ancestors 'none'
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp, f"{label}: CSP missing default-src 'self': {csp!r}"
    assert "frame-ancestors 'none'" in csp, f"{label}: CSP missing frame-ancestors 'none': {csp!r}"
    # HSTS must NOT be emitted in dev
    assert "Strict-Transport-Security" not in resp.headers, (
        f"{label}: HSTS unexpectedly present in dev: {resp.headers.get('Strict-Transport-Security')!r}"
    )


def test_security_headers_on_health():
    r = requests.get(f"{API}/health", timeout=10)
    assert r.status_code == 200
    _assert_security_headers(r, "GET /api/health")


def test_security_headers_on_401_unauth_me():
    r = requests.get(f"{API}/auth/me", timeout=10)
    assert r.status_code == 401
    _assert_security_headers(r, "GET /api/auth/me (anon)")


def test_security_headers_on_authenticated_patients(admin_session):
    r = admin_session.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    _assert_security_headers(r, "GET /api/patients (admin)")


def test_security_headers_on_404():
    r = requests.get(f"{API}/this-route-does-not-exist-xyz", timeout=10)
    assert r.status_code == 404
    _assert_security_headers(r, "GET nonexistent route")


def test_security_headers_on_post_login():
    r = requests.post(f"{API}/auth/login", json={"email": "nosuch@x", "password": "x"}, timeout=10)
    assert r.status_code in (400, 401, 422, 429)
    _assert_security_headers(r, "POST /api/auth/login (bad creds)")


# ---------- /api/compliance/transport access control ----------

def test_transport_requires_auth():
    r = requests.get(f"{API}/compliance/transport", timeout=10)
    assert r.status_code == 401, f"expected 401 got {r.status_code}: {r.text[:200]}"


def test_transport_forbids_non_admin(patient_session):
    r = patient_session.get(f"{API}/compliance/transport", timeout=10)
    assert r.status_code == 403, f"expected 403 got {r.status_code}: {r.text[:200]}"


def test_transport_admin_ok(admin_session):
    r = admin_session.get(f"{API}/compliance/transport", timeout=15)
    assert r.status_code == 200
    _assert_security_headers(r, "GET /api/compliance/transport (admin)")
    body = r.json()
    # Structural assertions
    for key in (
        "generated_at",
        "disclaimer",
        "app_env",
        "observed_scheme",
        "scheme_source",
        "cookie_flags",
        "security_headers_emitted_by_app",
        "hsts",
        "transport_warnings",
    ):
        assert key in body, f"missing key {key} in transport response"
    # Value assertions
    assert body["app_env"] != "production", f"APP_ENV should be dev here, got {body['app_env']}"
    assert body["observed_scheme"] in ("http", "https", ""), body["observed_scheme"]
    assert isinstance(body["security_headers_emitted_by_app"], list)
    assert any("Content-Security-Policy" in h for h in body["security_headers_emitted_by_app"])
    assert body["hsts"]["emitted_only_when"].startswith("APP_ENV=production")
    assert body["cookie_flags"]["secure"] is True
    assert body["cookie_flags"]["httponly"] is True


# ---------- Regression smoke — prior phases ----------

def test_regression_login_all_roles():
    for email, pwd in (ADMIN, DOCTOR, STAFF, PATIENT):
        s = requests.Session()
        r = s.post(f"{API}/auth/login", json={"email": email, "password": pwd}, timeout=15)
        assert r.status_code == 200, f"login {email}: {r.status_code}"
        body = r.json()
        assert "user" in body
        assert body["user"]["email"] == email
        if not body.get("mfa_required"):
            me = s.get(f"{API}/auth/me", timeout=10)
            assert me.status_code == 200
            assert me.json()["email"] == email


def test_regression_compliance_overview(admin_session):
    r = admin_session.get(f"{API}/compliance/overview", timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_regression_compliance_security_config(admin_session):
    r = admin_session.get(f"{API}/compliance/security-config", timeout=15)
    assert r.status_code == 200


def test_regression_compliance_monitoring_hooks(admin_session):
    r = admin_session.get(f"{API}/compliance/monitoring-hooks", timeout=15)
    assert r.status_code == 200


def test_regression_patients_list_masked(admin_session):
    r = admin_session.get(f"{API}/patients", timeout=15)
    assert r.status_code == 200
    lst = r.json()
    assert isinstance(lst, list)


def test_regression_audit_logs(admin_session):
    # After iter13 migration, audit_log.read is MFA-gated → reauth first.
    admin_session.post(f"{API}/auth/reauth", json={"password": "Admin@ComplianceClinic1"}, timeout=15)
    r = admin_session.get(f"{API}/audit-logs?limit=5", timeout=15)
    assert r.status_code == 200


def test_regression_privacy_requests(admin_session):
    r = admin_session.get(f"{API}/privacy/requests", timeout=15)
    # admin should be able to list; either 200 or 403 depending on impl, but per docs admin listing is allowed
    assert r.status_code in (200,)


def test_regression_appointments_list(admin_session):
    r = admin_session.get(f"{API}/appointments", timeout=15)
    assert r.status_code == 200


def test_regression_password_reset_request_dev_token():
    r = requests.post(
        f"{API}/auth/password-reset/request",
        json={"email": PATIENT[0]},
        timeout=15,
    )
    # Per docs: public endpoint, 200 always (enumeration-safe). Dev token returned in non-prod.
    assert r.status_code == 200
    _assert_security_headers(r, "POST /api/auth/password-reset/request")
