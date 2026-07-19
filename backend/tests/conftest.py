"""
Shared pytest fixtures for backend integration tests.

Problem:
  The running FastAPI backend is shared across test functions. Several
  tests (login rate limiting, change-password failure throttling, PIN
  verify lockouts) deliberately trip rate limiters. When the full suite
  runs, bursts of admin logins from many test files hit the
  `login:{ip}` ceiling (60 req / 60 s) and every subsequent test errors
  with 429 during fixture setup.

Fix:
  Before each test we POST `/api/_debug/rate-limit/reset` which wipes
  both the in-process deques in `core/rate_limit.py` and the Redis
  `rl:*` / `rlfail:*` namespace. The endpoint is only exposed when
  `APP_ENV != "production"` (see `core/debug_router.py`).

Graceful degradation:
  If the reset endpoint returns 404 or the backend isn't reachable, the
  fixture silently skips — the existing tests will then behave as
  before (individually still green) without masking the real problem.

Secondary problem — fixture-row leakage:
  `test_validation_phase4.py` + `test_x12_837p_phase7.py` create
  synthetic payers, patients, and claims on the default (Riverbend)
  tenant every run. Without teardown they accumulate — 30+ stray
  payers and 20+ stray patients after a handful of CI runs — which
  then trips the Riverbend integrity check and the
  `test_eight_personas_present` regression. The
  `_cleanup_fixture_rows_after_session` fixture below wipes rows that
  match the known-constant name shapes after the full session. It's
  safe: name prefixes are test-only ("Validator Payer ", "P7 Wire",
  "Phase-7 ...") and never overlap with curated Riverbend demo data.
"""
from __future__ import annotations

import asyncio
import os
import re

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
_API = f"{BASE_URL}/api" if BASE_URL else "http://localhost:8001/api"

_RESET_ENDPOINT = f"{_API}/_debug/rate-limit/reset"


def _reset_rate_limits() -> None:
    try:
        requests.post(_RESET_ENDPOINT, timeout=3)
    except requests.RequestException:
        # Never fail a test because the reset hook is unavailable.
        pass


@pytest.fixture(autouse=True)
def _clear_rate_limits_between_tests():
    """Reset rate-limit state *before* every test.

    We reset before (not after) so an early-exit in a previous test
    still leaves the limiter clean for the next one."""
    _reset_rate_limits()
    yield


# Name-shape patterns that the backend test suite creates and must
# clean up at session end. Each entry is (collection, field, regex).
# These are literal prefixes that never appear on curated Riverbend
# demo rows, so deleting by them is safe.
_TEST_FIXTURE_PATTERNS = [
    ("billing_payers", "name", r"^Validator Payer "),
    ("billing_payers", "name", r"^P7 Payer "),
    ("billing_payers", "name", r"^P4-"),
    ("patients", "first_name", r"^Val$"),
    ("patients", "first_name", r"^P7$"),
    ("patients", "first_name", r"^P4$"),
]


def _run_fixture_sweep() -> None:
    """Synchronous entry to the async cleanup body — used by both the
    module-scope autouse fixture below and the
    `pytest_runtest_teardown` hook that fires between tests so
    mid-session checks like test_riverbend_demo_sanitation see a
    pristine tenant."""
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # noqa: F401
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
    except Exception:  # noqa: BLE001
        return

    async def _sweep() -> None:
        from motor.motor_asyncio import AsyncIOMotorClient
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = c[os.environ["DB_NAME"]]
            tenant = await db.tenants.find_one(
                {"slug": "default"}, {"_id": 0, "id": 1},
            )
            if not tenant:
                return
            tid = tenant["id"]

            stray_payer_ids: set[str] = set()
            stray_patient_ids: set[str] = set()
            for coll, field, rx in _TEST_FIXTURE_PATTERNS:
                regex = re.compile(rx)
                async for row in db[coll].find(
                    {"tenant_id": tid, field: {"$regex": regex}},
                    {"_id": 0, "id": 1},
                ):
                    if coll == "billing_payers":
                        stray_payer_ids.add(row["id"])
                    elif coll == "patients":
                        stray_patient_ids.add(row["id"])

            # Orphan claims — claims whose payer_id no longer resolves.
            current_payer_ids = {
                p["id"] async for p in db.billing_payers.find(
                    {"tenant_id": tid,
                     "$or": [{"name": {"$regex": rx}}
                             for rx in [r"^Validator Payer ", r"^P7 Payer ",
                                        r"^P4-"]]},
                    {"_id": 0, "id": 1},
                )
            }
            # also includes orphans — claim with payer in stray set OR
            # patient in stray set.
            claim_ids_to_remove: set[str] = set()
            if stray_payer_ids or stray_patient_ids:
                clause = []
                if stray_payer_ids:
                    clause.append({"payer_id": {"$in": list(stray_payer_ids)}})
                if stray_patient_ids:
                    clause.append({"patient_id": {"$in": list(stray_patient_ids)}})
                async for row in db.claims.find(
                    {"tenant_id": tid, "$or": clause}, {"_id": 0, "id": 1},
                ):
                    claim_ids_to_remove.add(row["id"])

            # Delete in dependency order.
            if claim_ids_to_remove:
                cid_list = list(claim_ids_to_remove)
                await db.claim_lines.delete_many(
                    {"tenant_id": tid, "claim_id": {"$in": cid_list}})
                await db.claim_diagnoses.delete_many(
                    {"tenant_id": tid, "claim_id": {"$in": cid_list}})
                await db.claim_line_modifiers.delete_many(
                    {"tenant_id": tid, "claim_line_id": {"$in": cid_list}})
                await db.claim_validation_findings.delete_many(
                    {"tenant_id": tid, "claim_id": {"$in": cid_list}})
                await db.claim_submissions.delete_many(
                    {"tenant_id": tid, "claim_id": {"$in": cid_list}})
                await db.claims.delete_many(
                    {"tenant_id": tid, "id": {"$in": cid_list}})
            if stray_patient_ids:
                await db.patient_insurance_policies.delete_many(
                    {"tenant_id": tid,
                     "patient_id": {"$in": list(stray_patient_ids)}})
                await db.patients.delete_many(
                    {"tenant_id": tid,
                     "id": {"$in": list(stray_patient_ids)}})
            if stray_payer_ids:
                await db.patient_insurance_policies.delete_many(
                    {"tenant_id": tid,
                     "payer_id": {"$in": list(stray_payer_ids)}})
                await db.billing_payers.delete_many(
                    {"tenant_id": tid,
                     "id": {"$in": list(stray_payer_ids)}})
        finally:
            c.close()

    asyncio.run(_sweep())


def pytest_runtest_teardown(item, nextitem):  # noqa: ARG001
    """Fire the fixture-row sweep after every test so mid-session
    checks (like `test_riverbend_demo_sanitation`) always see a
    pristine tenant regardless of which tests ran before them."""
    # Skip when the next test is from the same module as the current
    # one — prevents hitting Mongo after every single test when it's
    # not necessary (perf: 90 tests × 50ms = 4s saved).
    if nextitem is not None and nextitem.module is item.module:
        return
    _run_fixture_sweep()


@pytest.fixture(scope="module", autouse=True)
def _cleanup_fixture_rows_after_module():
    """Safety net — module-end sweep in case the per-test teardown
    hook gets skipped (e.g. xfail / unexpected session-end)."""
    yield
    _run_fixture_sweep()
