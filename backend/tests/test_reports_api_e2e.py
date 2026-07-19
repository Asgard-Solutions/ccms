"""End-to-end API tests for the Reports section.

Covers:
  * /api/reports/catalog (needs reauth_token cookie)
  * /api/reports/{name}  metadata contract
  * /api/reports/{name}/run for several reports
  * /api/reports/{name}/export — non-PHI happy path (denials_log)
  * /api/reports/{name}/export — PHI gate (admin lacks export_phi)
  * Saved views CRUD
  * Download path with correct mime + filename
  * Audit trail rows
"""
from __future__ import annotations

import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fall back to local preview if env var missing in this shell
    BASE_URL = "https://phi-safe-clinical-ui.preview.emergentagent.com"

ADMIN_EMAIL = "admin@ccms.app"
ADMIN_PASSWORD = "Admin@ComplianceClinic1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.verify = False
    s.headers.update({"Content-Type": "application/json"})
    # login
    r = s.post(f"{BASE_URL}/api/auth/login",
               json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed {r.status_code} {r.text[:200]}"
    data = r.json()
    if data.get("mfa_required"):
        pytest.skip("admin requires MFA ticket - cannot continue")
    # step-up reauth (reports routes require this)
    r2 = s.post(f"{BASE_URL}/api/auth/reauth",
                json={"password": ADMIN_PASSWORD})
    assert r2.status_code == 200, f"reauth failed {r2.status_code} {r2.text[:200]}"
    return s


@pytest.fixture(scope="module")
def tenant_scope(admin_session):
    """Pick any tenant + location to pin admin into tenant scope."""
    r = admin_session.get(f"{BASE_URL}/api/tenancy/me/context")
    if r.status_code != 200:
        return None
    ctx = r.json()
    # Platform-admin w/ tenant_scope_all still needs tenant header for reports
    # (ctx.assert_tenant_bound). Pick first tenant/location if provided.
    headers = {}
    tid = ctx.get("tenant_id")
    if tid:
        headers["X-Tenant-Id"] = tid
    locs = ctx.get("locations") or []
    if locs:
        headers["X-Location-Id"] = locs[0].get("id") or locs[0].get("location_id")
    if headers:
        admin_session.headers.update(headers)
    return ctx


# ---------------------------------------------------------------------------
# Catalog + metadata
# ---------------------------------------------------------------------------

class TestCatalog:
    def test_catalog_returns_grouped_reports(self, admin_session, tenant_scope):
        r = admin_session.get(f"{BASE_URL}/api/reports/catalog")
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert "categories" in body and "total" in body
        assert body["total"] >= 1
        all_reports = [rep["name"] for cat in body["categories"] for rep in cat["reports"]]
        # Admin holds broad perms, expect many of the builtins
        assert "appointments_list" in all_reports
        assert "audit_activity" in all_reports

    def test_report_meta_contract(self, admin_session, tenant_scope):
        r = admin_session.get(f"{BASE_URL}/api/reports/appointments_list")
        assert r.status_code == 200, r.text[:300]
        meta = r.json()
        for key in ("name", "title", "category", "columns", "filters",
                    "sort_options", "contains_phi", "export_formats",
                    "default_columns", "default_sort"):
            assert key in meta, f"missing {key}"
        assert meta["name"] == "appointments_list"
        assert {"csv", "excel", "pdf"}.issubset(set(meta["export_formats"]))
        assert isinstance(meta["columns"], list) and meta["columns"]

    def test_unknown_report_returns_404(self, admin_session, tenant_scope):
        r = admin_session.get(f"{BASE_URL}/api/reports/__bogus__")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class TestRun:
    @pytest.mark.parametrize("name", [
        "appointments_list",
        "claims_list",
        "audit_activity",
        "provider_productivity",
        "invoices_list",
        "denials_log",
    ])
    def test_run_happy_path(self, admin_session, tenant_scope, name):
        r = admin_session.post(
            f"{BASE_URL}/api/reports/{name}/run",
            json={"page": 1, "page_size": 5},
        )
        assert r.status_code == 200, f"{name}: {r.status_code} {r.text[:300]}"
        body = r.json()
        assert body["report"] == name
        for key in ("rows", "total", "columns", "page", "page_size",
                    "contains_phi", "sort", "sort_dir"):
            assert key in body
        assert isinstance(body["rows"], list)
        assert isinstance(body["columns"], list) and body["columns"]

    def test_run_rejects_extra_fields(self, admin_session, tenant_scope):
        r = admin_session.post(
            f"{BASE_URL}/api/reports/appointments_list/run",
            json={"page": 1, "page_size": 5, "unexpected": True},
        )
        assert r.status_code in (400, 422)


# ---------------------------------------------------------------------------
# Export — non-PHI (denials_log)
# ---------------------------------------------------------------------------

def _poll_export(session, export_id, timeout=30):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = session.get(f"{BASE_URL}/api/exports/{export_id}")
        assert r.status_code == 200, r.text[:200]
        last = r.json()
        if last.get("status") in ("ready", "failed"):
            return last
        time.sleep(1.0)
    return last


class TestExport:
    def test_non_phi_export_denials_log(self, admin_session, tenant_scope):
        r = admin_session.post(
            f"{BASE_URL}/api/reports/denials_log/export",
            json={"format": "csv", "filters": {}},
        )
        assert r.status_code in (200, 202), r.text[:300]
        body = r.json()
        assert "export_id" in body
        assert body["password_protected"] is False
        status = _poll_export(admin_session, body["export_id"], timeout=40)
        assert status and status.get("status") == "ready", f"status={status}"
        # password should be absent for non-PHI
        assert not status.get("password")
        # download token
        token = status.get("download_token") or status.get("token")
        assert token, f"missing download token: {status}"
        dl = admin_session.get(
            f"{BASE_URL}/api/exports/{body['export_id']}/download",
            params={"token": token},
            allow_redirects=True,
            stream=True,
        )
        assert dl.status_code == 200, dl.text[:300]
        ctype = dl.headers.get("content-type", "")
        cdisp = dl.headers.get("content-disposition", "")
        assert "text/csv" in ctype or "application/csv" in ctype, ctype
        assert "attachment" in cdisp.lower()

    def test_phi_export_blocked_for_admin(self, admin_session, tenant_scope):
        """PHI report -> admin/super_admin lacks reporting.export_phi -> 403."""
        # Find any PHI report
        meta = admin_session.get(f"{BASE_URL}/api/reports/patient_roster").json()
        if not meta.get("contains_phi"):
            pytest.skip("patient_roster not flagged PHI in this build")
        r = admin_session.post(
            f"{BASE_URL}/api/reports/patient_roster/export",
            json={"format": "csv", "filters": {}},
        )
        assert r.status_code == 403, f"expected 403 got {r.status_code} body={r.text[:300]}"
        detail = (r.json().get("detail") or "").lower()
        assert "export_phi" in detail or "phi" in detail, detail


# ---------------------------------------------------------------------------
# Saved Views CRUD
# ---------------------------------------------------------------------------

class TestSavedViews:
    def test_crud_flow(self, admin_session, tenant_scope):
        # Create
        name = f"TEST_view_{uuid.uuid4().hex[:8]}"
        payload = {
            "name": name,
            "filters": {"status": "scheduled"},
            "columns": ["status", "patient_name"],
            "sort": None,
            "sort_dir": "desc",
            "is_shared": False,
            "is_default": False,
        }
        r = admin_session.post(
            f"{BASE_URL}/api/reports/appointments_list/views", json=payload)
        assert r.status_code == 201, r.text[:300]
        view = r.json()
        vid = view.get("id")
        assert vid

        # List
        r = admin_session.get(f"{BASE_URL}/api/reports/appointments_list/views")
        assert r.status_code == 200
        ids = [v.get("id") for v in r.json().get("views", [])]
        assert vid in ids

        # Patch
        r = admin_session.patch(
            f"{BASE_URL}/api/reports/views/{vid}",
            json={"is_default": True, "name": name + "_upd"},
        )
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("name", "").endswith("_upd")

        # Delete
        r = admin_session.delete(f"{BASE_URL}/api/reports/views/{vid}")
        assert r.status_code == 204

        # List again — gone
        r = admin_session.get(f"{BASE_URL}/api/reports/appointments_list/views")
        assert vid not in [v.get("id") for v in r.json().get("views", [])]


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class TestAudit:
    def test_report_generated_audit_row(self, admin_session, tenant_scope):
        # Trigger a run to create an audit row
        admin_session.post(
            f"{BASE_URL}/api/reports/audit_activity/run",
            json={"page": 1, "page_size": 2},
        )
        time.sleep(0.5)
        r = admin_session.get(
            f"{BASE_URL}/api/audit-logs",
            params={"action": "report.generated", "limit": 5},
        )
        assert r.status_code == 200, r.text[:300]
        rows = r.json()
        if isinstance(rows, dict):
            rows = rows.get("items") or rows.get("logs") or []
        assert any(
            (row.get("action") == "report.generated") for row in rows
        ), f"no report.generated audit row (got {len(rows)})"
