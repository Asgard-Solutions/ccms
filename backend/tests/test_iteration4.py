"""
Iteration 4 backend tests.

Covers:
 - PATCH /api/auth/users/{id} admin role/status updates + providers cache invalidation
 - GET /api/notifications masked-branch Redis cache + unmask does NOT populate cache
 - GET /api/perf/connection-info replica-verification block
 - GET /api/metrics Prometheus scrape endpoint + gauge/histogram behaviour

Uses the shared public preview URL (REACT_APP_BACKEND_URL) to hit the routed ingress.
"""
import os
import re
import subprocess
import time
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://patient-analytics-6.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")


def _redis(*args):
    return subprocess.run(["redis-cli", *args], capture_output=True, text=True).stdout.strip()


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    # admin/staff roles may have mfa_required=True if enrolled; for fresh seed users, not enrolled
    if body.get("mfa_required"):
        pytest.skip("MFA enrolled on seed account — skipping")
    return s


@pytest.fixture(scope="module")
def admin_session():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def staff_session():
    return _login(*STAFF)


# -------------------- PATCH /api/auth/users/{id} --------------------

class TestPatchUser:
    def _create_user(self, admin, role="staff"):
        email = f"TEST_it4_{uuid.uuid4().hex[:8]}@ccms.app"
        r = admin.post(f"{API}/auth/users", json={
            "email": email, "password": "StrongP@ssw0rd!234",
            "name": "Iteration 4 tester", "role": role,
        })
        assert r.status_code == 201, r.text
        return r.json(), email

    def test_patch_role_invalidates_providers_cache(self, admin_session):
        user, email = self._create_user(admin_session)
        # Populate providers cache
        r = admin_session.get(f"{API}/auth/providers")
        assert r.status_code == 200
        assert _redis("EXISTS", "identity:providers:active") == "1"

        # PATCH role => doctor
        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={"role": "doctor"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["role"] == "doctor"
        assert body["id"] == user["id"]

        # Cache must be invalidated
        assert _redis("EXISTS", "identity:providers:active") == "0", \
            "providers cache key should be gone after PATCH role"

        # Next GET includes patched user
        r2 = admin_session.get(f"{API}/auth/providers")
        assert r2.status_code == 200
        assert any(p["id"] == user["id"] for p in r2.json()), \
            "new doctor should appear in /auth/providers after invalidation"

    def test_patch_status_invalidates_providers_cache(self, admin_session):
        user, email = self._create_user(admin_session, role="doctor")
        # Populate cache
        admin_session.get(f"{API}/auth/providers")
        assert _redis("EXISTS", "identity:providers:active") == "1"

        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={"status": "disabled"})
        assert r.status_code == 200
        assert r.json()["status"] == "disabled"
        assert _redis("EXISTS", "identity:providers:active") == "0"

        admin_session.get(f"{API}/auth/providers")
        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={"status": "active"})
        assert r.status_code == 200
        assert _redis("EXISTS", "identity:providers:active") == "0"

    def test_patch_audit_row_written(self, admin_session):
        user, _ = self._create_user(admin_session)
        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={"role": "doctor"})
        assert r.status_code == 200
        # Audit log
        r = admin_session.get(f"{API}/audit-logs", params={"action": "user.updated", "limit": 50})
        assert r.status_code == 200
        rows = r.json()
        assert any(row.get("entity_id") == user["id"] and row.get("action") == "user.updated" for row in rows)

    def test_non_admin_forbidden(self, staff_session, admin_session):
        user, _ = self._create_user(admin_session)
        r = staff_session.patch(f"{API}/auth/users/{user['id']}", json={"role": "doctor"})
        assert r.status_code == 403

    def test_cannot_demote_self(self, admin_session):
        me = admin_session.get(f"{API}/auth/me").json()
        r = admin_session.patch(f"{API}/auth/users/{me['id']}", json={"role": "doctor"})
        assert r.status_code == 400

    def test_cannot_disable_self(self, admin_session):
        me = admin_session.get(f"{API}/auth/me").json()
        r = admin_session.patch(f"{API}/auth/users/{me['id']}", json={"status": "disabled"})
        assert r.status_code == 400

    def test_empty_body_returns_400(self, admin_session):
        user, _ = self._create_user(admin_session)
        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={})
        assert r.status_code == 400

    def test_unknown_user_returns_404(self, admin_session):
        r = admin_session.patch(f"{API}/auth/users/{uuid.uuid4()}", json={"role": "doctor"})
        assert r.status_code == 404

    def test_extra_fields_return_422(self, admin_session):
        user, _ = self._create_user(admin_session)
        r = admin_session.patch(f"{API}/auth/users/{user['id']}", json={"email": "x@y.com"})
        assert r.status_code == 422


# -------------------- Notifications masked-cache --------------------

class TestNotificationsCache:
    def _scan_notifications_keys(self):
        out = _redis("--scan", "--pattern", "notifications:*")
        return [k for k in out.splitlines() if k.strip()]

    def _book_appointment(self, admin):
        # find a patient + doctor
        patients = admin.get(f"{API}/patients").json()
        if not patients:
            r = admin.post(f"{API}/patients", json={
                "name": "TEST_it4_pat", "email": f"TEST_it4_p_{uuid.uuid4().hex[:6]}@x.com",
                "dob": "1990-01-01", "gender": "other",
            })
            assert r.status_code in (200, 201), r.text
            pid = r.json()["id"]
        else:
            pid = patients[0]["id"]
        docs = admin.get(f"{API}/auth/providers").json()
        if not docs:
            pytest.skip("no doctors")
        did = docs[0]["id"]
        import random
        hour = random.randint(0, 23)
        minute = random.choice([0, 15, 30, 45])
        year = random.randint(2099, 2150)
        month = random.randint(1, 12)
        day = random.randint(1, 28)
        r = admin.post(f"{API}/appointments", json={
            "patient_id": pid, "provider_id": did,
            "start_time": f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:00+00:00",
            "end_time": f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute+15 if minute<45 else 45:02d}:00+00:00" if minute < 45 else f"{year:04d}-{month:02d}-{day:02d}T{(hour+1)%24:02d}:00:00+00:00",
            "reason": "iteration 4 cache test",
        })
        assert r.status_code in (200, 201), r.text
        return r.json()

    def test_masked_populates_cache_second_is_hit(self, admin_session):
        self._book_appointment(admin_session)

        # clear cache first to get a deterministic miss->hit cycle
        subprocess.run(["redis-cli", "--scan", "--pattern", "notifications:*"], capture_output=True, text=True)
        for k in self._scan_notifications_keys():
            _redis("DEL", k)

        # metrics before
        before = requests.get(f"{API}/metrics").text
        def _counter(body, name):
            m = re.search(rf"^{name} ([0-9.e+-]+)$", body, re.MULTILINE)
            return float(m.group(1)) if m else 0.0
        hits_before = _counter(before, "ccms_cache_hits_total")

        r1 = admin_session.get(f"{API}/notifications")
        assert r1.status_code == 200, r1.text
        keys_after_first = self._scan_notifications_keys()
        assert len(keys_after_first) >= 1, f"expected a notifications:* key after masked GET, got {keys_after_first}"

        # Second call — cache hit
        r2 = admin_session.get(f"{API}/notifications")
        assert r2.status_code == 200
        after = requests.get(f"{API}/metrics").text
        hits_after = _counter(after, "ccms_cache_hits_total")
        assert hits_after > hits_before, f"cache hits should increase ({hits_before} -> {hits_after})"

    def test_booking_invalidates_notifications_prefix(self, admin_session):
        # Populate
        admin_session.get(f"{API}/notifications")
        keys_before = set(self._scan_notifications_keys())
        assert keys_before, "expected a cached notifications key"

        self._book_appointment(admin_session)
        # Subscriber should invalidate — give it a short moment
        time.sleep(0.8)
        keys_after = set(self._scan_notifications_keys())
        # At least one of the previous keys should be gone / replaced
        assert keys_before - keys_after or not (keys_before & keys_after), \
            f"booking should invalidate at least the prior key(s). before={keys_before} after={keys_after}"

    def test_unmask_does_not_populate_cache(self, admin_session):
        # Populate masked first
        admin_session.get(f"{API}/notifications")
        before = set(self._scan_notifications_keys())

        r = admin_session.get(f"{API}/notifications", params={"unmask": "true", "reason": "admin audit"})
        assert r.status_code == 200
        after = set(self._scan_notifications_keys())
        # unmask should not add any new notifications:* keys
        assert after.issubset(before) or after == before, \
            f"unmask=true must NOT populate cache. new keys: {after - before}"

        # Second unmask — should also write an audit row
        r2 = admin_session.get(f"{API}/notifications", params={"unmask": "true", "reason": "second audit"})
        assert r2.status_code == 200

        audits = admin_session.get(f"{API}/audit-logs", params={"action": "notification.unmasked", "limit": 50}).json()
        assert len(audits) >= 2, f"expected >=2 notification.unmasked audit rows, got {len(audits)}"


# -------------------- /api/perf/connection-info --------------------

class TestConnectionInfo:
    def test_admin_shape(self, admin_session):
        r = admin_session.get(f"{API}/perf/connection-info")
        assert r.status_code == 200, r.text
        body = r.json()
        for section in ("write", "read"):
            assert section in body
            assert "topology_type" in body[section]
            assert "nodes" in body[section]
            assert "read_preference" in body[section]
        assert body["same_client"] is False
        assert body["write"]["read_preference"].startswith("Primary")
        assert "Secondary" in body["read"]["read_preference"]

    def test_non_admin_forbidden(self, staff_session):
        r = staff_session.get(f"{API}/perf/connection-info")
        assert r.status_code == 403


# -------------------- /api/metrics --------------------

class TestMetrics:
    def test_unauth_ok_content_type(self):
        r = requests.get(f"{API}/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "version=0.0.4" in r.headers["content-type"]

    def test_required_metric_names(self):
        body = requests.get(f"{API}/metrics").text
        for name in (
            "ccms_cache_hits_total", "ccms_cache_misses_total", "ccms_cache_sets_total",
            "ccms_cache_invalidations_total", "ccms_cache_errors_total",
            "ccms_db_queries_total", "ccms_redis_up",
            "ccms_rate_limit_blocks_total", "ccms_http_request_duration_seconds_bucket",
        ):
            assert name in body, f"missing metric {name}"
        # db routing labels
        assert 'ccms_db_queries_total{route="write"}' in body
        assert 'ccms_db_queries_total{route="read"}' in body
        assert 'ccms_db_queries_total{route="read_after_write"}' in body

    def test_traffic_increases_counters(self, admin_session):
        def _vals(body):
            hits = float(re.search(r"^ccms_cache_hits_total ([0-9.e+-]+)$", body, re.MULTILINE).group(1))
            m = re.search(r'ccms_db_queries_total\{route="read"\} ([0-9.e+-]+)', body)
            reads = float(m.group(1)) if m else 0.0
            return hits, reads

        before = requests.get(f"{API}/metrics").text
        h0, r0 = _vals(before)
        for _ in range(4):
            admin_session.get(f"{API}/patients")
        time.sleep(0.3)
        after = requests.get(f"{API}/metrics").text
        h1, r1 = _vals(after)
        assert r1 > r0, f"read counter did not increase ({r0}->{r1})"
        assert h1 > h0, f"cache hits did not increase ({h0}->{h1})"

    def test_histogram_has_positive_buckets(self, admin_session):
        for _ in range(3):
            admin_session.get(f"{API}/patients")
        body = requests.get(f"{API}/metrics").text
        m = re.search(
            r'ccms_http_request_duration_seconds_bucket\{le="0.5",method="GET",path_prefix="/api/patients",status_class="2xx"\} ([0-9.e+-]+)',
            body,
        )
        assert m and float(m.group(1)) > 0, "histogram bucket for /api/patients missing or zero"

    def test_path_prefix_does_not_explode_with_ids(self, admin_session):
        # hit a dynamic patient id route
        pats = admin_session.get(f"{API}/patients").json()
        if pats:
            pid = pats[0]["id"]
            admin_session.get(f"{API}/patients/{pid}", params={"reason": "metrics test"})
        body = requests.get(f"{API}/metrics").text
        # There should NOT be a path_prefix containing a uuid
        bad = re.findall(r'path_prefix="(/api/patients/[0-9a-f-]{8,})"', body)
        assert not bad, f"path_prefix exploded with dynamic ids: {bad[:3]}"

    def test_redis_up_gauge_flips_when_stopped(self):
        # First ensure up == 1
        body = requests.get(f"{API}/metrics").text
        m = re.search(r"^ccms_redis_up ([0-9.]+)$", body, re.MULTILINE)
        assert m and float(m.group(1)) == 1.0

        subprocess.run(["sudo", "supervisorctl", "stop", "redis"], capture_output=True)
        try:
            time.sleep(1.5)
            # Give the gauge up to 5s to reflect
            flipped = False
            for _ in range(5):
                body2 = requests.get(f"{API}/metrics").text
                m2 = re.search(r"^ccms_redis_up ([0-9.]+)$", body2, re.MULTILINE)
                if m2 and float(m2.group(1)) == 0.0:
                    flipped = True
                    break
                time.sleep(1)
            assert flipped, "ccms_redis_up did not flip to 0 when redis was stopped"
        finally:
            subprocess.run(["sudo", "supervisorctl", "start", "redis"], capture_output=True)
            time.sleep(1.0)
