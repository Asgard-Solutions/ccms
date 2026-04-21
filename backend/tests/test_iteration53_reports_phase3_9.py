"""Iteration 53 — Phase 3-9 Reports expansion smoke tests.

Covers the deltas from the review request:
- Catalog shape (22 reports / 7 categories, exact counts).
- Each of the 12 new reports executes via POST /run (200 + shape).
- Saved-view column whitelist (POST + PATCH with bad columns -> 400).
- Tenant isolation of saved views.
- PHI flag regression.
"""
import os
import uuid
import requests
import pytest
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN = {"email": "admin@ccms.app", "password": "Admin@ComplianceClinic1"}
TENANT_B_ADMIN = {"email": "group-admin@sunrise.ccms.app", "password": "Sunrise@ComplianceClinic1"}

EXPECTED_CATEGORY_COUNTS = {
    "Operational": 3, "Clinical": 2, "Financial": 6, "Compliance": 5,
    "Patient": 3, "Scheduling": 1, "Workforce": 2,
}

NEW_REPORTS = [
    "new_patients", "patient_contact_completeness", "active_patients_summary",
    "cancellations_no_shows", "notes_by_provider", "payments_by_method_summary",
    "patient_balance", "phi_access_activity", "failed_logins",
    "export_history", "user_last_login", "workforce_invitations",
]


def _login_and_reauth(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=15)
    assert r.status_code == 200, r.text
    r2 = s.post(f"{API}/auth/reauth", json={"password": creds["password"]}, timeout=15)
    assert r2.status_code == 200, r2.text
    return s


@pytest.fixture(scope="module")
def admin_client():
    return _login_and_reauth(ADMIN)


@pytest.fixture(scope="module")
def tenant_b_client():
    try:
        return _login_and_reauth(TENANT_B_ADMIN)
    except Exception as e:
        pytest.skip(f"Tenant B admin not available: {e}")


def _flatten_catalog(data):
    """Return list of report dicts from catalog response."""
    cats = data.get("categories", [])
    reports = []
    for cat in cats:
        for rep in cat.get("reports", []):
            reports.append({**rep, "category": cat.get("category")})
    return reports


# ---------- Catalog ----------
def test_catalog_total_and_category_counts(admin_client):
    r = admin_client.get(f"{API}/reports/catalog", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("total") == 22, f"expected total=22, got {data.get('total')}"
    counts = {c["category"]: len(c["reports"]) for c in data["categories"]}
    for cat, cnt in EXPECTED_CATEGORY_COUNTS.items():
        assert counts.get(cat) == cnt, f"{cat}: expected {cnt}, got {counts.get(cat)}"


# ---------- Each new report executes ----------
@pytest.mark.parametrize("name", NEW_REPORTS)
def test_new_report_runs(admin_client, name):
    r = admin_client.post(
        f"{API}/reports/{name}/run",
        json={"filters": {}, "page": 1, "page_size": 5},
        timeout=25,
    )
    assert r.status_code == 200, f"{name}: {r.status_code} {r.text[:300]}"
    body = r.json()
    assert "rows" in body, f"{name}: missing 'rows' key, got {list(body.keys())}"
    assert isinstance(body["rows"], list)


# ---------- Saved-view column whitelist ----------
def test_saved_view_rejects_unknown_column_on_create(admin_client):
    payload = {
        "name": f"TEST_bad_{uuid.uuid4().hex[:6]}",
        "columns": ["definitely_not_a_real_column_xyz"],
        "filters": {},
    }
    r = admin_client.post(f"{API}/reports/new_patients/views", json=payload, timeout=10)
    assert r.status_code == 400, r.text
    assert "unknown columns" in r.text.lower()


def test_saved_view_rejects_unknown_column_on_patch(admin_client):
    create = admin_client.post(
        f"{API}/reports/new_patients/views",
        json={"name": f"TEST_valid_{uuid.uuid4().hex[:6]}", "columns": [], "filters": {}},
        timeout=10,
    )
    assert create.status_code in (200, 201), create.text
    body = create.json()
    vid = body.get("id") or body.get("view", {}).get("id")
    assert vid, body
    try:
        r = admin_client.patch(
            f"{API}/reports/views/{vid}",
            json={"columns": ["bogus_column_abc"]},
            timeout=10,
        )
        assert r.status_code == 400, r.text
        assert "unknown columns" in r.text.lower()
    finally:
        admin_client.delete(f"{API}/reports/views/{vid}", timeout=10)


# ---------- Tenant isolation ----------
def test_tenant_isolation_saved_views(admin_client, tenant_b_client):
    vname = f"TEST_iso_{uuid.uuid4().hex[:6]}"
    r = admin_client.post(
        f"{API}/reports/new_patients/views",
        json={"name": vname, "columns": [], "filters": {}},
        timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    vid = r.json().get("id") or r.json().get("view", {}).get("id")
    try:
        lst = tenant_b_client.get(f"{API}/reports/new_patients/views", timeout=10)
        assert lst.status_code == 200, lst.text
        body = lst.json()
        items = body if isinstance(body, list) else body.get("views", body.get("items", []))
        names = [v.get("name") for v in items]
        assert vname not in names, f"Tenant B leaked view from Tenant A: {names}"
    finally:
        admin_client.delete(f"{API}/reports/new_patients/views/{vid}", timeout=10)


# ---------- PHI flag regression ----------
def test_catalog_marks_phi_reports(admin_client):
    """At least one report must be flagged contains_phi=True (for consent flow)
    and at least one must be non-PHI (skips consent)."""
    r = admin_client.get(f"{API}/reports/catalog", timeout=10)
    reports = _flatten_catalog(r.json())
    phi_reports = [rep["name"] for rep in reports if rep.get("contains_phi")]
    non_phi_reports = [rep["name"] for rep in reports if not rep.get("contains_phi")]
    assert len(phi_reports) >= 1, "expected at least one PHI-flagged report for consent flow"
    assert len(non_phi_reports) >= 1, "expected at least one non-PHI report"
