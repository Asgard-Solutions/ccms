"""Tests for `scripts/seed_large_chart` — production guard, idempotency,
relationship integrity, cleanup path, and requested event count.

The seeder is a strictly additive fixture tool; these tests never touch
the frozen Clinical UI or its contracts.

Each test spins up its own event loop AND its own Motor client so the
motor-to-loop binding is always correct (needed because conftest.py in
this suite already runs its own `asyncio.run()` calls).
"""
from __future__ import annotations

import asyncio
import os

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

# Import target
from scripts import seed_large_chart as sls  # noqa: E402


def _run_with_client(async_body):
    """Run ``async_body(db)`` under a brand-new event loop AND a brand-new
    Motor client so the client's loop binding always matches the caller."""
    loop = asyncio.new_event_loop()
    try:
        async def _wrapper():
            client = AsyncIOMotorClient(os.environ["MONGO_URL"])
            try:
                db = client[os.environ["DB_NAME"]]
                return await async_body(db)
            finally:
                client.close()

        return loop.run_until_complete(_wrapper())
    finally:
        loop.close()


async def _tenant_id_async(db) -> str:
    t = await db.tenants.find_one({"slug": "default"}, {"_id": 0, "id": 1})
    assert t, "default tenant missing — run demo seed first"
    return t["id"]


# --------------------------------------------------------------------
# Production guard
# --------------------------------------------------------------------
class TestProductionGuard:
    def test_app_env_production_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(SystemExit, match="REFUSING TO RUN: APP_ENV=production"):
            sls._enforce_guard(confirm_non_production=True)

    def test_app_env_prod_alias_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        with pytest.raises(SystemExit, match="REFUSING TO RUN: APP_ENV=production"):
            sls._enforce_guard(confirm_non_production=True)

    def test_missing_confirm_flag_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        with pytest.raises(SystemExit, match="pass --confirm-non-production"):
            sls._enforce_guard(confirm_non_production=False)

    def test_confirm_flag_and_non_production_passes(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        sls._enforce_guard(confirm_non_production=True)


# --------------------------------------------------------------------
# Seed + cleanup + idempotency
# --------------------------------------------------------------------
class TestSeedBehaviour:
    def test_seed_creates_at_least_requested_event_count(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            counts = await sls.seed(db, tid, event_count=250)
            assert counts["_total_timeline_events"] >= 250
            assert counts["_patient_id"] == sls.FIXTURE_PATIENT_ID
            assert counts["clinical_encounters"] >= 55
        _run_with_client(_body)

    def test_seed_is_idempotent(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            first = await sls.seed(db, tid, event_count=250)
            second = await sls.seed(db, tid, event_count=250)
            assert first["_total_timeline_events"] == second["_total_timeline_events"]
            for coll in sls.FIXTURE_COLLECTIONS:
                if coll in first:
                    assert first[coll] == second[coll], f"coll drift on {coll}"
        _run_with_client(_body)

    def test_relationship_integrity(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            await sls.seed(db, tid, event_count=250)
            appts = {a["id"] async for a in db.appointments.find(
                {"tenant_id": tid, **sls.FIXTURE_MARKER}, {"_id": 0, "id": 1})}
            encounters = [e async for e in db.clinical_encounters.find(
                {"tenant_id": tid, **sls.FIXTURE_MARKER}, {"_id": 0})]
            assert all(e["appointment_id"] in appts for e in encounters)
            enc_ids = {e["id"] for e in encounters}
            notes = [n async for n in db.clinical_follow_up_notes.find(
                {"tenant_id": tid, **sls.FIXTURE_MARKER}, {"_id": 0})]
            assert notes
            assert all(n["encounter_id"] in enc_ids for n in notes)
            readiness = [r async for r in db.clinical_billing_readiness.find(
                {"tenant_id": tid, **sls.FIXTURE_MARKER}, {"_id": 0})]
            assert all(r["encounter_id"] in enc_ids for r in readiness)
            assert {"signed", "draft", "amended"} <= {n["status"] for n in notes}
            assert {"ready", "warning", "blocked"} <= {r["status"] for r in readiness}
            patient = await db.patients.find_one({"id": sls.FIXTURE_PATIENT_ID}, {"_id": 0})
            assert patient["fixture_source"] == "large_chart_seed"
        _run_with_client(_body)

    def test_cleanup_removes_all_fixture_rows(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            await sls.seed(db, tid, event_count=250)
            counts = await sls.cleanup(db, tid)
            for coll in sls.FIXTURE_COLLECTIONS:
                remaining = await db[coll].count_documents(
                    {"tenant_id": tid, **sls.FIXTURE_MARKER})
                assert remaining == 0, f"{coll} still has {remaining} fixture rows"
            assert sum(counts.values()) > 0
        _run_with_client(_body)

    def test_cleanup_never_touches_non_fixture_rows(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            before = await db.patients.count_documents(
                {"tenant_id": tid, "fixture_source": {"$ne": "large_chart_seed"}})
            await sls.seed(db, tid, event_count=250)
            after_seed = await db.patients.count_documents(
                {"tenant_id": tid, "fixture_source": {"$ne": "large_chart_seed"}})
            await sls.cleanup(db, tid)
            after_cleanup = await db.patients.count_documents(
                {"tenant_id": tid, "fixture_source": {"$ne": "large_chart_seed"}})
            assert before == after_seed == after_cleanup
        _run_with_client(_body)

    def test_larger_events_gives_more_visits(self):
        async def _body(db):
            tid = await _tenant_id_async(db)
            small = await sls.seed(db, tid, event_count=250)
            large = await sls.seed(db, tid, event_count=500)
            assert large["clinical_encounters"] > small["clinical_encounters"]
            assert large["_total_timeline_events"] >= 500
        _run_with_client(_body)


# --------------------------------------------------------------------
# CLI argparse smoke tests
# --------------------------------------------------------------------
class TestCLI:
    def test_default_events_is_250(self):
        args = sls._parse_args(["--confirm-non-production"])
        assert args.events == 250
        assert args.confirm_non_production is True
        assert args.cleanup is False

    def test_custom_events(self):
        args = sls._parse_args(["--confirm-non-production", "--events", "500"])
        assert args.events == 500

    def test_cleanup_flag(self):
        args = sls._parse_args(["--confirm-non-production", "--cleanup"])
        assert args.cleanup is True

    def test_missing_confirm_still_parses(self):
        args = sls._parse_args([])
        assert args.confirm_non_production is False
