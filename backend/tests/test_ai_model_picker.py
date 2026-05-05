"""Tests for the per-surface model picker (GET / PUT /api/ai/settings).

Covers:
  * GET returns surfaces metadata + available models.
  * PUT writes per-surface overrides and round-trips them on GET.
  * Unknown model id is rejected with 422 + structured error.
  * `get_model_choice(tenant_id, surface)` honours per-surface override.
"""
from __future__ import annotations

import asyncio
import os

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")
_BASE = (
    os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001"
).rstrip("/")
API = f"{_BASE}/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=30)
    assert r.status_code == 200, r.text
    return s


class TestAISettingsModelPicker:
    def test_get_returns_metadata(self):
        s = _login(*ADMIN)
        r = s.get(f"{API}/ai/settings", timeout=45)
        assert r.status_code == 200, r.text
        body = r.json()
        # New picker fields
        assert isinstance(body.get("surfaces"), list)
        assert len(body["surfaces"]) >= 5
        assert all("recommended_model" in s for s in body["surfaces"])
        assert isinstance(body.get("available_models"), list)
        assert len(body["available_models"]) >= 3
        for m in body["available_models"]:
            assert m["id"] and m["alias"] and m["tier"]
            assert m["input_per_mtok_usd"] >= 0
            assert m["output_per_mtok_usd"] >= 0
        assert "surface_models" in body  # may be empty {}

    def test_doctor_role_rejected(self):
        s = _login(*DOCTOR)
        r = s.get(f"{API}/ai/settings", timeout=15)
        assert r.status_code == 403

    def test_put_round_trip_surface_overrides(self):
        s = _login(*ADMIN)
        payload = {
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4-5-20250929",
            "enabled": True,
            "surface_models": {
                "scribe_soap_draft": "claude-opus-4-5-20251101",
                "semantic_search":   "claude-haiku-4-5-20251001",
            },
        }
        r = s.put(f"{API}/ai/settings", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["surface_models"]["scribe_soap_draft"] == "claude-opus-4-5-20251101"
        assert body["surface_models"]["semantic_search"] == "claude-haiku-4-5-20251001"

        # Round-trip: GET reflects what we just wrote.
        r2 = s.get(f"{API}/ai/settings", timeout=15)
        body2 = r2.json()
        assert body2["surface_models"] == payload["surface_models"]

    def test_unknown_model_rejected(self):
        s = _login(*ADMIN)
        r = s.put(
            f"{API}/ai/settings",
            json={
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5-20250929",
                "enabled": True,
                "surface_models": {"scribe_soap_draft": "gpt-5-vision"},
            }, timeout=15,
        )
        assert r.status_code == 422
        detail = r.json()["detail"]
        assert detail["code"] == "UNKNOWN_MODEL"
        assert detail["surface"] == "scribe_soap_draft"
        assert "claude-sonnet-4-5-20250929" in detail["allowed"]

    def test_unknown_surface_silently_dropped(self):
        # Forward-compat: if the UI sends a surface key the backend
        # doesn't know yet, we ignore it instead of 4xx-ing.
        s = _login(*ADMIN)
        r = s.put(
            f"{API}/ai/settings",
            json={
                "model_provider": "anthropic",
                "model_name": "claude-sonnet-4-5-20250929",
                "enabled": True,
                "surface_models": {
                    "future_surface_v2": "claude-opus-4-5-20251101",
                    "scribe_soap_draft": "claude-haiku-4-5-20251001",
                },
            }, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "future_surface_v2" not in body["surface_models"]
        assert body["surface_models"]["scribe_soap_draft"] == "claude-haiku-4-5-20251001"

    def test_runtime_resolver_honours_override(self):
        from services.ai.client import get_model_choice

        async def go():
            from motor.motor_asyncio import AsyncIOMotorClient
            from core.tenancy import reset_router_for_tests
            reset_router_for_tests()
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            # Find admin tenant.
            admin = await db.users.find_one(
                {"email": "admin@ccms.app"},
                {"_id": 0, "tenant_id": 1},
            )
            tid = admin["tenant_id"]

            # Pin override directly so test doesn't depend on PUT order.
            await db.ai_settings.update_one(
                {"tenant_id": tid},
                {"$set": {
                    "tenant_id": tid,
                    "model_provider": "anthropic",
                    "model_name": "claude-sonnet-4-5-20250929",
                    "enabled": True,
                    "surface_models": {
                        "scribe_soap_draft": "claude-opus-4-5-20251101",
                    },
                }},
                upsert=True,
            )
            # With surface — override wins.
            _, model_with = await get_model_choice(tid, surface="scribe_soap_draft")
            # Without surface — falls back to tenant default.
            _, model_without = await get_model_choice(tid, surface=None)
            # Surface that has no override — falls back to tenant default.
            _, model_other = await get_model_choice(tid, surface="semantic_search")
            c.close()
            return model_with, model_without, model_other

        with_override, without, other = asyncio.run(go())
        assert with_override == "claude-opus-4-5-20251101"
        assert without == "claude-sonnet-4-5-20250929"
        assert other == "claude-sonnet-4-5-20250929"

    @pytest.fixture(autouse=True, scope="class")
    def _reset_after(self):
        yield
        # Leave settings in a clean state so subsequent test runs start fresh.
        try:
            s = _login(*ADMIN)
            s.put(
                f"{API}/ai/settings",
                json={
                    "model_provider": "anthropic",
                    "model_name": "claude-sonnet-4-5-20250929",
                    "enabled": True,
                    "surface_models": {},
                }, timeout=15,
            )
        except Exception:
            pass
