"""Shared, side-effect-free primitives for the Clinical performance
governance chain. Owned by backend tooling only (underscore-prefixed).

STABLE PUBLIC SURFACE (re-exported from the four scripts for
backward-compatible imports):

  - RENEWAL_PERIOD_DAYS
  - DOWNSTREAM_DOCUMENTS  (also aliased as DOWNSTREAM_DOCS)
  - THRESHOLDS_FILE_TOKEN
  - METRICS  (imported from run_clinical_perf; re-exported here for
    convenience so callers can obtain the whole vocabulary from a
    single module.)
  - Exception classes: HarnessError, InvalidThresholdOrderingError,
    MixedUnitsError, UnresolvedPlaceholderError, ReviewerFieldError,
    MalformedRunContextError, DownstreamReferenceError.
  - Functions: parse_existing_markers, is_stale_draft, parse_number,
    validate_promotion_ordering, parse_thresholds_table,
    parse_reviewer_fields, validate_run_context, parse_context_tuple,
    validate_downstream_references.

Guarantees:
  - Deterministic. No I/O side effects beyond ``validate_downstream_references``
    which reads (never writes) filesystem paths.
  - Never emits telemetry.
  - Never writes files.
  - Signatures + return shapes are stable — bumping them is a
    breaking change that requires updating every consumer.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


# --------------------------------------------------------------------
# Constants (stable)
# --------------------------------------------------------------------
RENEWAL_PERIOD_DAYS = 180
THRESHOLDS_FILE_TOKEN = "CLINICAL_PERFORMANCE_THRESHOLDS.md"
DOWNSTREAM_DOCUMENTS: tuple[str, ...] = (
    "CLINICAL_MONITORING_PLAN.md",
    "CLINICAL_STAGED_ROLLOUT_PLAN.md",
    "CLINICAL_ROLLOUT_CHECKLIST.md",
    "CLINICAL_GA_READINESS.md",
    "PHASE3_PERFORMANCE_TEST_PLAN.md",
)
# Backwards-compatible alias — the promotion script exports this name.
DOWNSTREAM_DOCS = DOWNSTREAM_DOCUMENTS


# --------------------------------------------------------------------
# Exception hierarchy (stable)
# --------------------------------------------------------------------
class HarnessError(RuntimeError):
    """Base for every governance-chain failure."""


class InvalidThresholdOrderingError(HarnessError): ...
class MixedUnitsError(HarnessError): ...
class UnresolvedPlaceholderError(HarnessError): ...
class ReviewerFieldError(HarnessError): ...
class MalformedRunContextError(HarnessError): ...
class DownstreamReferenceError(HarnessError): ...


# --------------------------------------------------------------------
# Marker parsing
# --------------------------------------------------------------------
_MARKER_RE = re.compile(
    r"<!--\s*perf-(draft|approved):run-id=([^\s]+)\s+timestamp=(\S+)\s*-->"
)


def parse_existing_markers(text: str) -> list[dict]:
    """Scan `text` for ``perf-draft`` / ``perf-approved`` anchor
    comments. Returns a list of dicts, one per marker."""
    return [
        {"kind": m.group(1), "run_id": m.group(2), "timestamp": m.group(3)}
        for m in _MARKER_RE.finditer(text)
    ]


# --------------------------------------------------------------------
# Freshness / staleness
# --------------------------------------------------------------------
def is_stale_draft(
    timestamp_iso: str,
    now: datetime | None = None,
    window_days: int = RENEWAL_PERIOD_DAYS,
) -> bool:
    """True iff ``timestamp_iso`` is older than ``window_days``."""
    ref = now or datetime.now(timezone.utc)
    try:
        stamp = datetime.fromisoformat(timestamp_iso)
    except ValueError as exc:
        raise MalformedRunContextError(
            f"unparseable timestamp {timestamp_iso!r}"
        ) from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (ref - stamp).days > window_days


# --------------------------------------------------------------------
# Numeric parsing
# --------------------------------------------------------------------
_NUMERIC_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z%]+)?\s*$"
)


def parse_number(cell: str, *, expected_unit: str | None = None) -> tuple[float, str | None]:
    """Parse a threshold cell. Returns (value, unit). Raises
    UnresolvedPlaceholderError for anything non-numeric, MixedUnitsError
    for unit mismatches or unexpected units."""
    s = cell.strip().strip("`|").strip()
    if not s:
        raise UnresolvedPlaceholderError("empty threshold cell")
    if "REVIEW REQUIRED" in s.upper():
        raise UnresolvedPlaceholderError("REVIEW REQUIRED placeholder still present")
    m = _NUMERIC_RE.match(s)
    if not m:
        raise UnresolvedPlaceholderError(f"non-numeric threshold value: {cell!r}")
    unit = (m.group("unit") or "").lower() or None
    if expected_unit and unit and unit != expected_unit:
        raise MixedUnitsError(
            f"unit mismatch: expected {expected_unit!r}, got {unit!r} in {cell!r}"
        )
    if expected_unit is None and unit is not None and unit != "ms":
        raise MixedUnitsError(
            f"unexpected unit {unit!r} in {cell!r}; the harness measures milliseconds"
        )
    return float(m.group("num")), unit


# --------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------
def validate_promotion_ordering(
    release: float, warning: float, rollback: float,
) -> None:
    if not (release < warning < rollback):
        raise InvalidThresholdOrderingError(
            f"threshold ordering violated: release={release} warning={warning} "
            f"rollback={rollback}; required release < warning < rollback"
        )


# --------------------------------------------------------------------
# Block field parsers
# --------------------------------------------------------------------
def parse_context_tuple(block_lines: list[str]) -> dict[str, str]:
    body = "\n".join(block_lines)
    ctx: dict[str, str] = {}
    for field in ("Source run id", "Generated at", "Raw results",
                  "Profile", "Network", "Dataset", "Browser / device"):
        m = re.search(rf"\|\s*{re.escape(field)}\s*\|\s*([^|]+?)\s*\|", body)
        if not m or not m.group(1).strip():
            raise MalformedRunContextError(f"context tuple missing field {field!r}")
        ctx[field] = m.group(1).strip()
    return ctx


def parse_reviewer_fields(block_lines: list[str]) -> dict[str, str]:
    body = "\n".join(block_lines)
    got: dict[str, str] = {}
    for key in ("Approval owner", "Approval date", "Rationale"):
        m = re.search(rf"\*\*{re.escape(key)}:\*\*\s*(.+)$", body, flags=re.MULTILINE)
        if not m:
            raise ReviewerFieldError(f"reviewer field {key!r} missing entirely")
        value = m.group(1).strip()
        if not value or set(value) <= {"_", " "}:
            raise ReviewerFieldError(f"reviewer field {key!r} not filled")
        got[key] = value
    return got


def parse_thresholds_table(
    block_lines: list[str], *, metrics: tuple[str, ...] | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Extract release/warning/rollback per metric. ``metrics`` is the
    expected metric list; if omitted, the caller's METRICS is imported
    lazily to avoid a circular import."""
    if metrics is None:
        from scripts.run_clinical_perf import METRICS as _M
        metrics = _M
    body = "\n".join(block_lines)
    if "Proposed thresholds" not in body:
        raise UnresolvedPlaceholderError("threshold section header missing")
    section = body.split("Proposed thresholds", 1)[1]
    rows = re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        section, flags=re.MULTILINE,
    )
    got: dict[str, tuple[float, float, float]] = {}
    detected_unit: str | None = None
    for name, release, warning, rollback in rows:
        if name not in metrics:
            continue
        r_val, r_unit = parse_number(release, expected_unit=detected_unit)
        detected_unit = detected_unit or r_unit
        w_val, _ = parse_number(warning, expected_unit=detected_unit or r_unit)
        rb_val, _ = parse_number(rollback, expected_unit=detected_unit or r_unit)
        validate_promotion_ordering(release=r_val, warning=w_val, rollback=rb_val)
        got[name] = (r_val, w_val, rb_val)
    missing = [m for m in metrics if m not in got]
    if missing:
        raise UnresolvedPlaceholderError(
            f"threshold rows missing for metrics: {missing}"
        )
    return got


# --------------------------------------------------------------------
# Run-context + downstream-reference validators
# --------------------------------------------------------------------
def validate_run_context(meta: dict, run_id: str, raw_path: Path) -> None:
    required = ("patient_id", "fixture_events", "profile", "network", "generated_at")
    if not run_id or not isinstance(run_id, str):
        raise MalformedRunContextError("run_id is required (non-empty string)")
    if not raw_path or not str(raw_path):
        raise MalformedRunContextError("raw_path is required")
    for key in required:
        if meta.get(key) in (None, "", 0):
            raise MalformedRunContextError(f"meta missing required field {key!r}")


def validate_downstream_references(
    memory_dir: Path,
    *,
    docs: tuple[str, ...] = DOWNSTREAM_DOCUMENTS,
    token: str = THRESHOLDS_FILE_TOKEN,
) -> None:
    broken: list[str] = []
    for name in docs:
        path = memory_dir / name
        if not path.exists():
            broken.append(f"{name} — file missing")
            continue
        if token not in path.read_text():
            broken.append(f"{name} — no reference to {token}")
    if broken:
        raise DownstreamReferenceError(
            "downstream reference validation failed:\n  " + "\n  ".join(broken)
        )
