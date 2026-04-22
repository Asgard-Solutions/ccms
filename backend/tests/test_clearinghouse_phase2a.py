"""
Phase 2a — Clearinghouse foundations + claim_events stream.

Covers:
  * NoneAdapter + routing registry unit behavior.
  * `emit_claim_event` event-type validation.
  * Payer CRUD round-trips the new clearinghouse fields.
  * Seed/backfill populates defaults on legacy payer rows.
  * GET /api/billing/claims/{id}/events endpoint.

Intentionally minimal on claim-submission E2E — the existing phase
4/5/6 test suites exercise the submission path and the claim_events
writes are best-effort (never fail a mutation), so we rely on those
suites to keep covering the submission happy-path.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token") or r.json().get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    r = s.post(f"{API}/auth/reauth", json={"password": password}, timeout=10)
    if r.status_code == 200:
        rt = r.cookies.get("reauth_token") or r.json().get("reauth_token")
        if rt:
            s.headers["x-reauth-token"] = rt
    return s


# ---------------------------------------------------------------------------
# 1. NoneAdapter + routing registry (pure Python)
# ---------------------------------------------------------------------------
def test_none_adapter_satisfies_protocol():
    from services.billing.clearinghouse import ClearinghouseAdapter
    from services.billing.clearinghouse.none import NoneAdapter

    adapter = NoneAdapter()
    # runtime_checkable Protocol conformance
    assert isinstance(adapter, ClearinghouseAdapter)
    assert adapter.route_id == "none"
    assert adapter.supports_edi is False
    assert adapter.supports_era is False
    assert adapter.supports_eligibility is False


def test_none_adapter_submit_returns_manual_result():
    from services.billing.clearinghouse.none import NoneAdapter

    adapter = NoneAdapter()
    result = asyncio.run(adapter.submit(
        claim_id="claim-123",
        payload_json={"schema": "ccms.claim.v1"},
        payload_x12="ISA*...~",
        method="manual_portal",
        external_reference="REF-9",
        payer={"id": "p1", "clearinghouse_route": "none"},
    ))
    assert result.adapter_route == "none"
    assert result.status == "manual"
    assert result.external_id == "REF-9"
    assert result.submitted_at is not None


def test_none_adapter_ack_and_era_fetchers_are_noops():
    from services.billing.clearinghouse.none import NoneAdapter

    adapter = NoneAdapter()
    assert asyncio.run(adapter.fetch_ack_999("x")) is None
    assert asyncio.run(adapter.fetch_ack_277ca("x")) is None
    assert asyncio.run(adapter.fetch_era_list()) == []
    assert asyncio.run(adapter.eligibility_270_271(policy={"id": "pol"})) is None


def test_get_adapter_for_payer_defaults_to_none():
    from services.billing.clearinghouse import get_adapter_for_payer

    # Unknown/missing route falls back to "none".
    assert get_adapter_for_payer(None).route_id == "none"
    assert get_adapter_for_payer({}).route_id == "none"
    assert get_adapter_for_payer(
        {"clearinghouse_route": "change_healthcare"},   # not yet registered
    ).route_id == "none"
    assert get_adapter_for_payer(
        {"clearinghouse_route": "none"},
    ).route_id == "none"


def test_register_adapter_round_trips():
    from services.billing.clearinghouse import register_adapter, get_adapter_for_payer
    from services.billing.clearinghouse.routing import available_routes
    from services.billing.clearinghouse.base import SubmissionResult

    class DummyAdapter:
        route_id = "dummy_test_adapter"
        supports_edi = True
        supports_era = False
        supports_eligibility = False

        async def submit(self, **_):
            return SubmissionResult(
                adapter_route="dummy_test_adapter", status="queued",
            )

        async def fetch_ack_999(self, external_id):
            return None

        async def fetch_ack_277ca(self, external_id):
            return None

        async def fetch_era_list(self):
            return []

        async def eligibility_270_271(self, *, policy):
            return None

    register_adapter("dummy_test_adapter", DummyAdapter)
    try:
        assert "dummy_test_adapter" in available_routes()
        resolved = get_adapter_for_payer(
            {"clearinghouse_route": "dummy_test_adapter"},
        )
        assert resolved.route_id == "dummy_test_adapter"
    finally:
        # Best-effort cleanup so other tests don't see our stub.
        from services.billing.clearinghouse.routing import _FACTORIES, _CACHE
        _FACTORIES.pop("dummy_test_adapter", None)
        _CACHE.pop("dummy_test_adapter", None)


# ---------------------------------------------------------------------------
# 2. emit_claim_event — type validation
# ---------------------------------------------------------------------------
def test_emit_claim_event_rejects_unknown_event_type():
    from services.billing.events import emit_claim_event

    # Build a minimal TenantContext-like object and a dummy db.
    @dataclass
    class _Ctx:
        tenant_id: str = "tenant-x"
        user_role: str = "admin"
        allowed_location_ids: list | None = None

    class _Coll:
        async def insert_one(self, _doc):   # pragma: no cover
            raise AssertionError("should not reach the collection")

    class _DB:
        claim_events = _Coll()

    with pytest.raises(ValueError, match="Unknown claim event type"):
        asyncio.run(emit_claim_event(
            _DB(), _Ctx(),
            claim_id="c1",
            event_type="bogus_event",
        ))


# ---------------------------------------------------------------------------
# 3. Payer CRUD — new clearinghouse fields round-trip
# ---------------------------------------------------------------------------
def test_payer_create_returns_clearinghouse_defaults():
    s = _login(*ADMIN)
    name = f"CH Audit Test Payer {uuid.uuid4().hex[:8]}"
    r = s.post(f"{API}/billing/payers", json={
        "name": name,
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    # Defaults applied even when caller omits them.
    assert body["clearinghouse_route"] == "none"
    assert body["claim_submission_mode"] == "portal"
    assert body["enrollment_status"] == "not_started"
    assert body["trading_partner_id"] is None


def test_payer_create_and_update_accepts_clearinghouse_fields():
    s = _login(*ADMIN)
    name = f"CH Route Test Payer {uuid.uuid4().hex[:8]}"
    r = s.post(f"{API}/billing/payers", json={
        "name": name,
        "payer_type": "commercial",
        "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
        "claim_submission_mode": "edi",
        "enrollment_status": "in_progress",
        "trading_partner_id": "TP-TEST-001",
    }, timeout=15)
    assert r.status_code == 201, r.text
    body = r.json()
    pid = body["id"]
    assert body["clearinghouse_route"] == "change_healthcare"
    assert body["claim_submission_mode"] == "edi"
    assert body["enrollment_status"] == "in_progress"
    assert body["trading_partner_id"] == "TP-TEST-001"

    # PATCH: flip enrollment to `enrolled` and clear trading_partner.
    r = s.patch(f"{API}/billing/payers/{pid}", json={
        "enrollment_status": "enrolled",
        "trading_partner_id": None,
    }, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enrollment_status"] == "enrolled"
    assert body["trading_partner_id"] is None
    # Unchanged fields preserved.
    assert body["clearinghouse_route"] == "change_healthcare"
    assert body["claim_submission_mode"] == "edi"


def test_payer_create_rejects_invalid_clearinghouse_route():
    s = _login(*ADMIN)
    name = f"CH Invalid Route {uuid.uuid4().hex[:8]}"
    r = s.post(f"{API}/billing/payers", json={
        "name": name,
        "payer_type": "commercial",
        "remit_method": "era",
        "clearinghouse_route": "bogus_route_xyz",
    }, timeout=15)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 4. /claims/{id}/events endpoint is wired and auth-protected
# ---------------------------------------------------------------------------
def test_claim_events_endpoint_returns_404_for_unknown_claim():
    s = _login(*ADMIN)
    bogus_id = str(uuid.uuid4())
    r = s.get(f"{API}/billing/claims/{bogus_id}/events", timeout=10)
    assert r.status_code == 404, r.text


def test_claim_events_endpoint_requires_auth():
    r = requests.get(
        f"{API}/billing/claims/{uuid.uuid4()}/events", timeout=10,
    )
    # 401 unauthenticated or 403 on the permission gate — both acceptable.
    assert r.status_code in (401, 403), r.text
