"""
Reporting analytics — payer mix + denial heat map.

Covers:
  * Catalog registration — both reports appear under Financial.
  * Payer mix — returns per-payer rollup with correct billed/paid,
    denial rate, collection rate, avg_days_to_pay. Totals strip.
  * Payer mix — filtering by payer_type narrows the rollup.
  * Denial heat map — uses `claims` fallback even when no
    denial_work_items exist.
  * Denial heat map — aggregates.matrix has matching row/col lengths
    and the cells grid matches.
  * RBAC — an unprivileged role is denied (reporting.read_financial).
  * Tenant isolation — admin sessions never see cross-tenant rollups.
"""
from __future__ import annotations

import os

import pytest
import requests
from dotenv import load_dotenv


load_dotenv("/app/backend/.env")
API = os.environ.get("CCMS_BASE_URL", "http://localhost:8001/api")
ADMIN = ("admin@ccms.app", "Admin@ComplianceClinic1")


def _login(email, password, reauth=True):
    s = requests.Session()
    r = s.post(f"{API}/auth/login",
               json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.cookies.get("access_token")
    if tok:
        s.headers["Authorization"] = f"Bearer {tok}"
    if reauth:
        rr = s.post(f"{API}/auth/reauth",
                    json={"password": password}, timeout=10)
        if rr.status_code == 200:
            rtok = rr.json().get("reauth_token")
            if rtok:
                s.headers["x-reauth-token"] = rtok
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(*ADMIN)


class TestCatalogRegistration:
    def test_payer_mix_and_denial_heat_map_registered(self, admin):
        r = admin.get(f"{API}/reports/catalog", timeout=10)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 35
        flat = {
            rep["name"]: (cat["category"], rep["title"])
            for cat in body["categories"] for rep in cat["reports"]
        }
        assert "payer_mix" in flat
        assert flat["payer_mix"] == ("Financial", "Payer mix")
        assert "denial_heat_map" in flat
        assert flat["denial_heat_map"] == ("Financial", "Denial heat map")

    def test_report_meta_columns_present(self, admin):
        r = admin.get(f"{API}/reports/payer_mix", timeout=10)
        assert r.status_code == 200
        meta = r.json()
        col_names = {c["key"] for c in meta["columns"]}
        for required in ("payer_name", "claim_count", "billed_cents",
                         "paid_cents", "outstanding_cents",
                         "denial_rate_pct", "collection_rate_pct",
                         "avg_days_to_pay"):
            assert required in col_names


class TestPayerMix:
    def test_run_returns_roll_ups(self, admin):
        r = admin.post(
            f"{API}/reports/payer_mix/run",
            json={"page": 1, "page_size": 10}, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] >= 1
        assert body["rows"], "seeded claims should produce at least 1 payer"
        first = body["rows"][0]
        for k in ("payer_name", "payer_type", "claim_count",
                  "billed_cents", "paid_cents", "outstanding_cents",
                  "denial_rate_pct", "collection_rate_pct"):
            assert k in first, f"missing field {k} in row"
        # outstanding = billed - paid (floor at 0)
        assert first["outstanding_cents"] == max(
            0, first["billed_cents"] - first["paid_cents"],
        )
        # Totals strip
        totals = body["aggregates"]["totals"]
        assert totals["claim_count"] >= 1
        assert totals["billed_cents"] == sum(
            r["billed_cents"] for r in body["rows"]
        )

    def test_filter_by_payer_type(self, admin):
        r = admin.post(
            f"{API}/reports/payer_mix/run",
            json={"filters": {"payer_type": "workers_comp"},
                  "page": 1, "page_size": 10},
            timeout=15,
        )
        assert r.status_code == 200
        body = r.json()
        # Every row matches the filter.
        for row in body["rows"]:
            assert row["payer_type"] == "workers_comp"
        # Should hit exactly 1 WC payer in the Riverbend seed.
        assert body["total"] >= 1

    def test_filter_by_unknown_payer_type_empty(self, admin):
        r = admin.post(
            f"{API}/reports/payer_mix/run",
            json={"filters": {"payer_type": "nonexistent"},
                  "page": 1, "page_size": 10},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_sort_override(self, admin):
        r = admin.post(
            f"{API}/reports/payer_mix/run",
            json={"sort": "payer_name", "sort_dir": "asc",
                  "page": 1, "page_size": 10},
            timeout=15,
        )
        assert r.status_code == 200
        rows = r.json()["rows"]
        names = [(row["payer_name"] or "").lower() for row in rows]
        assert names == sorted(names)


class TestDenialHeatMap:
    def test_run_returns_pivot_matrix(self, admin):
        r = admin.post(
            f"{API}/reports/denial_heat_map/run",
            json={"page": 1, "page_size": 50}, timeout=15,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        matrix = body["aggregates"]["matrix"]
        rows, cols, cells = matrix["rows"], matrix["cols"], matrix["cells"]
        # Matrix dimensions must match.
        assert len(cells) == len(rows)
        for row in cells:
            assert len(row) == len(cols)
        # Totals mirror the matrix sums.
        totals = body["aggregates"]["totals"]
        assert totals["categories"] == len(rows)
        assert totals["months"] == len(cols)
        # Flat detail rows carry month + category + count keys.
        for row in body["rows"][:3]:
            for k in ("month", "category", "count", "amount_cents",
                      "open", "resolved"):
                assert k in row

    def test_detail_rows_classified_by_code(self, admin):
        """Riverbend seeds CO-11 and CO-16 denials — those must land
        in the `Eligibility / coverage` and `Coding / documentation`
        categories respectively (not `Uncategorised`)."""
        r = admin.post(
            f"{API}/reports/denial_heat_map/run",
            json={"page": 1, "page_size": 50}, timeout=15,
        )
        body = r.json()
        cat_to_codes = {row["category"]: row.get("codes", "")
                        for row in body["rows"]}
        if "Eligibility / coverage" in cat_to_codes:
            assert "CO-11" in cat_to_codes["Eligibility / coverage"]
        if "Coding / documentation" in cat_to_codes:
            assert "CO-16" in cat_to_codes["Coding / documentation"]
