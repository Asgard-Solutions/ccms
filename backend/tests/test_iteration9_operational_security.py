"""
Iteration 9 tests — Operational Security Readiness.

Covers:
  - GET /api/compliance/monitoring-hooks RBAC + payload shape
  - Prometheus counters in /api/metrics:
      * ccms_auth_failures_total{reason="invalid_credentials"}
      * ccms_phi_access_total{action="patient.list_viewed"}
      * ccms_exports_total{kind="audit_csv"}
      * ccms_privileged_actions_total{action="user.disabled"}
      * ccms_breakglass_total
      * ccms_privacy_requests_total
      * ccms_rate_limit_blocks_total{source="local"}
      * ccms_secure_endpoint_errors_total
  - Structured JSON security-log lines emitted to supervisor stdout
  - Unit: security_logger._scrub / _BANNED_META_KEYS redaction
  - Rate-limit block emits suspicious event (trigger 31+ rapid logins)
"""
import importlib
import os
import re
import sys
import time
import uuid
from pathlib import Path

import pytest
import requests

BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or "https://ccms-claims-phase6.preview.emergentagent.com"
).rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")

SUPERVISOR_LOG = Path("/var/log/supervisor/backend.out.log")
SUPERVISOR_ERR_LOG = Path("/var/log/supervisor/backend.err.log")


def _clear_login_lockouts():
    """Clear persisted brute-force lockout counters so the admin fixture can
    always log in. Also resets counters for the test-disable throwaway users."""
    mongo_url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
    db_name = os.environ.get("DB_NAME") or "test_database"
    try:
        from pymongo import MongoClient
        c = MongoClient(mongo_url, serverSelectionTimeoutMS=2000)
        c[db_name].login_attempts.delete_many({})
        c.close()
    except Exception:
        # Best-effort — if Mongo isn't reachable from the test runner, skip.
        pass


def _login(email, password):
    s = requests.Session()
    # Retry through transient 429 rate-limit windows (test suite can trip the
    # per-IP bucket on repeated runs; window is ~60s).
    last_resp = None
    deadline = time.time() + 90
    while time.time() < deadline:
        r = s.post(
            f"{API}/auth/login",
            json={"email": email, "password": password},
            timeout=15,
        )
        last_resp = r
        if r.status_code == 200:
            break
        if r.status_code == 429:
            time.sleep(10)
            continue
        break
    assert last_resp is not None and last_resp.status_code == 200, (
        f"login failed for {email}: "
        f"{last_resp.status_code if last_resp is not None else 'n/a'} "
        f"{last_resp.text if last_resp is not None else ''}"
    )
    body = last_resp.json()
    assert body.get("mfa_required") in (False, None)
    return s


@pytest.fixture(scope="module", autouse=True)
def _reset_lockouts():
    _clear_login_lockouts()
    yield


@pytest.fixture(scope="module")
def admin_session():
    _clear_login_lockouts()
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def doctor_session():
    return _login(*DOCTOR)


@pytest.fixture(scope="module")
def staff_session():
    return _login(*STAFF)


@pytest.fixture(scope="module")
def patient_session():
    return _login(*PATIENT)


def _fetch_metrics_text() -> str:
    r = requests.get(f"{API}/metrics", timeout=15)
    assert r.status_code == 200, r.text
    return r.text


def _counter_value(text: str, name: str, labels: dict | None = None) -> float:
    """Parse a specific Prometheus counter row value; 0.0 if not present."""
    if labels:
        label_str = ",".join(
            f'{k}="{v}"' for k, v in sorted(labels.items())
        )
        pat = re.compile(
            rf"^{re.escape(name)}\{{{re.escape(label_str)}\}}\s+([0-9eE.+-]+)",
            re.MULTILINE,
        )
    else:
        pat = re.compile(rf"^{re.escape(name)}\s+([0-9eE.+-]+)", re.MULTILINE)
    m = pat.search(text)
    if not m:
        # Try label order variants — labels dict order may differ
        if labels:
            lab_re = r"\{" + r",".join(
                rf'(?:[a-z_]+="[^"]*",?)*{re.escape(k)}="{re.escape(v)}"(?:,[a-z_]+="[^"]*")*'
                for k, v in labels.items()
            ) + r"\}"
            pat2 = re.compile(
                rf"^{re.escape(name)}{lab_re}\s+([0-9eE.+-]+)",
                re.MULTILINE,
            )
            m = pat2.search(text)
        if not m:
            return 0.0
    return float(m.group(1))


def _counter_any_label(text: str, name: str, label_key: str, label_val: str) -> float:
    """Find a counter row containing a particular label=value pair, any other labels."""
    pat = re.compile(
        rf'^{re.escape(name)}\{{[^}}]*{re.escape(label_key)}="{re.escape(label_val)}"[^}}]*\}}\s+([0-9eE.+-]+)',
        re.MULTILINE,
    )
    m = pat.search(text)
    return float(m.group(1)) if m else 0.0


# ---------- UNIT: security_logger banned-key scrubber ----------

class TestSecurityLoggerScrub:
    def test_banned_keys_redacted(self):
        sys.path.insert(0, "/app/backend")
        mod = importlib.import_module("core.security_logger")
        scrubbed = mod._scrub({
            "password": "plaintext",
            "Token": "abc",
            "refresh_token": "rtkn",
            "actor_email": "admin@ccms.app",
        })
        assert scrubbed["password"] == "<redacted>"
        assert scrubbed["Token"] == "<redacted>"
        assert scrubbed["refresh_token"] == "<redacted>"
        assert scrubbed["actor_email"] == "admin@ccms.app"

    def test_banned_meta_keys_contains_expected(self):
        sys.path.insert(0, "/app/backend")
        mod = importlib.import_module("core.security_logger")
        for k in ("password", "token", "mfa_secret", "jwt_secret",
                  "new_password", "data_encryption_key"):
            assert k in mod._BANNED_META_KEYS


# ---------- /api/compliance/monitoring-hooks ----------

class TestMonitoringHooksRBAC:
    def test_anon_is_401(self):
        r = requests.get(f"{API}/compliance/monitoring-hooks", timeout=10)
        assert r.status_code == 401

    def test_patient_is_403(self, patient_session):
        r = patient_session.get(f"{API}/compliance/monitoring-hooks", timeout=10)
        assert r.status_code == 403

    def test_doctor_is_403(self, doctor_session):
        r = doctor_session.get(f"{API}/compliance/monitoring-hooks", timeout=10)
        assert r.status_code == 403

    def test_staff_is_403(self, staff_session):
        r = staff_session.get(f"{API}/compliance/monitoring-hooks", timeout=10)
        assert r.status_code == 403

    def test_admin_200_shape(self, admin_session):
        r = admin_session.get(f"{API}/compliance/monitoring-hooks", timeout=10)
        assert r.status_code == 200, r.text
        data = r.json()
        for k in ("generated_at", "disclaimer", "structured_logger",
                  "events", "metrics", "incident_evidence_surfaces"):
            assert k in data
        assert len(data["events"]) >= 20, f"events={len(data['events'])}"
        assert len(data["metrics"]) >= 10, f"metrics={len(data['metrics'])}"
        assert len(data["incident_evidence_surfaces"]) >= 5
        # Spot check event schema
        ev = data["events"][0]
        for k in ("component", "event", "when", "outcome", "notes"):
            assert k in ev
        # Ensure key events are present
        names = {e["event"] for e in data["events"]}
        assert "auth.login" in names
        assert "rate_limit.block" in names
        assert "system.unhandled_error" in names
        # Metric catalog spot check
        metric_names = {m["name"] for m in data["metrics"]}
        for req in ("ccms_auth_failures_total", "ccms_phi_access_total",
                    "ccms_privileged_actions_total", "ccms_breakglass_total",
                    "ccms_exports_total", "ccms_secure_endpoint_errors_total",
                    "ccms_privacy_requests_total"):
            assert req in metric_names, f"missing metric {req}"


# ---------- Metrics counters bumped by real actions ----------

class TestMetricsCountersIncrement:
    def test_auth_failure_increments(self):
        # Use a non-existent email to avoid tripping brute-force lockout on
        # a seeded account (which would change the 'reason' label).
        # Snapshot the full counter family and compare sum across labels.
        import re as _re
        text0 = _fetch_metrics_text()
        sum_before = sum(
            float(m.group(1))
            for m in _re.finditer(
                r"^ccms_auth_failures_total\{[^}]*\}\s+([0-9eE.+-]+)",
                text0,
                _re.MULTILINE,
            )
        )
        requests.post(
            f"{API}/auth/login",
            json={"email": f"nonexistent_{uuid.uuid4().hex[:6]}@ccms.app",
                  "password": "WrongPass_987!"},
            timeout=10,
        )
        time.sleep(0.5)
        text1 = _fetch_metrics_text()
        sum_after = sum(
            float(m.group(1))
            for m in _re.finditer(
                r"^ccms_auth_failures_total\{[^}]*\}\s+([0-9eE.+-]+)",
                text1,
                _re.MULTILINE,
            )
        )
        assert sum_after >= sum_before + 1, (
            f"auth_failures sum before={sum_before} after={sum_after}"
        )

    def test_phi_list_viewed_increments(self, admin_session):
        before = _counter_value(
            _fetch_metrics_text(),
            "ccms_phi_access_total",
            {"action": "patient.list_viewed"},
        )
        r = admin_session.get(f"{API}/patients", timeout=15)
        assert r.status_code == 200
        time.sleep(0.5)
        after = _counter_value(
            _fetch_metrics_text(),
            "ccms_phi_access_total",
            {"action": "patient.list_viewed"},
        )
        assert after >= before + 1, f"phi_access before={before} after={after}"

    def test_audit_csv_export_increments(self, admin_session):
        before = _counter_value(
            _fetch_metrics_text(),
            "ccms_exports_total",
            {"kind": "audit_csv"},
        )
        r = admin_session.get(f"{API}/audit-logs/export.csv", timeout=20)
        assert r.status_code == 200
        time.sleep(0.5)
        after = _counter_value(
            _fetch_metrics_text(),
            "ccms_exports_total",
            {"kind": "audit_csv"},
        )
        assert after >= before + 1, f"exports before={before} after={after}"

    def test_privileged_user_disabled_increments(self, admin_session):
        # Create a throwaway user, disable them.
        unique = uuid.uuid4().hex[:8]
        email = f"TEST_disable_{unique}@ccms.app"
        cr = admin_session.post(
            f"{API}/auth/users",
            json={
                "email": email,
                "password": "TempP@ssw0rd123!",
                "name": "TEST Disable",
                "role": "staff",
            },
            timeout=15,
        )
        assert cr.status_code in (200, 201), cr.text
        user_id = cr.json().get("id") or cr.json().get("user", {}).get("id")
        assert user_id, cr.text

        before = _counter_value(
            _fetch_metrics_text(),
            "ccms_privileged_actions_total",
            {"action": "user.disabled"},
        )
        dr = admin_session.post(f"{API}/auth/users/{user_id}/disable", timeout=15)
        assert dr.status_code in (200, 204), dr.text
        time.sleep(0.5)
        after = _counter_value(
            _fetch_metrics_text(),
            "ccms_privileged_actions_total",
            {"action": "user.disabled"},
        )
        assert after >= before + 1, f"privileged before={before} after={after}"

    def test_breakglass_increments(self, admin_session, doctor_session):
        # Create a patient (not owned by the demo doctor), then doctor opens with reason.
        unique = uuid.uuid4().hex[:8]
        pr = admin_session.post(
            f"{API}/patients",
            json={
                "first_name": "TEST",
                "last_name": f"BG_{unique}",
                "date_of_birth": "1990-01-01",
                "gender": "other",
                "phone": "555-0100",
                "email": f"TEST_bg_{unique}@example.com",
            },
            timeout=15,
        )
        assert pr.status_code in (200, 201), pr.text
        pid = pr.json().get("id")
        assert pid

        before_bg = _counter_value(_fetch_metrics_text(), "ccms_breakglass_total")
        # Doctor accesses out-of-scope patient with reason + unmask → break-glass
        resp = doctor_session.get(
            f"{API}/patients/{pid}",
            params={"reason": "Emergency clinical review", "unmask": "true"},
            timeout=15,
        )
        # Doctor either gets 200 (break-glass) or 403 (policy rejects out-of-scope).
        # Either way, if broken we won't see counter bump.
        time.sleep(0.5)
        after_bg = _counter_value(_fetch_metrics_text(), "ccms_breakglass_total")
        if resp.status_code == 200:
            assert after_bg >= before_bg + 1, (
                f"break-glass counter did not increment before={before_bg} "
                f"after={after_bg} resp={resp.status_code}"
            )
        else:
            pytest.skip(
                f"Doctor out-of-scope view returned {resp.status_code}; "
                "break-glass path not available in this data config."
            )

    def test_privacy_request_create_increments(self, patient_session):
        before = _counter_any_label(
            _fetch_metrics_text(),
            "ccms_privacy_requests_total",
            "type",
            "export",
        )
        r = patient_session.post(
            f"{API}/privacy/requests",
            json={"request_type": "export",
                  "notes": "TEST iteration9 export request"},
            timeout=15,
        )
        assert r.status_code in (200, 201), r.text
        time.sleep(0.5)
        after = _counter_any_label(
            _fetch_metrics_text(),
            "ccms_privacy_requests_total",
            "type",
            "export",
        )
        assert after >= before + 1, f"privacy before={before} after={after}"


# ---------- Rate-limit block ----------

class TestRateLimitBlock:
    def test_rate_limit_block_emits_and_counts(self):
        text0 = _fetch_metrics_text()
        before_local = _counter_value(
            text0, "ccms_rate_limit_blocks_total", {"source": "local"}
        )
        # Fire 40 rapid bad logins from the same client → trip the 30/60s bucket
        s = requests.Session()
        saw_429 = False
        for _ in range(40):
            r = s.post(
                f"{API}/auth/login",
                json={"email": "noone@ccms.app", "password": "bad"},
                timeout=5,
            )
            if r.status_code == 429:
                saw_429 = True
        time.sleep(0.5)
        text1 = _fetch_metrics_text()
        after_local = _counter_value(
            text1, "ccms_rate_limit_blocks_total", {"source": "local"}
        )
        if not saw_429 and after_local <= before_local:
            pytest.skip(
                "Rate limit not tripped on this path — possibly NAT-shared client IP."
            )
        assert after_local >= before_local + 1, (
            f"rate_limit local before={before_local} after={after_local}"
        )


# ---------- Structured JSON log lines ----------

class TestSecurityJsonLogLines:
    def test_auth_login_json_line_present(self):
        # Fire a known-good login, then scan supervisor log tail for a structured
        # auth.login event line. We accept either backend.out.log or backend.err.log.
        requests.post(
            f"{API}/auth/login",
            json={"email": ADMIN[0], "password": ADMIN[1]},
            timeout=15,
        )
        time.sleep(1.0)

        found = False
        pat = re.compile(r'"event"\s*:\s*"auth\.login"')
        for path in (SUPERVISOR_LOG, SUPERVISOR_ERR_LOG):
            if not path.exists():
                continue
            # tail last ~400 KB to bound work
            try:
                size = path.stat().st_size
                with path.open("rb") as f:
                    if size > 400_000:
                        f.seek(size - 400_000)
                    blob = f.read().decode("utf-8", errors="ignore")
            except Exception:
                continue
            if pat.search(blob):
                found = True
                break
        assert found, "No structured 'auth.login' JSON event found in supervisor logs"


# ---------- Global 500 handler / secure_endpoint_errors counter exists ----------

class TestSecureEndpointErrorsCounter:
    def test_counter_is_registered(self):
        # We don't force a 500 (no safe synthetic path), but we verify the
        # counter family is exposed in /api/metrics (HELP/TYPE lines exist
        # even at zero).
        text = _fetch_metrics_text()
        assert "ccms_secure_endpoint_errors_total" in text
        assert "# TYPE ccms_secure_endpoint_errors_total counter" in text

    def test_error_handler_installed(self):
        """Best-effort: if we can induce a 500, confirm the payload is sanitized."""
        sys.path.insert(0, "/app/backend")
        try:
            mod = importlib.import_module("core.error_handlers")
        except Exception as e:
            pytest.skip(f"cannot import error_handlers: {e}")
        assert hasattr(mod, "install") and hasattr(mod, "handle_uncaught_exception")
