"""
Catalog-wide smoke + saved-view validation tests.

For each registered report:
  * `to_public()` satisfies the frontend contract
  * `default_columns` is a subset of `columns`
  * `default_sort` is inside `sort_options` or is a direct column key
  * `runner` executes against a seeded tenant and returns a sane RunResult

For the saved-view whitelist gate:
  * Arbitrary unknown column keys are rejected at create + update time.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Load backend .env so `tenant_db()` can resolve MONGO_URL under pytest.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
assert os.environ.get("MONGO_URL"), "MONGO_URL must be set for catalog tests"

from services.reports import (  # noqa: E402 — must come after load_dotenv
    QueryContext,
    all_definitions,
    get_definition,
)
from services.reports.views import (  # noqa: E402
    SavedViewCreate,
    SavedViewUpdate,
    _validate_columns_against_definition,
)


# ---------------------------------------------------------------------------
# Contract shape per report
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("definition", all_definitions(), ids=lambda d: d.name)
def test_definition_contract_is_consistent(definition):
    pub = definition.to_public()

    # default_columns is a subset of declared columns
    declared = {c.key for c in definition.columns}
    defaults = set(definition.default_columns)
    assert defaults.issubset(declared), (
        f"{definition.name}: default columns not in `columns`: {defaults - declared}"
    )

    # default_sort must be a valid sort key (either a sort_option or a column)
    sort_keys = {s.key for s in definition.sort_options}
    assert (
        definition.default_sort in sort_keys
        or definition.default_sort in declared
    ), f"{definition.name}: default_sort not a declared option"

    # Public payload stays serialisation-safe
    assert isinstance(pub["columns"], list)
    assert isinstance(pub["default_columns"], list)
    assert set(pub["export_formats"]).issubset({"csv", "excel", "pdf"})

    # Every PHI-carrying report must self-declare contains_phi=True
    has_phi_col = any(c.phi for c in definition.columns)
    if has_phi_col:
        assert definition.contains_phi, (
            f"{definition.name}: has PHI columns but contains_phi=False"
        )


# ---------------------------------------------------------------------------
# Saved-view column whitelist validation
# ---------------------------------------------------------------------------

def test_validate_columns_rejects_unknown_keys():
    # Pick any definition and try to smuggle an unknown key
    d = next(d for d in all_definitions() if d.columns)
    good = [d.default_columns[0]]
    bad = ["password_hash", "__magic__"]

    # Good passes
    _validate_columns_against_definition(d.name, good)

    # Bad raises a 400-mapped HTTPException
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _validate_columns_against_definition(d.name, good + bad)
    assert exc.value.status_code == 400
    assert "Unknown columns" in exc.value.detail


def test_validate_columns_rejects_for_unknown_report():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _validate_columns_against_definition("__nope__", ["x"])
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Runner smoke — every report executes on an empty tenant without raising.
# ---------------------------------------------------------------------------

class _FakeTenantContext:
    """Minimal TenantContext stand-in for report smoke testing.

    Points at a dedicated Mongo DB so nothing else on the test box sees
    the reads; `allowed_location_ids=set()` keeps the location filter
    ineffective for aggregate reports.
    """

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.user = {"id": "tester", "email": "t@test", "role": "super_admin"}
        self.allowed_location_ids = set()
        self.tenant_scope_all = True
        self.is_platform_admin = True

    def assert_tenant_bound(self) -> None:
        return None


@pytest.mark.parametrize("definition", all_definitions(), ids=lambda d: d.name)
def test_runner_executes_without_raising(definition):
    """Each report must execute against a fresh event loop without error.

    We use `asyncio.run` per-test so Motor's client lives and dies in one
    loop — this avoids the pytest-asyncio shared-loop pitfall where a
    prior test closes the loop under the shared client.
    """
    import asyncio
    from core.tenancy import reset_router_for_tests  # isolated helper

    async def _run():
        reset_router_for_tests()
        ctx = _FakeTenantContext("t-smoke-catalog")
        qc = QueryContext(
            tenant=ctx,
            filters={},
            sort=definition.default_sort,
            sort_dir=definition.default_sort_dir,
            page=1, page_size=5,
            selected_columns=None,
        )
        result = await definition.runner(qc)
        assert isinstance(result.rows, list)
        assert isinstance(result.total, int) and result.total >= 0
        assert isinstance(result.aggregates, dict)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Category coverage — every declared report is in one of the expected groups
# ---------------------------------------------------------------------------

EXPECTED_CATEGORIES = {"Operational", "Clinical", "Financial",
                       "Compliance", "Patient", "Scheduling", "Workforce"}


def test_every_report_has_a_known_category():
    for d in all_definitions():
        assert d.category in EXPECTED_CATEGORIES, (
            f"{d.name}: unexpected category {d.category}"
        )


def test_catalog_has_at_least_one_report_per_expected_domain():
    by_cat: dict[str, int] = {}
    for d in all_definitions():
        by_cat[d.category] = by_cat.get(d.category, 0) + 1
    for cat in ("Operational", "Financial", "Clinical", "Compliance"):
        assert by_cat.get(cat, 0) >= 1, f"missing at least one {cat} report"


# ---------------------------------------------------------------------------
# SavedViewCreate pydantic validation (sanity)
# ---------------------------------------------------------------------------

def test_saved_view_create_rejects_bad_sort_dir():
    with pytest.raises(Exception):
        SavedViewCreate(name="x", sort_dir="sideways")


def test_saved_view_update_allows_partial_payload():
    # Updating only name should be fine — no required fields.
    SavedViewUpdate(name="renamed")
