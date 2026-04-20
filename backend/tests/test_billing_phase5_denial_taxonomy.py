"""
Billing Phase 5 — denial taxonomy (denial_category).

Covers:
  * `derive_category()` happy paths + normalization
  * Auto-tagging on remittance post (claim-level + line-level denials)
  * List filter by category + unknown category → 400
  * Operator override via PUT endpoint + unknown category → 400
  * Category summary aggregation (open/in_progress/escalated by default;
    include_closed switches the lens)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
import requests
from dotenv import load_dotenv

from services.billing.denial_categories import (
    DENIAL_CATEGORIES,
    DENIAL_CATEGORY_LABELS,
    derive_category,
    normalize_code,
)

load_dotenv("/app/backend/.env")
API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
DEFAULT_ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200
    access = r.cookies.get("access_token")
    if access:
        s.headers["Authorization"] = f"Bearer {access}"
    rr = s.post(f"{API}/auth/reauth",
                json={"password": password}, timeout=10)
    if rr.status_code == 200:
        tok = rr.json().get("reauth_token")
        if tok:
            s.headers["x-reauth-token"] = tok
    return s


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _build_submitted_claim(s):
    patients = s.get(f"{API}/patients", timeout=15).json()
    patient = patients[0]
    # ensure self-pay schedule
    scheds = s.get(f"{API}/billing/fee-schedules", timeout=10).json()
    if not any(x["kind"] == "self_pay" and x["active"] for x in scheds):
        s.post(f"{API}/billing/fee-schedules",
               json={"name": _unique("SP"), "kind": "self_pay"}, timeout=10)
    payer = s.post(f"{API}/billing/payers", json={
        "name": _unique("Pt"), "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=10).json()
    sched = s.post(f"{API}/billing/fee-schedules", json={
        "name": _unique("Pt5"), "kind": "payer", "payer_id": payer["id"],
    }, timeout=10).json()
    s.put(f"{API}/billing/fee-schedules/{sched['id']}/lines", json=[
        {"code_type": "cpt", "code": "98940", "allowed_cents": 4000},
    ], timeout=10)
    s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": patient["id"], "payer_id": payer["id"],
        "rank": "primary", "subscriber_name": "Sub",
        "member_id": "M-" + uuid.uuid4().hex[:8],
    }, timeout=10)
    rec = s.post(f"{API}/patients/{patient['id']}/records", json={
        "record_type": "treatment", "title": "T", "description": "x",
        "diagnosis": "LBP", "treatment": "CMT",
    }, timeout=10).json()
    s.put(f"{API}/patients/{patient['id']}/records/{rec['id']}/coding", json={
        "procedures": [{"code_type": "cpt", "code": "98940", "units": 1, "modifiers": []}],
        "diagnoses": [{"sequence": 1, "code": "M54.16"}],
        "responsibility": "insurance",
    }, timeout=10)
    s.post(f"{API}/patients/{patient['id']}/records/{rec['id']}/sign", timeout=10)
    inv = s.post(f"{API}/billing/encounters/{rec['id']}/capture", timeout=10).json()
    claim = s.post(f"{API}/billing/claims/from-invoice/{inv['id']}", timeout=10).json()
    s.put(f"{API}/billing/claims/{claim['id']}/header", json={
        "billing_provider_id": "bp", "rendering_provider_id": "rp",
        "place_of_service": "11",
    }, timeout=10)
    s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=10)
    s.post(f"{API}/billing/claims/{claim['id']}/submissions",
           json={"method": "manual_portal"}, timeout=10)
    fresh = s.get(f"{API}/billing/claims/{claim['id']}/detail",
                  timeout=10).json()["claim"]
    return fresh


def _post_denial(s, claim, code: str | None):
    """Post a 100%-denied remittance for `claim` and return the
    resulting denial work item."""
    r = s.post(f"{API}/billing/remittances", json={
        "payer_id": claim["payer_id"],
        "received_at": datetime.now(timezone.utc).date().isoformat(),
        "total_paid_cents": 0,
        "claims": [{
            "claim_id": claim["id"],
            "billed_cents": int(claim["billed_cents"]),
            "paid_cents": 0, "contractual_cents": 0,
            "patient_resp_cents": 0,
            "denied_cents": int(claim["billed_cents"]),
            "denial_code": code,
        }],
    }, timeout=10)
    assert r.status_code == 201, r.text
    items = s.get(f"{API}/billing/denial-work-items", timeout=10).json()
    return [i for i in items if i["claim_id"] == claim["id"]][0]


# ---------------------------------------------------------------------------
# Unit — category derivation
# ---------------------------------------------------------------------------
class TestDerivation:
    def test_known_codes_map_to_expected_category(self):
        assert derive_category("CO-97") == "coding"
        assert derive_category("CO-29") == "timely_filing"
        assert derive_category("CO-18") == "duplicate"
        assert derive_category("CO-197") == "authorization"
        assert derive_category("CO-26") == "eligibility"

    def test_unknown_and_empty_codes_fall_through(self):
        assert derive_category("CO-9999") == "other"
        assert derive_category(None) == "other"
        assert derive_category("") == "other"

    def test_normalize_handles_bare_numbers_and_missing_dash(self):
        assert normalize_code("97") == "CO-97"
        assert normalize_code("co97") == "CO-97"
        assert normalize_code("co-97") == "CO-97"
        # Derive keeps working through normalization.
        assert derive_category("97") == "coding"
        assert derive_category("co97") == "coding"

    def test_categories_and_labels_are_in_sync(self):
        for cat in DENIAL_CATEGORIES:
            assert cat in DENIAL_CATEGORY_LABELS
        assert len(DENIAL_CATEGORIES) == len(set(DENIAL_CATEGORIES))


# ---------------------------------------------------------------------------
# Integration — auto-tagging at posting
# ---------------------------------------------------------------------------
class TestAutoTagging:
    def test_claim_level_denial_gets_category(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        item = _post_denial(s, claim, "CO-97")
        assert item["denial_category"] == "coding"

    def test_unknown_code_still_tagged_other(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        item = _post_denial(s, claim, "CO-9999")
        assert item["denial_category"] == "other"

    def test_missing_code_maps_to_other(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        item = _post_denial(s, claim, None)
        assert item["denial_category"] == "other"
        assert item["denial_code"] == "UNSPECIFIED"


# ---------------------------------------------------------------------------
# Integration — list filter + override + summary
# ---------------------------------------------------------------------------
class TestListFilterAndOverride:
    def test_list_filters_by_category(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        _post_denial(s, claim, "CO-29")   # timely_filing
        rows = s.get(f"{API}/billing/denial-work-items",
                     params={"category": "timely_filing"},
                     timeout=10).json()
        assert rows, "expected at least one timely_filing row"
        assert all(r["denial_category"] == "timely_filing" for r in rows)

    def test_unknown_category_filter_returns_400(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/billing/denial-work-items",
                  params={"category": "nonsense"}, timeout=10)
        assert r.status_code == 400

    def test_operator_can_override_category(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        item = _post_denial(s, claim, "CO-97")  # derived coding
        # Override to authorization.
        r = s.put(f"{API}/billing/denial-work-items/{item['id']}",
                  json={"denial_category": "authorization"},
                  timeout=10)
        assert r.status_code == 200, r.text
        assert r.json()["denial_category"] == "authorization"

    def test_override_rejects_unknown_category(self):
        s = _login(*DEFAULT_ADMIN)
        claim = _build_submitted_claim(s)
        item = _post_denial(s, claim, "CO-97")
        r = s.put(f"{API}/billing/denial-work-items/{item['id']}",
                  json={"denial_category": "not-a-thing"}, timeout=10)
        assert r.status_code == 400


class TestCategorySummary:
    def test_summary_returns_all_categories_even_when_zero(self):
        s = _login(*DEFAULT_ADMIN)
        r = s.get(f"{API}/billing/denial-work-items/category-summary",
                  timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["rows"]) == len(DENIAL_CATEGORIES)
        keys = {row["category"] for row in body["rows"]}
        assert keys == set(DENIAL_CATEGORIES)

    def test_summary_increments_on_new_denial(self):
        s = _login(*DEFAULT_ADMIN)
        before = s.get(f"{API}/billing/denial-work-items/category-summary",
                       timeout=10).json()
        prev = next(r["count"] for r in before["rows"]
                    if r["category"] == "timely_filing")
        claim = _build_submitted_claim(s)
        _post_denial(s, claim, "CO-29")
        after = s.get(f"{API}/billing/denial-work-items/category-summary",
                      timeout=10).json()
        now = next(r["count"] for r in after["rows"]
                   if r["category"] == "timely_filing")
        assert now >= prev + 1

    def test_include_closed_toggle(self):
        s = _login(*DEFAULT_ADMIN)
        open_only = s.get(
            f"{API}/billing/denial-work-items/category-summary",
            timeout=10,
        ).json()
        all_rows = s.get(
            f"{API}/billing/denial-work-items/category-summary",
            params={"include_closed": "true"}, timeout=10,
        ).json()
        # With-closed must be >= open-only on total count.
        assert all_rows["total_count"] >= open_only["total_count"]
