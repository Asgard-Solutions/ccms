"""
Phase 6 — Change/Optum clearinghouse foundation + payer routing layer.

Covers:
  * ChangeHealthcareAdapter env parsing for envelope identity fields
    (receiver_id, receiver_name, biller_id, submitter_id) + per-service
    credentials (claims_username/password, reports_username/password).
  * `config_summary()` never exposes raw secrets — only redacted hints
    and boolean presence flags for the new Phase 6 fields.
  * `submission_identity()` returns the envelope-level identifiers.
  * Optum adapter uses a distinct `CLEARINGHOUSE_OPTUM_*` env prefix
    for the new Phase 6 fields too.
  * Payer API round-trip for Phase 6 routing fields
    (`claims_cpid`, `realtime_payer_id`, `enrollment_required`,
    `routing_metadata`, `routing_last_resolved_at`).
  * Submission records capture the Phase 6 envelope snapshot
    (receiver_id, biller_id, submitter_id), plus `st02_control_number`
    (unique per submission) and `raw_837_hash` (sha256 of 837P bytes).
  * `seed_billing` backfills Phase 6 payer fields on legacy rows
    (idempotent, safe defaults).
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid

import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

# Make the backend package importable when pytest is invoked from /app.
_BACKEND_DIR = "/app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")
DOCTOR = ("doctor@ccms.app", "Doctor@ComplianceClinic1")


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


def _reset_chc_cache():
    from services.billing.clearinghouse.routing import _CACHE
    _CACHE.pop("change_healthcare", None)
    _CACHE.pop("optum", None)


_P6_ADAPTER_KEYS = (
    "CLEARINGHOUSE_CHC_MODE",
    "CLEARINGHOUSE_CHC_CLIENT_ID",
    "CLEARINGHOUSE_CHC_CLIENT_SECRET",
    "CLEARINGHOUSE_CHC_RECEIVER_ID",
    "CLEARINGHOUSE_CHC_RECEIVER_NAME",
    "CLEARINGHOUSE_CHC_BILLER_ID",
    "CLEARINGHOUSE_CHC_SUBMITTER_ID",
    "CLEARINGHOUSE_CHC_CLAIMS_USERNAME",
    "CLEARINGHOUSE_CHC_CLAIMS_PASSWORD",
    "CLEARINGHOUSE_CHC_REPORTS_USERNAME",
    "CLEARINGHOUSE_CHC_REPORTS_PASSWORD",
    "CLEARINGHOUSE_OPTUM_RECEIVER_ID",
    "CLEARINGHOUSE_OPTUM_BILLER_ID",
    "CLEARINGHOUSE_OPTUM_SUBMITTER_ID",
)


def _clear_p6_env():
    for k in _P6_ADAPTER_KEYS:
        os.environ.pop(k, None)
    _reset_chc_cache()


# ---------------------------------------------------------------------------
# 1. Adapter — Phase 6 env parsing + redacted config summary
# ---------------------------------------------------------------------------
def test_adapter_parses_phase6_envelope_identity_env_vars():
    _clear_p6_env()
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    os.environ["CLEARINGHOUSE_CHC_RECEIVER_ID"] = "RECV-123"
    os.environ["CLEARINGHOUSE_CHC_RECEIVER_NAME"] = "CHANGE HEALTHCARE"
    os.environ["CLEARINGHOUSE_CHC_BILLER_ID"] = "BILL-777"
    os.environ["CLEARINGHOUSE_CHC_SUBMITTER_ID"] = "SUBM-999"
    try:
        from services.billing.clearinghouse import get_adapter_for_payer
        adapter = get_adapter_for_payer(
            {"clearinghouse_route": "change_healthcare"},
        )
        identity = adapter.submission_identity()
        assert identity["adapter_route"] == "change_healthcare"
        assert identity["receiver_id"] == "RECV-123"
        assert identity["receiver_name"] == "CHANGE HEALTHCARE"
        assert identity["biller_id"] == "BILL-777"
        assert identity["submitter_id"] == "SUBM-999"
    finally:
        _clear_p6_env()


def test_adapter_submission_identity_defaults_to_none_when_env_absent():
    _clear_p6_env()
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    try:
        from services.billing.clearinghouse import get_adapter_for_payer
        adapter = get_adapter_for_payer(
            {"clearinghouse_route": "change_healthcare"},
        )
        identity = adapter.submission_identity()
        assert identity["receiver_id"] is None
        assert identity["receiver_name"] is None
        assert identity["biller_id"] is None
        assert identity["submitter_id"] is None
        # adapter_route is always present so `claim_submissions` knows
        # which adapter produced the row even when creds are blank.
        assert identity["adapter_route"] == "change_healthcare"
    finally:
        _clear_p6_env()


def test_adapter_parses_phase6_service_credentials():
    _clear_p6_env()
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    os.environ["CLEARINGHOUSE_CHC_CLAIMS_USERNAME"] = "claims_user_9876"
    os.environ["CLEARINGHOUSE_CHC_CLAIMS_PASSWORD"] = "SUPER_CLAIMS_PWD"
    os.environ["CLEARINGHOUSE_CHC_REPORTS_USERNAME"] = "reports_user_5432"
    os.environ["CLEARINGHOUSE_CHC_REPORTS_PASSWORD"] = "SUPER_REPORTS_PWD"
    try:
        from services.billing.clearinghouse import config_summaries
        summaries = config_summaries()
        chc = next(s for s in summaries if s["route_id"] == "change_healthcare")
        assert chc["has_claims_username"] is True
        assert chc["has_claims_password"] is True
        assert chc["has_reports_username"] is True
        assert chc["has_reports_password"] is True
        # Hint strings must be redacted — first/last two chars + stars.
        assert chc["claims_username_hint"] and "****" in chc["claims_username_hint"]
        assert chc["claims_username_hint"].startswith("cl")
        assert chc["claims_username_hint"].endswith("76")
        assert chc["reports_username_hint"] and "****" in chc["reports_username_hint"]
        # Raw passwords must NEVER appear anywhere in the summary.
        as_str = repr(chc)
        assert "SUPER_CLAIMS_PWD" not in as_str
        assert "SUPER_REPORTS_PWD" not in as_str
        assert "claims_user_9876" not in as_str
        assert "reports_user_5432" not in as_str
    finally:
        _clear_p6_env()


def test_adapter_config_summary_omits_credentials_when_absent():
    _clear_p6_env()
    try:
        from services.billing.clearinghouse import config_summaries
        summaries = config_summaries()
        chc = next(s for s in summaries if s["route_id"] == "change_healthcare")
        # Phase 6 fields must exist in the summary shape (so the UI
        # can render placeholders), even when nothing is configured.
        for k in (
            "has_claims_username", "has_claims_password",
            "has_reports_username", "has_reports_password",
            "receiver_id", "receiver_name", "biller_id", "submitter_id",
            "claims_username_hint", "reports_username_hint",
        ):
            assert k in chc, f"Missing Phase 6 summary field: {k}"
        assert chc["has_claims_username"] is False
        assert chc["has_claims_password"] is False
        assert chc["has_reports_username"] is False
        assert chc["has_reports_password"] is False
        assert chc["receiver_id"] is None
        assert chc["biller_id"] is None
        assert chc["submitter_id"] is None
        assert chc["claims_username_hint"] is None
        assert chc["reports_username_hint"] is None
    finally:
        _clear_p6_env()


def test_optum_adapter_uses_separate_env_prefix_for_phase6():
    _clear_p6_env()
    os.environ["CLEARINGHOUSE_CHC_MODE"] = "sandbox"
    os.environ["CLEARINGHOUSE_CHC_RECEIVER_ID"] = "CHC-RECV"
    os.environ["CLEARINGHOUSE_OPTUM_RECEIVER_ID"] = "OPTUM-RECV"
    os.environ["CLEARINGHOUSE_OPTUM_BILLER_ID"] = "OPTUM-BILL"
    try:
        from services.billing.clearinghouse import get_adapter_for_payer
        chc = get_adapter_for_payer(
            {"clearinghouse_route": "change_healthcare"},
        )
        optum = get_adapter_for_payer({"clearinghouse_route": "optum"})
        # Each adapter is isolated; the Optum identity must NOT pick
        # up the CHC env values.
        chc_ident = chc.submission_identity()
        optum_ident = optum.submission_identity()
        assert chc_ident["receiver_id"] == "CHC-RECV"
        assert optum_ident["receiver_id"] == "OPTUM-RECV"
        assert optum_ident["biller_id"] == "OPTUM-BILL"
        # route_id disambiguates them on the claim_submissions row.
        assert chc_ident["adapter_route"] == "change_healthcare"
        assert optum_ident["adapter_route"] == "optum"
    finally:
        _clear_p6_env()


# ---------------------------------------------------------------------------
# 2. Payer — Phase 6 routing fields round-trip
# ---------------------------------------------------------------------------
def test_payer_accepts_phase6_routing_fields_on_create():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/payers", json={
        "name": f"P6 Router {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
        "clearinghouse_route": "change_healthcare",
        "claim_submission_mode": "edi",
        "enrollment_status": "in_progress",
        "trading_partner_id": "TP-ZZ-1",
        "claims_cpid": "CPID-7777",
        "realtime_payer_id": "RT-8888",
        "enrollment_required": True,
        "routing_metadata": {"region": "NY", "version": "v1"},
    }, timeout=15)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["claims_cpid"] == "CPID-7777"
    assert p["realtime_payer_id"] == "RT-8888"
    assert p["enrollment_required"] is True
    assert p["routing_metadata"] == {"region": "NY", "version": "v1"}
    # Cache field defaults to None on create — only populated when the
    # adapter resolves and caches a value.
    assert p["routing_last_resolved_at"] is None


def test_payer_phase6_fields_default_safely_when_omitted():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/payers", json={
        "name": f"P6 Legacy {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["claims_cpid"] is None
    assert p["realtime_payer_id"] is None
    assert p["enrollment_required"] is False
    assert p["routing_metadata"] is None
    assert p["routing_last_resolved_at"] is None


def test_payer_phase6_fields_updatable_via_patch():
    s = _login(*ADMIN)
    created = s.post(f"{API}/billing/payers", json={
        "name": f"P6 Patch {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15).json()
    r = s.patch(f"{API}/billing/payers/{created['id']}", json={
        "claims_cpid": "CPID-2222",
        "realtime_payer_id": "RT-3333",
        "enrollment_required": True,
        "routing_metadata": {"note": "updated"},
    }, timeout=15)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["claims_cpid"] == "CPID-2222"
    assert p["realtime_payer_id"] == "RT-3333"
    assert p["enrollment_required"] is True
    assert p["routing_metadata"] == {"note": "updated"}


def test_payer_rejects_overlong_claims_cpid():
    s = _login(*ADMIN)
    r = s.post(f"{API}/billing/payers", json={
        "name": f"P6 Bad CPID {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
        "claims_cpid": "X" * 41,  # max_length=40
    }, timeout=15)
    assert r.status_code == 422, r.text


# ---------------------------------------------------------------------------
# 3. Submission — ST02 + 837 hash + envelope identity snapshot in DB
# ---------------------------------------------------------------------------
def _seed_submittable_claim(s, *, payer_overrides=None):
    """Build a claim end-to-end and push it to `ready` via validate."""
    payer_body = {
        "name": f"P6 Submit Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }
    if payer_overrides:
        payer_body.update(payer_overrides)
    payer = s.post(f"{API}/billing/payers", json=payer_body, timeout=15).json()

    patient = s.post(f"{API}/patients", json={
        "first_name": "P6", "last_name": f"Sub{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"p6-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()

    policy = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary",
        "subscriber_name": "P6 Subscriber",
        "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()

    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "policy_id": policy["id"],
        "claim_type": "professional", "place_of_service": "11",
        "frequency_code": "1",
        "billing_provider_id": "1234567890",
        "rendering_provider_id": "1234567890",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M99.01"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 5500, "diagnosis_pointers": [1],
            "modifiers": ["AT"],
        }],
    }, timeout=15).json()

    # Run the scrubber so the claim advances to `ready`.
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)
    return claim, payer


def _fetch_latest_submission_row(claim_id: str) -> dict:
    """Hit Mongo directly to read the raw `claim_submissions` row —
    the API response model strips extra fields (`extra="ignore"`)."""
    from motor.motor_asyncio import AsyncIOMotorClient

    mongo_url = os.environ["MONGO_URL"]
    db_name = os.environ["DB_NAME"]

    async def _find():
        client = AsyncIOMotorClient(mongo_url)
        try:
            rows = await client[db_name].claim_submissions.find(
                {"claim_id": claim_id}, {"_id": 0},
            ).sort([("submitted_at", -1)]).to_list(10)
            return rows
        finally:
            client.close()

    return asyncio.run(_find())


def test_submission_captures_phase6_identity_and_control_numbers():
    s = _login(*ADMIN)
    claim, _ = _seed_submittable_claim(s)
    r = s.post(
        f"{API}/billing/claims/{claim['id']}/submissions",
        json={"method": "manual_portal"}, timeout=15,
    )
    assert r.status_code == 201, r.text

    rows = _fetch_latest_submission_row(claim["id"])
    assert rows, "expected at least one claim_submissions row"
    row = rows[0]

    # ST02 monotonically-unique per submission, 9 numeric digits.
    assert "st02_control_number" in row
    assert isinstance(row["st02_control_number"], str)
    assert row["st02_control_number"].isdigit()
    assert len(row["st02_control_number"]) == 9

    # Raw 837 hash is a hex sha256 of the persisted payload_x12.
    assert row.get("raw_837_hash")
    expected_hash = hashlib.sha256(
        (row.get("payload_x12") or "").encode("utf-8"),
    ).hexdigest()
    assert row["raw_837_hash"] == expected_hash

    # Envelope identity snapshot keys always present. None of the
    # CLEARINGHOUSE_CHC_* envs are set by default, so values are None
    # for a manual (NoneAdapter) submission — but the KEYS must be on
    # the row so the UI / audit replayer can rely on the schema.
    for k in ("receiver_id", "receiver_name", "biller_id", "submitter_id"):
        assert k in row, f"Missing Phase 6 snapshot field on submission: {k}"


def test_two_submissions_get_distinct_st02_control_numbers():
    s = _login(*ADMIN)
    claim1, _ = _seed_submittable_claim(s)
    claim2, _ = _seed_submittable_claim(s)
    r1 = s.post(
        f"{API}/billing/claims/{claim1['id']}/submissions",
        json={"method": "manual_portal"}, timeout=15,
    )
    r2 = s.post(
        f"{API}/billing/claims/{claim2['id']}/submissions",
        json={"method": "manual_portal"}, timeout=15,
    )
    assert r1.status_code == 201 and r2.status_code == 201

    row1 = _fetch_latest_submission_row(claim1["id"])[0]
    row2 = _fetch_latest_submission_row(claim2["id"])[0]
    assert row1["st02_control_number"] != row2["st02_control_number"]


# ---------------------------------------------------------------------------
# 4. Seed backfill — Phase 6 payer fields
# ---------------------------------------------------------------------------
def test_seed_backfills_phase6_fields_on_legacy_payer_rows():
    """Insert a payer row missing every Phase 6 field, run the seed,
    verify the safe defaults get written in idempotently."""
    from core.db import get_db_write
    from services.billing.seed import seed_billing

    legacy_id = str(uuid.uuid4())

    async def _run():
        db = get_db_write()
        await db.billing_payers.insert_one({
            "id": legacy_id,
            "tenant_id": None,   # system row; seed backfill isn't tenant-gated
            "name": f"LEGACY P6 {legacy_id[:6]}",
            "payer_type": "commercial",
            "remit_method": "era",
            "status": "active",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_at": "2025-01-01T00:00:00+00:00",
            # Intentionally omit all Phase 6 fields:
            #   claims_cpid, realtime_payer_id, enrollment_required,
            #   routing_metadata, routing_last_resolved_at.
        })
        try:
            await seed_billing()
            fresh = await db.billing_payers.find_one(
                {"id": legacy_id}, {"_id": 0},
            )
            assert fresh["claims_cpid"] is None
            assert fresh["realtime_payer_id"] is None
            assert fresh["enrollment_required"] is False
            assert fresh["routing_metadata"] is None
            assert fresh["routing_last_resolved_at"] is None

            # Idempotency — second run must not flip the defaults.
            await seed_billing()
            fresh2 = await db.billing_payers.find_one(
                {"id": legacy_id}, {"_id": 0},
            )
            assert fresh2["enrollment_required"] is False
            assert fresh2["claims_cpid"] is None
        finally:
            await db.billing_payers.delete_one({"id": legacy_id})

    asyncio.run(_run())
