"""Reports service — framework + built-in report registry.

See `definitions.py` for the framework and `builtin.py` for the canonical
reports. Every registered report flows through the router in `router.py`
which handles permission checks, tenant/location scoping, caching, and
audit emission.
"""
from __future__ import annotations

# Force-registration side effect: import builtin.py + builtin_extra.py so
# every @register() call runs at module import time.
from services.reports import builtin as _builtin  # noqa: F401
from services.reports import builtin_extra as _builtin_extra  # noqa: F401

from services.reports.definitions import (
    Column,
    Filter,
    QueryContext,
    ReportDefinition,
    RunResult,
    SortOption,
    all_definitions,
    get_definition,
    register,
    resolve_columns,
    resolve_sort,
)


__all__ = [
    "Column", "Filter", "QueryContext", "ReportDefinition", "RunResult",
    "SortOption", "all_definitions", "get_definition", "register",
    "resolve_columns", "resolve_sort",
]
