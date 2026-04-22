"""
Phase 2c — Change / Optum adapter + clearinghouse enrollments +
admin settings endpoints.

Covers:
  * Adapters resolve from `clearinghouse_route` on the payer.
  * "disabled" mode returns manual with no transmission.
  * "sandbox" mode returns queued with a synthetic tracking id and
    never performs HTTP (env-gated in this process).
  * `config_summaries()` never leaks secrets — only redacted hints.
  * GET /api/billing/clearinghouse/config requires admin.
  * Enrollment CRUD round-trip + payer mirror.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


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
# 1. Adapter resolution + config summary (unit)
# ---------------------------------------------------------------------------
def _reset_chc_cache():
    """Fresh adapter instances so env changes made in-test are honoured."""
    from services.billing.clearinghouse.routing import _CACHE
    _CACHE.pop("change_healthcare", None)
    _CACHE.pop("optum", None)


def test_adapter_resolution_for_change_healthcare():
    from services.billing.clearinghouse import get_adapter_for_payer

    adapter = get_adapter_for_payer({"clearinghouse_route": "change_healthcare"})
    assert adapter.route_id == "change_healthcare"
    assert adapter.supports_edi is True


def test_adapter_resolution_for_optum_uses_separate_env_prefix():
    from services.billing.clearinghouse import get_adapter_for_payer

    adapter = get_adapter_for_payer({"clearinghouse_route": "optum"})
    assert adapter.route_id == "optum"
    # The Optum adapter inherits but must NOT share env prefix with CHC.
    assert getattr(adapter, "_env_prefix") == "CLEARINGHOUSE_OPTUM"


def test_disabled_mode_submit_returns_manual_and_logs_only():
    os.environ.pop("CLEARINGHOUSE_CHC_MODE", None)
    _reset_chc_cache()
    from services.billing.clearinghouse import get_adapter_for_payer

    adapter = get_adapter_for_payer({"clearinghouse_route": "change_healthcare"})
    result = asyncio.run(adapter.submit(
        claim_id="c-test", payload_json={}, payload_x12="",
        method="manual_portal", external_reference=None,
        payer={"clearinghouse_route": "change_healthcare",
               "enrollment_status": "not_started"},
    ))
    assert result.adapter_route == "change_healthcare"
    assert result.status == "manual"
    assert "disabled" in (result.message or "").lower()
    assert result.external_id is None


def test_sandbox_mode_submit_returns_queued_with_synthetic_id():
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    _reset_chc_cache()
    try:
        from services.billing.clearinghouse import get_adapter_for_payer
        adapter = get_adapter_for_payer({"clearinghouse_route": "change_healthcare"})
        result = asyncio.run(adapter.submit(
            claim_id="c-test", payload_json={"a": 1}, payload_x12="ISA*...",
            method="batch_file", external_reference=None,
            payer={"clearinghouse_route": "change_healthcare",
                   "enrollment_status": "enrolled"},
        ))
        assert result.adapter_route == "change_healthcare"
        assert result.status == "queued"
        assert result.external_id and result.external_id.startswith("chc-sbx-")
        assert (result.raw or {}).get("synthetic") is True
    finally:
        os.environ.pop("CLEARINGHOUSE_CHC_MODE", None)
        _reset_chc_cache()


def test_production_without_credentials_downgrades_to_disabled():
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "production"
    os.environ.pop("CLEARINGHOUSE_CHC_CLIENT_ID", None)
    os.environ.pop("CLEARINGHOUSE_CHC_CLIENT_SECRET", None)
    _reset_chc_cache()
    try:
        from services.billing.clearinghouse import get_adapter_for_payer
        adapter = get_adapter_for_payer({"clearinghouse_route": "change_healthcare"})
        assert getattr(adapter, "_mode") == "disabled"
    finally:
        os.environ.pop("CLEARINGHOUSE_CHC_MODE", None)
        _reset_chc_cache()


def test_config_summary_never_exposes_secret():
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    os.environ["CLEARINGHOUSE_CHC_CLIENT_ID"] = "SECRET_CLIENT_ID_XYZ_987654"
    os.environ["CLEARINGHOUSE_CHC_CLIENT_SECRET"] = "SECRET_SECRET_VALUE"
    _reset_chc_cache()
    try:
        from services.billing.clearinghouse import config_summaries
        summaries = config_summaries()
        chc = next(s for s in summaries if s["route_id"] == "change_healthcare")
        assert chc["mode"] == "sandbox"
        assert chc["has_client_id"] is True
        assert chc["has_client_secret"] is True
        # Must never reflect the raw secret value.
        as_str = repr(chc)
        assert "SECRET_CLIENT_ID_XYZ_987654" not in as_str
        assert "SECRET_SECRET_VALUE" not in as_str
        # Redacted hint must carry only first/last 2 chars.
        assert chc["client_id_hint"].startswith("SE")
        assert chc["client_id_hint"].endswith("54")
        assert "****" in chc["client_id_hint"]
    finally:
        for k in ("CLEARINGHOUSE_CHC_MODE",
                  "CLEARINGHOUSE_CHC_CLIENT_ID",
                  "CLEARINGHOUSE_CHC_CLIENT_SECRET"):
            os.environ.pop(k, None)
        _reset_chc_cache()


# ---------------------------------------------------------------------------
# 2. Admin endpoints — config + enrollments
# ---------------------------------------------------------------------------
def test_config_endpoint_requires_admin():
    # Doctor is a privileged role but does NOT have `clinic_settings.update`.
    s = _login(*DOCTOR)
    r = s.get(f"{API}/billing/clearinghouse/config", timeout=10)
    assert r.status_code in (401, 403), r.text


def test_config_endpoint_returns_adapter_list_for_admin():
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/clearinghouse/config", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    routes = {d["route_id"] for d in data}
    assert "change_healthcare" in routes
    assert "optum" in routes
    # `none` is intentionally filtered out — it has no config surface.
    assert "none" not in routes


def test_enrollment_upsert_roundtrip_and_payer_mirror():
    s = _login(*ADMIN)
    # Seed a payer.
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"CH Enroll Payer {uuid.uuid4().hex[:8]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15).json()

    # Create an enrollment (in_progress).
    r = s.post(f"{API}/billing/clearinghouse/enrollments", json={
        "payer_id": payer["id"],
        "clearinghouse": "change_healthcare",
        "status": "in_progress",
        "submitter_id": "SMITR-1",
        "trading_partner_id": "TP-AAA",
        "notes": "Onboarding kickoff",
    }, timeout=15)
    assert r.status_code == 201, r.text
    enroll = r.json()
    assert enroll["status"] == "in_progress"
    assert enroll["clearinghouse"] == "change_healthcare"

    # Upsert with a higher state (enrolled) — should update the row
    # and mirror to the payer record.
    r = s.post(f"{API}/billing/clearinghouse/enrollments", json={
        "payer_id": payer["id"],
        "clearinghouse": "change_healthcare",
        "status": "enrolled",
        "trading_partner_id": "TP-AAA",
    }, timeout=15)
    assert r.status_code == 201, r.text
    upserted = r.json()
    assert upserted["id"] == enroll["id"], "upsert should update existing row"
    assert upserted["status"] == "enrolled"

    # Payer now reflects the enrolled status + route.
    fresh_payer = s.get(f"{API}/billing/payers", timeout=10).json()
    fresh = next(p for p in fresh_payer if p["id"] == payer["id"])
    assert fresh["clearinghouse_route"] == "change_healthcare"
    assert fresh["enrollment_status"] == "enrolled"
    assert fresh["trading_partner_id"] == "TP-AAA"

    # PATCH to suspend.
    r = s.patch(f"{API}/billing/clearinghouse/enrollments/{enroll['id']}", json={
        "status": "suspended",
        "notes": "Temporary hold",
    }, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "suspended"


def test_enrollment_upsert_requires_existing_payer():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/clearinghouse/enrollments", json={
        "payer_id": str(uuid.uuid4()),
        "clearinghouse": "change_healthcare",
        "status": "in_progress",
    }, timeout=15)
    assert r.status_code == 404, r.text


def test_enrollment_list_filters_by_clearinghouse():
    s = _login(*ADMIN)
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"Optum Filter Payer {uuid.uuid4().hex[:8]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15).json()
    s.post(f"{API}/billing/clearinghouse/enrollments", json={
        "payer_id": payer["id"],
        "clearinghouse": "optum",
        "status": "in_progress",
    }, timeout=15)
    r = s.get(
        f"{API}/billing/clearinghouse/enrollments?clearinghouse=optum",
        timeout=10,
    )
    assert r.status_code == 200, r.text
    rows = r.json()
    assert all(row["clearinghouse"] == "optum" for row in rows), rows
    assert any(row["payer_id"] == payer["id"] for row in rows)
