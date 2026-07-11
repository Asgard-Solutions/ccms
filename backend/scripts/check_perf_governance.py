"""Continuous governance check for the Clinical performance threshold
chain. Read-only. Never edits thresholds, never promotes drafts, never
repairs documents.

    python -m scripts.check_perf_governance \
      --thresholds-file /app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md \
      --strict \
      --json-output /app/memory/performance/governance-check.json

Modes:

- default: permissive. Fails only on true policy violations
  (malformed marker, duplicate ids, invalid ordering, mixed units,
  missing approval fields, expired approvals, broken downstream
  references).
- ``--allow-pending-drafts``: pending drafts don't fail, everything
  else still fails. Regular CI PR mode.
- ``--strict``: additionally fails on pending drafts AND on duplicated
  threshold numbers appearing inside downstream policy documents
  (those documents must cite the thresholds file, not embed the
  values). Release-pipeline mode.
- ``--warn-before-expiry-days N``: approvals within N days of the
  renewal window emit an "approaching_expiry" status. Never fails on
  its own.

Row statuses:

- ``valid``               — approved, complete, within renewal window.
- ``pending``             — draft, well-formed, not stale.
- ``approaching_expiry``  — approved, within warn window of renewal.
- ``expired``             — approved but past renewal window OR draft
                            older than 180 days.
- ``not_applicable``      — template combination row with no data yet.
- ``invalid``             — structural violation.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.run_clinical_perf import (  # noqa: E402
    METRICS,
    STALE_WINDOW_DAYS,
    THRESHOLDS_PATH as DEFAULT_THRESHOLDS_PATH,
    HarnessError,
    InvalidThresholdOrderingError,
    is_stale_draft,
    validate_promotion_ordering,
)
from scripts.promote_perf_threshold import (  # noqa: E402
    DOWNSTREAM_DOCS,
    MixedUnitsError,
    ReviewerFieldError,
    UnresolvedPlaceholderError,
    THRESHOLDS_FILE_TOKEN,
    parse_context_tuple,
    parse_number,
    parse_reviewer_fields,
    parse_thresholds_table,
)

DEFAULT_WARN_DAYS = 30
DEFAULT_MEMORY_DIR = Path("/app/memory")

STATUS_VALID = "valid"
STATUS_PENDING = "pending"
STATUS_APPROACHING_EXPIRY = "approaching_expiry"
STATUS_EXPIRED = "expired"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_INVALID = "invalid"


@dataclass
class BlockReport:
    run_id: str | None
    kind: str  # "draft" | "approved" | "template"
    status: str
    timestamp: str | None
    problems: list[str] = field(default_factory=list)


@dataclass
class GovernanceReport:
    thresholds_file: str
    blocks: list[BlockReport] = field(default_factory=list)
    downstream_broken: list[str] = field(default_factory=list)
    downstream_duplicated_numbers: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.blocks:
            out[b.status] = out.get(b.status, 0) + 1
        return out


# --------------------------------------------------------------------
# Marker + block extraction (delegates to the harness where possible)
# --------------------------------------------------------------------
_MARKER_RE = re.compile(
    r"<!--\s*perf-(draft|approved):run-id=([^\s]+)\s+timestamp=(\S+)\s*-->"
)


def _extract_blocks(text: str) -> list[tuple[int, int, str, str, str]]:
    """Return `(start, end, kind, run_id, timestamp)` per marker block.

    ``end`` is the first line NOT in the block. Malformed markers are
    surfaced separately by ``_detect_malformed_markers``.
    """
    lines = text.splitlines()
    starts: list[tuple[int, str, str, str]] = []  # (line_idx, kind, run_id, ts)
    for i, line in enumerate(lines):
        m = _MARKER_RE.search(line)
        if m:
            starts.append((i, m.group(1), m.group(2), m.group(3)))
    blocks: list[tuple[int, int, str, str, str]] = []
    for idx, (start, kind, run_id, ts) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        blocks.append((start, end, kind, run_id, ts))
    return blocks


def _detect_malformed_markers(text: str) -> list[str]:
    """Return a list of near-marker strings that look like a marker but
    fail the strict regex — helps catch typos like missing timestamp."""
    problems: list[str] = []
    loose = re.compile(r"<!--\s*perf-(?:draft|approved).*-->")
    for m in loose.finditer(text):
        if not _MARKER_RE.match(m.group(0)):
            problems.append(f"malformed marker: {m.group(0)!r}")
    return problems


# --------------------------------------------------------------------
# Per-block validation
# --------------------------------------------------------------------
def _classify_block(
    lines: list[str],
    kind: str,
    run_id: str,
    ts: str,
    *,
    now: datetime,
    warn_before_expiry_days: int,
) -> BlockReport:
    report = BlockReport(run_id=run_id, kind=kind, status=STATUS_INVALID, timestamp=ts)

    # Malformed run-id character check (whitespace already handled by regex).
    if not run_id:
        report.problems.append("empty run_id")
        return report

    # Age
    try:
        stale = is_stale_draft(ts, now=now, window_days=STALE_WINDOW_DAYS)
    except HarnessError as exc:
        report.problems.append(f"timestamp: {exc}")
        return report

    # Draft path
    if kind == "draft":
        if stale:
            report.status = STATUS_EXPIRED
            report.problems.append("draft older than 180 days (stale)")
            return report
        # Well-formed? Try to parse context; if the body still contains
        # REVIEW REQUIRED that's expected for a draft (pending).
        try:
            parse_context_tuple(lines)
        except HarnessError as exc:
            report.problems.append(f"context: {exc}")
            return report
        report.status = STATUS_PENDING
        return report

    # Approved path
    if kind == "approved":
        # Age against renewal window
        try:
            stamp = datetime.fromisoformat(ts)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
        except ValueError as exc:
            report.problems.append(f"unparseable timestamp: {exc}")
            return report
        age_days = (now - stamp).days
        if age_days > STALE_WINDOW_DAYS:
            report.status = STATUS_EXPIRED
            report.problems.append(
                f"approval older than renewal window ({age_days} days)"
            )
        elif age_days > STALE_WINDOW_DAYS - warn_before_expiry_days:
            report.status = STATUS_APPROACHING_EXPIRY
        else:
            report.status = STATUS_VALID

        # Structural validation regardless of age.
        try:
            parse_context_tuple(lines)
        except HarnessError as exc:
            report.problems.append(f"context: {exc}")
            report.status = STATUS_INVALID
            return report
        try:
            parse_thresholds_table(lines)
        except (
            UnresolvedPlaceholderError,
            InvalidThresholdOrderingError,
            MixedUnitsError,
        ) as exc:
            report.problems.append(f"thresholds: {exc}")
            report.status = STATUS_INVALID
            return report
        try:
            parse_reviewer_fields(lines)
        except ReviewerFieldError as exc:
            report.problems.append(f"reviewer: {exc}")
            report.status = STATUS_INVALID
            return report
        return report

    report.problems.append(f"unknown marker kind {kind!r}")
    return report


# --------------------------------------------------------------------
# Duplicate id detection
# --------------------------------------------------------------------
def _detect_duplicate_run_ids(blocks: list[tuple[int, int, str, str, str]]) -> list[str]:
    counts: dict[str, int] = {}
    for _s, _e, _k, rid, _ts in blocks:
        counts[rid] = counts.get(rid, 0) + 1
    return [f"duplicate run_id {rid!r} ({n} occurrences)"
            for rid, n in counts.items() if n > 1]


# --------------------------------------------------------------------
# Downstream reference + duplication detection
# --------------------------------------------------------------------
def _check_downstream_references(memory_dir: Path) -> list[str]:
    broken: list[str] = []
    for name in DOWNSTREAM_DOCS:
        path = memory_dir / name
        if not path.exists():
            broken.append(f"{name} — file missing")
            continue
        if THRESHOLDS_FILE_TOKEN not in path.read_text():
            broken.append(f"{name} — missing citation of {THRESHOLDS_FILE_TOKEN}")
    return broken


_NUMBER_MS_RE = re.compile(r"\b(\d{2,6}(?:\.\d+)?)\s*ms\b", re.IGNORECASE)


def _collect_approved_numbers(text: str) -> set[float]:
    """Pull every numeric threshold value out of approved blocks."""
    numbers: set[float] = set()
    for start, end, kind, _rid, _ts in _extract_blocks(text):
        if kind != "approved":
            continue
        block_body = "\n".join(text.splitlines()[start:end])
        if "Proposed thresholds" not in block_body:
            continue
        section = block_body.split("Proposed thresholds", 1)[1]
        for m in _NUMBER_MS_RE.finditer(section):
            try:
                numbers.add(float(m.group(1)))
            except ValueError:
                continue
    return numbers


def _detect_duplicated_downstream_numbers(
    memory_dir: Path, approved_numbers: set[float],
) -> list[str]:
    """Flag downstream docs that embed the same threshold numbers the
    approved rows carry. Downstream docs must cite, not duplicate."""
    if not approved_numbers:
        return []
    problems: list[str] = []
    for name in DOWNSTREAM_DOCS:
        path = memory_dir / name
        if not path.exists():
            continue
        content = path.read_text()
        for m in _NUMBER_MS_RE.finditer(content):
            try:
                value = float(m.group(1))
            except ValueError:
                continue
            if value in approved_numbers:
                problems.append(
                    f"{name} embeds approved threshold {value}ms — "
                    "downstream docs must cite CLINICAL_PERFORMANCE_THRESHOLDS.md, "
                    "not duplicate values"
                )
                break  # one finding per file is enough
    return problems


# --------------------------------------------------------------------
# Template row detection (rows like "not yet approved" placeholders in
# `CLINICAL_PERFORMANCE_THRESHOLDS.md`)
# --------------------------------------------------------------------
def _detect_template_rows(text: str) -> list[BlockReport]:
    reports: list[BlockReport] = []
    for m in re.finditer(
        r"^### (Combination \d+.*)\n(.*?)(?=^###|\Z)",
        text, flags=re.MULTILINE | re.DOTALL,
    ):
        header = m.group(1).strip()
        body = m.group(2)
        if "not yet approved" in body:
            reports.append(BlockReport(
                run_id=header, kind="template",
                status=STATUS_NOT_APPLICABLE, timestamp=None,
                problems=[],
            ))
    return reports


# --------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------
def check(
    *,
    thresholds_path: Path,
    memory_dir: Path,
    warn_before_expiry_days: int = DEFAULT_WARN_DAYS,
    now: datetime | None = None,
) -> GovernanceReport:
    now = now or datetime.now(timezone.utc)
    report = GovernanceReport(thresholds_file=str(thresholds_path))
    if not thresholds_path.exists():
        raise HarnessError(f"thresholds file not found: {thresholds_path}")
    text = thresholds_path.read_text()

    # Structural marker issues (malformed) surface as invalid blocks
    # so the report has one entry per problem.
    for problem in _detect_malformed_markers(text):
        report.blocks.append(BlockReport(
            run_id=None, kind="invalid", status=STATUS_INVALID,
            timestamp=None, problems=[problem],
        ))

    all_blocks = _extract_blocks(text)
    for msg in _detect_duplicate_run_ids(all_blocks):
        report.blocks.append(BlockReport(
            run_id=None, kind="invalid", status=STATUS_INVALID,
            timestamp=None, problems=[msg],
        ))

    seen_ids: set[str] = set()
    for start, end, kind, run_id, ts in all_blocks:
        if run_id in seen_ids:
            continue  # duplicates already flagged
        seen_ids.add(run_id)
        block_lines = text.splitlines()[start:end]
        report.blocks.append(_classify_block(
            block_lines, kind, run_id, ts,
            now=now, warn_before_expiry_days=warn_before_expiry_days,
        ))

    report.blocks.extend(_detect_template_rows(text))
    report.downstream_broken = _check_downstream_references(memory_dir)
    report.downstream_duplicated_numbers = _detect_duplicated_downstream_numbers(
        memory_dir, _collect_approved_numbers(text)
    )
    return report


def evaluate_exit_code(
    report: GovernanceReport,
    *,
    strict: bool,
    allow_pending_drafts: bool,
) -> int:
    """Return 0 for pass, 2 for policy violation. Exit rules:

      - Any ``invalid`` or ``expired`` block → fail.
      - Any broken downstream reference → fail.
      - In default / permissive mode, pending drafts are OK.
      - In ``--allow-pending-drafts``, same as default plus explicit
        allow (semantically identical for the pending case).
      - In ``--strict``, pending drafts fail AND duplicated downstream
        numbers fail.
      - ``approaching_expiry`` never fails.
      - ``not_applicable`` never fails.
    """
    for b in report.blocks:
        if b.status in {STATUS_INVALID, STATUS_EXPIRED}:
            return 2
    if report.downstream_broken:
        return 2
    if strict:
        for b in report.blocks:
            if b.status == STATUS_PENDING:
                return 2
        if report.downstream_duplicated_numbers:
            return 2
    return 0


# --------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Clinical perf governance CI check")
    p.add_argument("--thresholds-file", type=Path, default=DEFAULT_THRESHOLDS_PATH)
    p.add_argument("--memory-dir", type=Path, default=DEFAULT_MEMORY_DIR)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--allow-pending-drafts", action="store_true")
    p.add_argument("--warn-before-expiry-days", type=int, default=DEFAULT_WARN_DAYS)
    p.add_argument("--json-output", type=Path, default=None)
    return p.parse_args(argv)


def _print_console_summary(report: GovernanceReport) -> None:
    counts = report.counts()
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    print(f"[perf-governance] blocks: {', '.join(parts) or '(none)'}")
    for b in report.blocks:
        if b.status in {STATUS_INVALID, STATUS_EXPIRED}:
            for problem in b.problems:
                print(
                    f"[perf-governance] {b.status.upper()}: run_id={b.run_id} "
                    f"{problem}"
                )
    for msg in report.downstream_broken:
        print(f"[perf-governance] BROKEN REFERENCE: {msg}")
    for msg in report.downstream_duplicated_numbers:
        print(f"[perf-governance] DUPLICATED NUMBER: {msg}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = check(
            thresholds_path=args.thresholds_file,
            memory_dir=args.memory_dir,
            warn_before_expiry_days=args.warn_before_expiry_days,
        )
    except HarnessError as exc:
        print(f"[perf-governance] FAIL: {exc}", file=sys.stderr)
        return 2
    _print_console_summary(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(asdict(report), indent=2))
        print(f"[perf-governance] json: {args.json_output}")
    return evaluate_exit_code(
        report,
        strict=args.strict,
        allow_pending_drafts=args.allow_pending_drafts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
