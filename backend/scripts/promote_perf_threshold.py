"""Promote an approved perf-threshold draft block in
`CLINICAL_PERFORMANCE_THRESHOLDS.md`.

The harness (`scripts/run_clinical_perf.py --write-threshold-draft`)
records measured evidence and appends a draft block with all threshold
tiers rendered as ``REVIEW REQUIRED``. A human reviewer then edits the
draft in place, replacing each ``REVIEW REQUIRED`` cell with a real
numeric threshold and filling the Approval owner / date / rationale
fields. This script promotes that reviewer-signed draft to an approved
row inside the same document. It never edits downstream documents.

    python -m scripts.promote_perf_threshold \
      --run-id 20260215T120000000000 \
      --thresholds-file /app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md \
      --approved-by "Platform Reliability Owner" \
      --approval-date 2026-02-15 \
      --rationale "Meets P95 budget with 30% headroom" \
      --confirm-promotion

Design constraints:

- Documentation-only. No application, database, telemetry, or contract
  access. Safe to run in any non-production environment.
- Refuses without ``--confirm-promotion``.
- Locates exactly one matching ``perf-draft`` block by run id.
- Rejects missing / duplicate / stale / already-approved blocks.
- Rejects any remaining ``REVIEW REQUIRED`` value.
- Parses every metric and validates numeric values, consistent units,
  release < warning < rollback, complete context tuple, approval owner,
  approval date, rationale.
- Preserves the measured evidence table unchanged.
- Changes only: marker, status, reviewer fields, approved threshold
  values.
- Adds a promotion timestamp and an immutable history entry.
- Atomic write through a temp file + os.replace.
- Creates a backup before promotion.
- Emits a concise diff preview (use ``--dry-run`` to skip writing).
- Downstream reference validation: reports broken references — never
  edits the downstream documents.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Reuse the harness's shared constants + error types + validators.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.run_clinical_perf import (  # noqa: E402
    METRICS,
    STALE_WINDOW_DAYS,
    THRESHOLDS_PATH as DEFAULT_THRESHOLDS_PATH,
    HarnessError,
    ApprovedRowProtectionError,
    DuplicateDraftError,
    InvalidThresholdOrderingError,
    MalformedRunContextError,
    _parse_existing_markers,
    is_stale_draft,
    validate_promotion_ordering,
)


class PromotionError(HarnessError):
    """Base for promotion-time failures."""


class DraftNotFoundError(PromotionError): ...
class DuplicateBlockError(PromotionError): ...
class UnresolvedPlaceholderError(PromotionError): ...
class ReviewerFieldError(PromotionError): ...
class MixedUnitsError(PromotionError): ...
class DownstreamReferenceError(PromotionError): ...
class ProductionOperationError(PromotionError): ...
class AtomicWriteError(PromotionError): ...


# --------------------------------------------------------------------
# Downstream documents that MUST continue to cite the thresholds file
# rather than embed numbers. Missing citation → broken reference.
# --------------------------------------------------------------------
DOWNSTREAM_DOCS = (
    "CLINICAL_MONITORING_PLAN.md",
    "CLINICAL_STAGED_ROLLOUT_PLAN.md",
    "CLINICAL_ROLLOUT_CHECKLIST.md",
    "CLINICAL_GA_READINESS.md",
    "PHASE3_PERFORMANCE_TEST_PLAN.md",
)
THRESHOLDS_FILE_TOKEN = "CLINICAL_PERFORMANCE_THRESHOLDS.md"

# Numeric parsing — accept "300", "300.5", "300 ms", "300.5ms" (all interpreted
# as milliseconds). Accept alternative unit suffixes only to detect mixed units.
# Reject anything genuinely non-numeric (e.g. "REVIEW REQUIRED").
_NUMERIC_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z%]+)?\s*$"
)


# --------------------------------------------------------------------
# Draft block parsing
# --------------------------------------------------------------------
@dataclass
class DraftBlock:
    run_id: str
    timestamp: str
    start_line: int      # index (inclusive) of the marker line
    end_line: int        # index (exclusive) — first line NOT in the block
    lines: list[str]     # slice of the file for this block
    kind: str            # "draft" | "approved"


def find_block(text: str, run_id: str) -> DraftBlock:
    """Locate exactly one block whose marker matches ``run_id``.

    Raises:
      DraftNotFoundError — no matching marker.
      DuplicateBlockError — more than one matching marker.
    """
    all_lines = text.splitlines(keepends=False)
    matches: list[tuple[int, str, str]] = []  # (line_index, kind, timestamp)
    marker_re = re.compile(
        r"<!--\s*perf-(draft|approved):run-id=([^\s]+)\s+timestamp=(\S+)\s*-->"
    )
    for i, line in enumerate(all_lines):
        m = marker_re.search(line)
        if m and m.group(2) == run_id:
            matches.append((i, m.group(1), m.group(3)))
    if not matches:
        raise DraftNotFoundError(
            f"no perf-draft / perf-approved block found for run_id={run_id!r}"
        )
    if len(matches) > 1:
        raise DuplicateBlockError(
            f"multiple blocks found for run_id={run_id!r}: "
            f"{[m[0] for m in matches]}"
        )
    start, kind, ts = matches[0]

    # End of block: next `<!-- perf-` marker OR EOF.
    end = len(all_lines)
    for j in range(start + 1, len(all_lines)):
        if "<!-- perf-" in all_lines[j] and "run-id=" in all_lines[j]:
            end = j
            break
    return DraftBlock(
        run_id=run_id, timestamp=ts,
        start_line=start, end_line=end,
        lines=all_lines[start:end], kind=kind,
    )


def parse_number(cell: str, *, expected_unit: str | None = None) -> tuple[float, str | None]:
    """Parse a threshold cell. Returns (value, unit-if-any).

    Rejects placeholders and any non-numeric value.
    """
    s = cell.strip().strip("`|").strip()
    if not s:
        raise UnresolvedPlaceholderError("empty threshold cell")
    if "REVIEW REQUIRED" in s.upper():
        raise UnresolvedPlaceholderError("REVIEW REQUIRED placeholder still present")
    m = _NUMERIC_RE.match(s)
    if not m:
        raise UnresolvedPlaceholderError(
            f"non-numeric threshold value: {cell!r}"
        )
    unit = (m.group("unit") or "").lower() or None
    if expected_unit and unit and unit != expected_unit:
        raise MixedUnitsError(
            f"unit mismatch: expected {expected_unit!r}, got {unit!r} in {cell!r}"
        )
    # If this is the very first parse and unit is not ms/None, still flag it.
    if expected_unit is None and unit is not None and unit != "ms":
        raise MixedUnitsError(
            f"unexpected unit {unit!r} in {cell!r}; the harness measures milliseconds"
        )
    return float(m.group("num")), unit


def parse_thresholds_table(block_lines: list[str]) -> dict[str, tuple[float, float, float]]:
    """Extract the Release/Warning/Rollback triple for every metric.

    Enforces:
      - all METRICS present
      - all values numeric (no REVIEW REQUIRED, no blanks)
      - consistent units across the block
      - ordering release < warning < rollback per metric
    """
    body = "\n".join(block_lines)
    if "Proposed thresholds" not in body:
        raise UnresolvedPlaceholderError("threshold section header missing")
    section = body.split("Proposed thresholds", 1)[1]
    rows = re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
        section,
        flags=re.MULTILINE,
    )
    got: dict[str, tuple[float, float, float]] = {}
    detected_unit: str | None = None
    for name, release, warning, rollback in rows:
        if name not in METRICS:
            continue
        r_val, r_unit = parse_number(release, expected_unit=detected_unit)
        detected_unit = detected_unit or r_unit
        w_val, _ = parse_number(warning, expected_unit=detected_unit or r_unit)
        rb_val, _ = parse_number(rollback, expected_unit=detected_unit or r_unit)
        validate_promotion_ordering(release=r_val, warning=w_val, rollback=rb_val)
        got[name] = (r_val, w_val, rb_val)
    missing = [m for m in METRICS if m not in got]
    if missing:
        raise UnresolvedPlaceholderError(
            f"threshold rows missing for metrics: {missing}"
        )
    return got


def parse_reviewer_fields(block_lines: list[str]) -> dict[str, str]:
    """Extract Approval owner / Approval date / Rationale. Missing or
    blank ("____________________") values are rejected."""
    body = "\n".join(block_lines)
    got: dict[str, str] = {}
    for key in ("Approval owner", "Approval date", "Rationale"):
        m = re.search(
            rf"\*\*{re.escape(key)}:\*\*\s*(.+)$",
            body,
            flags=re.MULTILINE,
        )
        if not m:
            raise ReviewerFieldError(f"reviewer field {key!r} missing entirely")
        value = m.group(1).strip()
        if not value or set(value) <= {"_", " "}:
            raise ReviewerFieldError(f"reviewer field {key!r} not filled")
        got[key] = value
    return got


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


# --------------------------------------------------------------------
# Approved-block rendering (in-place transformation)
# --------------------------------------------------------------------
def render_approved_block(
    *,
    original_lines: list[str],
    thresholds: dict[str, tuple[float, float, float]],
    reviewer: dict[str, str],
    promotion_ts: str,
) -> list[str]:
    """Return the transformed block content.

    Changes only:
      - marker `perf-draft` → `perf-approved`
      - Status: `AWAITING SIGN-OFF` → `APPROVED`
      - reviewer fields (kept as-is; already filled)
      - threshold table cells (already numeric)
      - promotion history stamp appended before the closing blank line
    Everything else — including the measured evidence table — is
    preserved byte-for-byte.
    """
    out: list[str] = []
    for line in original_lines:
        if "<!-- perf-draft:" in line:
            out.append(line.replace("perf-draft:", "perf-approved:"))
        elif "**Status:**" in line and "AWAITING SIGN-OFF" in line:
            out.append(line.replace("`AWAITING SIGN-OFF`", "`APPROVED`"))
        else:
            out.append(line)
    # Append promotion stamp.
    out.append("")
    out.append(f"**Promoted at:** {promotion_ts}")
    out.append(f"**Promoted by:** {reviewer['Approval owner']}")
    out.append("")
    out.append(
        "> Immutable history: this block was promoted from draft to approved. "
        "Any subsequent edit must be a new draft with a new run id."
    )
    out.append("")
    return out


# --------------------------------------------------------------------
# Downstream-reference validation
# --------------------------------------------------------------------
def validate_downstream_references(memory_dir: Path) -> None:
    """Every downstream document MUST still cite the thresholds file by
    name. If a downstream doc is missing OR fails to cite the source of
    truth, that is a broken reference — raise, do not silently edit."""
    broken: list[str] = []
    for name in DOWNSTREAM_DOCS:
        path = memory_dir / name
        if not path.exists():
            broken.append(f"{name} — file missing")
            continue
        content = path.read_text()
        if THRESHOLDS_FILE_TOKEN not in content:
            broken.append(f"{name} — no reference to {THRESHOLDS_FILE_TOKEN}")
    if broken:
        raise DownstreamReferenceError(
            "downstream reference validation failed:\n  " + "\n  ".join(broken)
        )


# --------------------------------------------------------------------
# Atomic write + backup
# --------------------------------------------------------------------
def atomic_write(path: Path, new_content: str) -> Path:
    """Write ``new_content`` to ``path`` atomically. Returns the backup
    path so it can be reported."""
    try:
        backup = path.with_suffix(
            path.suffix + f".backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        shutil.copy2(path, backup)
        directory = path.parent
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(directory)
        )
        with os.fdopen(fd, "w") as fh:
            fh.write(new_content)
        os.replace(tmp_name, path)
        return backup
    except OSError as exc:
        raise AtomicWriteError(f"atomic write failed: {exc}") from exc


# --------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------
@dataclass
class PromotionResult:
    run_id: str
    backup_path: Path | None
    diff: str
    thresholds: dict[str, tuple[float, float, float]]
    reviewer: dict[str, str]
    dry_run: bool


def promote(
    *,
    thresholds_path: Path,
    run_id: str,
    approved_by: str,
    approval_date: str,
    rationale: str | None,
    memory_dir: Path,
    dry_run: bool,
    now: datetime | None = None,
) -> PromotionResult:
    """Perform the promotion in-memory + write atomically unless
    ``dry_run``. Returns a PromotionResult with the diff so the caller
    can display it."""
    if not thresholds_path.exists():
        raise MalformedRunContextError(f"thresholds file not found: {thresholds_path}")
    original_text = thresholds_path.read_text()

    block = find_block(original_text, run_id)
    if block.kind == "approved":
        raise ApprovedRowProtectionError(
            f"run_id {run_id!r} is already promoted (perf-approved); "
            "refusing to re-promote"
        )
    if is_stale_draft(block.timestamp, now=now, window_days=STALE_WINDOW_DAYS):
        raise UnresolvedPlaceholderError(
            f"draft for run_id {run_id!r} is stale (older than {STALE_WINDOW_DAYS} days); "
            "re-measure before promoting"
        )

    # Parse and validate everything BEFORE touching the file.
    parse_context_tuple(block.lines)
    thresholds = parse_thresholds_table(block.lines)
    reviewer = parse_reviewer_fields(block.lines)
    # CLI-supplied approver / date / rationale must match (or fill) the
    # reviewer fields.
    if reviewer["Approval owner"] not in {"____________________", approved_by}:
        # Reviewer already filled the field — trust the file over the CLI.
        pass
    else:
        reviewer["Approval owner"] = approved_by
    if reviewer["Approval date"] in {"____________________", ""}:
        reviewer["Approval date"] = approval_date
    if rationale and reviewer["Rationale"] in {"____________________", ""}:
        reviewer["Rationale"] = rationale

    # Downstream reference validation — done BEFORE the write.
    validate_downstream_references(memory_dir)

    # Compose the new block + splice it in.
    now = now or datetime.now(timezone.utc)
    new_block_lines = render_approved_block(
        original_lines=block.lines,
        thresholds=thresholds,
        reviewer=reviewer,
        promotion_ts=now.isoformat(),
    )
    all_lines = original_text.splitlines(keepends=False)
    new_lines = (
        all_lines[: block.start_line]
        + new_block_lines
        + all_lines[block.end_line :]
    )
    new_text = "\n".join(new_lines)
    if not new_text.endswith("\n"):
        new_text += "\n"

    diff = "\n".join(
        difflib.unified_diff(
            original_text.splitlines(),
            new_text.splitlines(),
            fromfile=str(thresholds_path) + " (before)",
            tofile=str(thresholds_path) + " (after)",
            lineterm="",
        )
    )

    if dry_run:
        return PromotionResult(
            run_id=run_id, backup_path=None, diff=diff,
            thresholds=thresholds, reviewer=reviewer, dry_run=True,
        )

    backup = atomic_write(thresholds_path, new_text)
    return PromotionResult(
        run_id=run_id, backup_path=backup, diff=diff,
        thresholds=thresholds, reviewer=reviewer, dry_run=False,
    )


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Promote a signed perf-threshold draft to APPROVED status "
            "inside CLINICAL_PERFORMANCE_THRESHOLDS.md. Documentation-only; "
            "never touches downstream docs or application code."
        ),
    )
    p.add_argument("--run-id", required=True)
    p.add_argument(
        "--thresholds-file", type=Path, default=DEFAULT_THRESHOLDS_PATH,
    )
    p.add_argument("--approved-by", required=True)
    p.add_argument("--approval-date", required=True,
                   help="ISO date, e.g. 2026-02-15.")
    p.add_argument("--rationale", default=None)
    p.add_argument("--confirm-promotion", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--memory-dir", type=Path, default=Path("/app/memory"),
        help="Directory where downstream reference docs live.",
    )
    return p.parse_args(argv)


def _refuse_production() -> None:
    env = (os.environ.get("APP_ENV") or "").strip().lower()
    if env in {"production", "prod"}:
        raise ProductionOperationError(
            "REFUSING TO RUN: APP_ENV=production. Promotion is documentation-"
            "only, but this tool must not run in the production environment "
            "to avoid interleaving governance changes with a live release."
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _refuse_production()
        if not (args.confirm_promotion or args.dry_run):
            raise PromotionError(
                "REFUSING TO PROMOTE: pass --confirm-promotion (or --dry-run "
                "to preview without writing)."
            )
        result = promote(
            thresholds_path=args.thresholds_file,
            run_id=args.run_id,
            approved_by=args.approved_by,
            approval_date=args.approval_date,
            rationale=args.rationale,
            memory_dir=args.memory_dir,
            dry_run=args.dry_run,
        )
        preview = "\n".join(result.diff.splitlines()[:40])
        print("[perf-promote] diff preview (first 40 lines):")
        print(preview)
        print("[perf-promote] ...")
        if result.dry_run:
            print("[perf-promote] DRY RUN — no file was modified.")
        else:
            print(f"[perf-promote] promotion applied — backup: {result.backup_path}")
        print(f"[perf-promote] approved thresholds for run_id={result.run_id}")
        for name, (r, w, rb) in result.thresholds.items():
            print(f"[perf-promote]   {name}  release={r}  warning={w}  rollback={rb}")
        return 0
    except HarnessError as exc:
        print(f"[perf-promote] FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
