"""
Phase-UI — Paginated claims queue endpoint.

Covers:
  * Envelope shape: rows / summary / tab_counts / filter_options.
  * Pagination & sorting round-trip.
  * Human-friendly enrichment fields (patient_name, payer_name,
    assignee_name, last_event).
  * Tab-count accuracy.
  * Empty / no-results differentiation via `total`.
  * 404 on unknown tab.
"""
from __future__ import annotations

import os
import uuid

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE}/api" if BASE else "http://localhost:8001/api"

ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


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


def _seed_claim(s, *, status_target="draft") -> tuple[dict, dict, dict]:
    """Create payer + patient + policy + claim. Returns (claim, patient, payer)."""
    payer = s.post(f"{API}/billing/payers", json={
        "name": f"Queue V2 Payer {uuid.uuid4().hex[:6]}",
        "payer_type": "commercial",
        "remit_method": "era",
    }, timeout=15).json()
    pt = s.post(f"{API}/patients", json={
        "first_name": "QueueFirst",
        "last_name": f"QueueLast{uuid.uuid4().hex[:4]}",
        "date_of_birth": "1990-01-01",
        "email": f"qv2-{uuid.uuid4().hex[:6]}@example.com",
    }, timeout=15).json()
    pol = s.post(f"{API}/billing/insurance-policies", json={
        "patient_id": pt["id"], "payer_id": payer["id"],
        "rank": "primary",
        "subscriber_name": "Queue V2",
        "relationship_to_subscriber": "self",
        "member_id": f"M-{uuid.uuid4().hex[:6]}",
    }, timeout=15).json()
    pos = "" if status_target == "validation_failed" else "11"
    claim = s.post(f"{API}/billing/claims", json={
        "patient_id": pt["id"], "payer_id": payer["id"], "policy_id": pol["id"],
        "claim_type": "professional",
        "place_of_service": pos or None,
        "frequency_code": "1",
        "billing_provider_id": "BP-V2", "rendering_provider_id": "RP-V2",
        "service_date_from": "2026-04-10",
        "service_date_to":   "2026-04-10",
        "diagnoses": [{"sequence": 1, "code": "M54.5"}],
        "lines": [{
            "sequence": 1, "service_date": "2026-04-10",
            "code_type": "cpt", "code": "98940", "units": 1,
            "billed_cents": 7700, "diagnosis_pointers": [1],
        }],
    }, timeout=15).json()
    if status_target == "validation_failed":
        s.post(f"{API}/billing/claims/{claim['id']}/validate", timeout=15)
    return claim, pt, payer


def test_queue_v2_envelope_shape():
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/claims/queue?tab=all&page=1&page_size=10", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.keys()) >= {
        "tab", "page", "page_size", "total", "sort",
        "rows", "summary", "tab_counts", "billed_totals",
        "filter_options",
    }
    assert data["tab"] == "all"
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert isinstance(data["rows"], list)
    assert set(data["summary"].keys()) >= {
        "shown", "ready", "needs_fixes", "billed_total_cents", "total",
    }
    assert set(data["tab_counts"].keys()) >= {
        "all", "pending-submission", "needs-fixes", "rejected", "follow-up",
    }
    assert "payers" in data["filter_options"]
    assert "assignees" in data["filter_options"]


def test_queue_v2_enriches_rows_with_human_friendly_fields():
    s = _login(*ADMIN)
    claim, patient, payer = _seed_claim(s, status_target="draft")
    # Target page should include our brand new claim — sort by
    # updated_at:desc puts it first.
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page=1&page_size=25"
        f"&sort=updated_at:desc",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    row = next((x for x in rows if x["id"] == claim["id"]), None)
    assert row is not None, "seeded claim not on first page"
    expected_name = f"{patient['first_name']} {patient['last_name']}"
    assert row["patient_name"] == expected_name
    assert row["payer_name"] == payer["name"]
    assert row["last_event"] in ("created", "validated"), row["last_event"]


def test_queue_v2_pagination_and_sort():
    s = _login(*ADMIN)
    # Use a tiny page size so pagination is exercised without seeding.
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&page=1&page_size=1"
        f"&sort=billed_cents:asc",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["page_size"] == 1
    assert len(data["rows"]) <= 1
    assert data["sort"].startswith("billed_cents:")
    if data["total"] >= 2:
        a = data["rows"][0]["billed_cents"]
        r2 = s.get(
            f"{API}/billing/claims/queue?tab=all&page=2&page_size=1"
            f"&sort=billed_cents:asc",
            timeout=15,
        )
        b = r2.json()["rows"][0]["billed_cents"]
        assert a <= b, (a, b)


def test_queue_v2_tab_counts_sum_logic():
    s = _login(*ADMIN)
    # Make at least one validation_failed and one draft so counts change.
    _seed_claim(s, status_target="validation_failed")
    _seed_claim(s, status_target="draft")
    r = s.get(f"{API}/billing/claims/queue?tab=all", timeout=15).json()
    tc = r["tab_counts"]
    # `all` is >= each named queue.
    assert tc["all"] >= tc["pending-submission"]
    assert tc["all"] >= tc["rejected"]
    assert tc["all"] >= tc["follow-up"]
    assert tc["needs-fixes"] >= 1


def test_queue_v2_billed_totals_are_real_and_filter_aware():
    """Phase 12 — per-tab `billed_totals` mirror `tab_counts` and sum
    `billed_cents` over the filter-aware tab query."""
    s = _login(*ADMIN)
    claim, _, payer = _seed_claim(s, status_target="draft")
    r = s.get(f"{API}/billing/claims/queue?tab=all", timeout=15).json()
    bt = r["billed_totals"]
    tc = r["tab_counts"]
    # Same keys as tab_counts.
    assert set(bt.keys()) == set(tc.keys())
    # Each entry is a non-negative int.
    for k, v in bt.items():
        assert isinstance(v, int) and v >= 0, (k, v)
    # `all` total >= named tabs.
    for named in ("pending-submission", "rejected", "follow-up"):
        assert bt["all"] >= bt[named]
    # Filter-aware: scoping by a bogus payer zeroes the current summary
    # + all tab counters.
    r2 = s.get(
        f"{API}/billing/claims/queue?tab=all&payer_id={uuid.uuid4()}",
        timeout=15,
    ).json()
    assert all(v == 0 for v in r2["billed_totals"].values())
    assert r2["summary"]["billed_total_cents"] == 0
    # Filter-aware positive path: scoping by the real payer surfaces
    # at least the just-seeded claim's billed_cents.
    r3 = s.get(
        f"{API}/billing/claims/queue?tab=all&payer_id={payer['id']}",
        timeout=15,
    ).json()
    assert r3["billed_totals"]["all"] >= claim["billed_cents"] > 0



def test_queue_v2_no_results_vs_empty():
    s = _login(*ADMIN)
    # Apply a filter that cannot match — ensures summary reflects 0
    # total without 404ing.
    bogus = str(uuid.uuid4())
    r = s.get(
        f"{API}/billing/claims/queue?tab=all&payer_id={bogus}",
        timeout=15,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 0
    assert data["rows"] == []
    assert data["summary"]["shown"] == 0
    # Filter options list still populated.
    assert isinstance(data["filter_options"]["payers"], list)


def test_queue_v2_unknown_tab_returns_404():
    s = _login(*ADMIN)
    r = s.get(f"{API}/billing/claims/queue?tab=bogus_tab", timeout=10)
    assert r.status_code == 404, r.text


def test_queue_v2_requires_auth():
    r = requests.get(f"{API}/billing/claims/queue", timeout=10)
    assert r.status_code in (401, 403), r.text
