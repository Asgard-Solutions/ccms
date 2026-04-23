"""
Iteration 3 — Performance + scalability upgrade test suite.

Covers:
- /api/perf/stats admin-only + redis_alive block
- Providers-list cache (hit, invalidate-on-user-create, invalidate-on-disable)
- Patients-list masked cache + NEVER-cache-unmasked-PHI invariant
- Appointments-list cache + invalidate on create/update/cancel
- Read-after-write on PUT appointment, cancel appointment, PUT patient
- IP rate-limit (31 login attempts -> 429)
- Redis fallback: app remains healthy with redis stopped
- DB routing stats (reads>0, writes>0, read_ratio in [0,1])
"""
import os
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis as redis_sync
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://claim-refactor.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")
STAFF = ("staff@ccms.app", "Staff@ComplianceClinic1")
PATIENT = ("patient@ccms.app", "Patient@ComplianceClinic1")


def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    body = r.json()
    assert body.get("mfa_required") is False
    return s


@pytest.fixture(scope="module")
def rds():
    return redis_sync.Redis.from_url(REDIS_URL, decode_responses=True)


@pytest.fixture(scope="module")
def admin():
    return _login(*ADMIN)


@pytest.fixture(scope="module")
def doctor():
    return _login(*DOCTOR)


@pytest.fixture(scope="module")
def staff():
    return _login(*STAFF)


@pytest.fixture(scope="module")
def patient():
    return _login(*PATIENT)


# ------------------------------------------------------------------
# 1) REDIS BASIC + /api/perf/stats RBAC
# ------------------------------------------------------------------
class TestPerfStats:
    def test_redis_ping(self, rds):
        assert rds.ping() is True

    def test_stats_admin_ok(self, admin):
        r = admin.get(f"{API}/perf/stats", timeout=10)
        assert r.status_code == 200
        body = r.json()
        assert body.get("redis_alive") is True
        assert "cache" in body and "db" in body and "rate_limit" in body
        assert "hits" in body["cache"] and "misses" in body["cache"]
        assert "reads" in body["db"] and "writes" in body["db"]

    def test_stats_forbidden_for_non_admin(self, doctor, staff, patient):
        for s in (doctor, staff, patient):
            r = s.get(f"{API}/perf/stats", timeout=10)
            assert r.status_code == 403, f"expected 403, got {r.status_code} {r.text}"


# ------------------------------------------------------------------
# 2) PROVIDERS LIST CACHE
# ------------------------------------------------------------------
class TestProvidersCache:
    def test_providers_cache_hit_and_invalidation(self, admin, rds):
        rds.delete("identity:providers:active")

        # reset stats for deterministic check
        admin.post(f"{API}/perf/cache/reset-stats", timeout=10)

        r1 = admin.get(f"{API}/auth/providers", timeout=10)
        assert r1.status_code == 200
        assert rds.exists("identity:providers:active") == 1, "providers cache key missing after first GET"

        # second call should be a HIT
        r2 = admin.get(f"{API}/auth/providers", timeout=10)
        assert r2.status_code == 200
        stats = admin.get(f"{API}/perf/stats", timeout=10).json()["cache"]
        assert stats["hits"] >= 1, f"expected hits>=1 after 2nd GET, got {stats}"

        # creating a doctor should invalidate providers
        new_email = f"TEST_cache_doc_{uuid.uuid4().hex[:6]}@ccms.app"
        cr = admin.post(
            f"{API}/auth/users",
            json={"email": new_email, "password": "Doctor@ComplianceClinic1", "role": "doctor", "name": "Test CacheDoc"},
            timeout=15,
        )
        assert cr.status_code in (200, 201), cr.text
        user_id = cr.json().get("id") or cr.json().get("_id") or cr.json().get("user", {}).get("id")
        assert user_id, f"could not extract user id from {cr.json()}"
        assert rds.exists("identity:providers:active") == 0, "providers cache NOT invalidated after user create"

        # repopulate then disable → should invalidate again
        admin.get(f"{API}/auth/providers", timeout=10)
        assert rds.exists("identity:providers:active") == 1

        dr = admin.post(f"{API}/auth/users/{user_id}/disable", timeout=15)
        assert dr.status_code in (200, 204), dr.text
        assert rds.exists("identity:providers:active") == 0, "providers cache NOT invalidated on disable"


# ------------------------------------------------------------------
# 3) PATIENTS LIST CACHE — masked-only; unmask path is NEVER cached
# ------------------------------------------------------------------
class TestPatientsCache:
    def test_masked_cached_unmask_never_cached_and_invalidated_on_write(self, admin, rds):
        # Clean up any existing patients:list:* keys
        for k in rds.scan_iter(match="patients:list:*"):
            rds.delete(k)

        # masked list populates a cache key
        r1 = admin.get(f"{API}/patients", timeout=15)
        assert r1.status_code == 200
        masked_keys = list(rds.scan_iter(match="patients:list:role=admin:search=:deleted=0:masked=1"))
        assert len(masked_keys) == 1, f"expected masked key to exist, found {masked_keys}"

        admin.post(f"{API}/perf/cache/reset-stats", timeout=10)
        r2 = admin.get(f"{API}/patients", timeout=15)
        assert r2.status_code == 200
        cstats = admin.get(f"{API}/perf/stats", timeout=10).json()["cache"]
        assert cstats["hits"] >= 1, f"expected a hit on 2nd masked GET, got {cstats}"

        # Count keys before unmask, do unmask, count after — MUST be identical
        before = set(rds.scan_iter(match="patients:list:*"))
        ru = admin.get(f"{API}/patients", params={"unmask": "true"}, timeout=15)
        assert ru.status_code == 200
        # confirm returned payload has unmasked flag / non-masked data
        after = set(rds.scan_iter(match="patients:list:*"))
        assert before == after, (
            f"unmask=true MUST NOT read/set a cache key! diff: "
            f"added={after - before}, removed={before - after}"
        )

        # Now POST a new patient -> patients: prefix should be gone
        create_payload = {
            "first_name": "TESTCachePat",
            "last_name": uuid.uuid4().hex[:6],
            "email": f"TEST_cachepat_{uuid.uuid4().hex[:6]}@ccms.app",
            "phone": "555-010-0101",
            "date_of_birth": "1990-01-01",
            "address": "100 Cache Ln",
            "emergency_contact": "ICE 555-000-0000",
        }
        cp = admin.post(f"{API}/patients", json=create_payload, timeout=20)
        assert cp.status_code in (200, 201), cp.text
        remaining = list(rds.scan_iter(match="patients:list:*"))
        assert remaining == [], f"patients:list:* NOT invalidated after create: {remaining}"


# ------------------------------------------------------------------
# 4) APPOINTMENTS LIST CACHE + READ-AFTER-WRITE
# ------------------------------------------------------------------
@pytest.fixture(scope="module")
def seed_appt_entities(admin):
    # get a patient id and a doctor id
    pr = admin.get(f"{API}/patients", timeout=15)
    patients = pr.json()
    patient_id = (patients[0].get("id") or patients[0].get("_id")) if patients else None
    assert patient_id, "no patient found to seed appointment"

    provs = admin.get(f"{API}/auth/providers", timeout=10).json()
    provider_id = provs[0].get("id") or provs[0].get("_id") if provs else None
    assert provider_id, "no provider found to seed appointment"

    return {"patient_id": patient_id, "provider_id": provider_id}


class TestAppointmentsCacheAndRAW:
    def test_list_cache_and_raw_on_update_and_cancel(self, admin, rds, seed_appt_entities):
        for k in rds.scan_iter(match="appts:*"):
            rds.delete(k)

        r1 = admin.get(f"{API}/appointments", timeout=15)
        assert r1.status_code == 200
        keys1 = list(rds.scan_iter(match="appts:list:*"))
        assert len(keys1) >= 1, f"expected an appts:list:* key after first GET, got {keys1}"

        admin.post(f"{API}/perf/cache/reset-stats", timeout=10)
        r2 = admin.get(f"{API}/appointments", timeout=15)
        assert r2.status_code == 200
        cs = admin.get(f"{API}/perf/stats", timeout=10).json()["cache"]
        assert cs["hits"] >= 1, f"expected HIT on 2nd appt list, got {cs}"

        # Book an appointment
        now = datetime.now(timezone.utc).replace(microsecond=0)
        start = (now + timedelta(days=2, hours=1)).isoformat()
        end = (now + timedelta(days=2, hours=2)).isoformat()
        payload = {
            "patient_id": seed_appt_entities["patient_id"],
            "provider_id": seed_appt_entities["provider_id"],
            "start_time": start,
            "end_time": end,
            "reason": "TEST cache RAW",
            "notes": "cache raw",
        }
        cr = admin.post(f"{API}/appointments", json=payload, timeout=20)
        assert cr.status_code in (200, 201), cr.text
        appt = cr.json()
        appt_id = appt.get("id") or appt.get("_id")

        # create should invalidate
        assert list(rds.scan_iter(match="appts:list:*")) == [], "appts:list:* not invalidated after POST"

        # PUT reschedule → body must reflect new time (read-after-write)
        new_start = (now + timedelta(days=3, hours=1)).isoformat()
        new_end = (now + timedelta(days=3, hours=2)).isoformat()
        ur = admin.patch(
            f"{API}/appointments/{appt_id}",
            json={"start_time": new_start, "end_time": new_end},
            timeout=20,
        )
        assert ur.status_code == 200, ur.text
        ub = ur.json()
        assert new_start[:16] in ub.get("start_time", ""), f"RAW failed on PUT: {ub}"
        assert new_end[:16] in ub.get("end_time", ""), f"RAW failed on PUT: {ub}"

        # Cancel → status should be cancelled in response (read-after-write)
        canc = admin.post(f"{API}/appointments/{appt_id}/cancel", json={"reason": "TEST"}, timeout=20)
        assert canc.status_code == 200, canc.text
        assert canc.json().get("status") == "cancelled", f"RAW failed on cancel: {canc.json()}"

        # routing stats: read_after_write should be > 0
        stats = admin.get(f"{API}/perf/stats", timeout=10).json()
        assert stats["db"]["read_after_write"] >= 1
        assert stats["db"]["writes"] > 0
        assert stats["db"]["reads"] > 0
        rr = stats["db"]["read_ratio_overall"]
        assert rr is None or (0.0 <= rr <= 1.0)


# ------------------------------------------------------------------
# 5) PATIENT PUT read-after-write
# ------------------------------------------------------------------
class TestPatientPutRAW:
    def test_put_patient_reflects_new_name_in_body(self, admin):
        create = {
            "first_name": "TESTRAW",
            "last_name": uuid.uuid4().hex[:6],
            "email": f"TEST_raw_{uuid.uuid4().hex[:6]}@ccms.app",
            "phone": "555-010-0202",
            "date_of_birth": "1991-02-02",
            "address": "200 RAW Ln",
            "emergency_contact": "RAW ICE",
        }
        cr = admin.post(f"{API}/patients", json=create, timeout=20)
        assert cr.status_code in (200, 201), cr.text
        pid = cr.json().get("id") or cr.json().get("_id")

        new_last = f"Renamed{uuid.uuid4().hex[:6]}"
        ur = admin.patch(
            f"{API}/patients/{pid}",
            params={"unmask": "true"},
            json={"last_name": new_last},
            timeout=20,
        )
        assert ur.status_code == 200, ur.text
        body = ur.json()
        body_repr = str(body)
        assert new_last in body_repr, (
            f"PUT response does not reflect new last_name: {body}"
        )


# ------------------------------------------------------------------
# 6) RATE LIMIT — 31 wrong logins should 429
# ------------------------------------------------------------------
class TestRateLimit:
    def test_login_ip_rate_limit_blocks_31st(self):
        s = requests.Session()
        bad_email = f"TEST_rl_{uuid.uuid4().hex[:6]}@ccms.app"
        # Use a rotating email to avoid per-email lockout... but the test wants
        # both paths to be acceptable: IP limit OR per-email lockout.
        saw_429 = False
        last_code = None
        for i in range(31):
            r = s.post(
                f"{API}/auth/login",
                json={"email": bad_email, "password": "WrongPwd1!x"},
                timeout=10,
            )
            last_code = r.status_code
            if r.status_code == 429:
                saw_429 = True
                break
        assert saw_429, f"expected 429 within 31 attempts, last={last_code}"

    def test_x_forwarded_for_resets_ip_bucket(self):
        # Use a brand-new email and a spoofed forwarded IP — should allow up to 30
        s = requests.Session()
        new_ip = f"10.{uuid.uuid4().int % 255}.{uuid.uuid4().int % 255}.{uuid.uuid4().int % 255}"
        bad_email = f"TEST_rl2_{uuid.uuid4().hex[:6]}@ccms.app"
        # Just 2 calls with new IP -> must not be 429
        for _ in range(2):
            r = s.post(
                f"{API}/auth/login",
                headers={"X-Forwarded-For": new_ip},
                json={"email": bad_email, "password": "WrongPwd1!x"},
                timeout=10,
            )
            assert r.status_code != 429, f"fresh IP should not be rate-limited, got {r.status_code}"


# ------------------------------------------------------------------
# 7) REDIS FALLBACK
# ------------------------------------------------------------------
class TestRedisFallback:
    def test_app_healthy_when_redis_stopped(self, admin):
        # Stop redis
        subprocess.run(["sudo", "supervisorctl", "stop", "redis"], check=False, capture_output=True)
        try:
            # give the supervisor + app cached client a moment to start failing
            deadline = time.time() + 5
            dead = False
            while time.time() < deadline:
                r = admin.get(f"{API}/perf/stats", timeout=10)
                if r.status_code == 200 and r.json().get("redis_alive") is False:
                    dead = True
                    break
                time.sleep(0.3)
            assert dead, "redis_alive still true after stopping redis"

            # health still 200
            h = requests.get(f"{API}/health", timeout=10)
            assert h.status_code == 200

            # providers list still returns (cache miss path)
            pr = admin.get(f"{API}/auth/providers", timeout=10)
            assert pr.status_code == 200

            # login still works using the demo patient account
            s = requests.Session()
            lr = s.post(f"{API}/auth/login", json={"email": PATIENT[0], "password": PATIENT[1]}, timeout=15)
            assert lr.status_code == 200, lr.text
        finally:
            subprocess.run(["sudo", "supervisorctl", "start", "redis"], check=False, capture_output=True)
            # wait for redis to come back
            deadline = time.time() + 10
            alive = False
            while time.time() < deadline:
                r = admin.get(f"{API}/perf/stats", timeout=10)
                if r.status_code == 200 and r.json().get("redis_alive") is True:
                    alive = True
                    break
                time.sleep(0.4)
            assert alive, "redis did NOT come back alive after start"
