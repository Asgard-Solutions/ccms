"""
Report definition framework.

A `ReportDefinition` describes a tenant-scoped report in a single place:
  * name, category, human-facing title + description
  * available columns (each with key, label, type, phi flag)
  * default columns surfaced when a user opens the report with no saved view
  * supported filters (with type + choices for dropdowns)
  * default sort + supported sort keys
  * required permission (e.g. `reporting.read_financial`)
  * whether the report output may contain PHI
  * a `runner` — async fn that receives a QueryContext and returns rows

The runner is expected to honour `QueryContext` (filters, sort, page,
page_size, selected_columns) and return `RunResult`. Framework-level
concerns (permission check, tenant/location scoping, audit, caching)
live in the router.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Literal

from core.tenancy import TenantContext


# ---------------------------------------------------------------------------
# Column & filter spec
# ---------------------------------------------------------------------------

ColumnType = Literal["string", "number", "currency", "integer", "date", "datetime", "boolean", "enum"]
FilterType = Literal["string", "enum", "multi_enum", "date_range", "boolean", "integer"]


@dataclass
class Column:
    key: str
    label: str
    type: ColumnType = "string"
    phi: bool = False
    sortable: bool = True
    width: int | None = None  # optional hint for UI
    align: Literal["left", "right", "center"] | None = None
    hidden_by_default: bool = False


@dataclass
class Filter:
    key: str
    label: str
    type: FilterType
    options: list[dict[str, Any]] | None = None  # for enum / multi_enum
    placeholder: str | None = None


@dataclass
class SortOption:
    key: str
    label: str


@dataclass
class QueryContext:
    tenant: TenantContext
    filters: dict[str, Any]
    sort: str | None
    sort_dir: Literal["asc", "desc"]
    page: int
    page_size: int
    selected_columns: list[str] | None  # None = default


@dataclass
class RunResult:
    rows: list[dict[str, Any]]
    total: int
    aggregates: dict[str, Any] = field(default_factory=dict)


RunnerFn = Callable[[QueryContext], Awaitable[RunResult]]


@dataclass
class ReportDefinition:
    name: str
    title: str
    category: str
    description: str
    required_permission: tuple[str, str]  # (resource, action)
    columns: list[Column]
    default_columns: list[str]
    filters: list[Filter]
    sort_options: list[SortOption]
    default_sort: str
    default_sort_dir: Literal["asc", "desc"] = "desc"
    contains_phi: bool = False
    export_formats: tuple[str, ...] = ("csv", "excel", "pdf")
    cache_ttl_seconds: int = 300
    runner: RunnerFn | None = None

    def column_by_key(self, key: str) -> Column | None:
        for c in self.columns:
            if c.key == key:
                return c
        return None

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "required_permission": {
                "resource": self.required_permission[0],
                "action": self.required_permission[1],
            },
            "columns": [
                {
                    "key": c.key, "label": c.label, "type": c.type, "phi": c.phi,
                    "sortable": c.sortable, "align": c.align,
                    "hidden_by_default": c.hidden_by_default,
                } for c in self.columns
            ],
            "default_columns": list(self.default_columns),
            "filters": [
                {"key": f.key, "label": f.label, "type": f.type,
                 "options": f.options, "placeholder": f.placeholder}
                for f in self.filters
            ],
            "sort_options": [{"key": s.key, "label": s.label} for s in self.sort_options],
            "default_sort": self.default_sort,
            "default_sort_dir": self.default_sort_dir,
            "contains_phi": self.contains_phi,
            "export_formats": list(self.export_formats),
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ReportDefinition] = {}


def register(definition: ReportDefinition) -> ReportDefinition:
    if definition.runner is None:
        raise ValueError(f"report {definition.name!r} has no runner")
    if definition.name in _REGISTRY:
        raise ValueError(f"report {definition.name!r} already registered")
    _REGISTRY[definition.name] = definition
    return definition


def get_definition(name: str) -> ReportDefinition | None:
    return _REGISTRY.get(name)


def all_definitions() -> list[ReportDefinition]:
    return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Helpers — validating columns/sort against a definition
# ---------------------------------------------------------------------------

def resolve_columns(definition: ReportDefinition, requested: Iterable[str] | None) -> list[Column]:
    keys = list(requested) if requested else list(definition.default_columns)
    out: list[Column] = []
    seen: set[str] = set()
    for k in keys:
        col = definition.column_by_key(k)
        if col and k not in seen:
            out.append(col)
            seen.add(k)
    return out or [c for c in definition.columns if c.key in definition.default_columns]


def resolve_sort(definition: ReportDefinition, sort: str | None) -> str:
    if sort:
        allowed = {s.key for s in definition.sort_options}
        if sort in allowed:
            return sort
    return definition.default_sort
