"""Tests for `scripts/check_perf_governance` — the read-only CI guard.

Covers: valid approved rows, valid pending drafts, duplicate ids,
malformed markers, invalid ordering, mixed units, missing approval
fields, stale drafts, expired approvals, warning-window behavior,
broken downstream references, duplicated thresholds downstream, JSON
output, strict vs permissive mode, and read-only behavior.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import check_perf_governance as gov
from scripts import promote_perf_threshold as pp
from scripts import run_clinical_perf as rcp


# --------------------------------------------------------------------
# Helpers — reuse the draft/approved block builders exactly.
# --------------------------------------------------------------------
def _make_summary():
    m = {"p50": 100.0, "p75": 150.0, "p95": 300.0, "min": 80.0, "max": 400.0, "count": 20}
    return {
        "run_count": 20, "successful": 20, "errors": 0, "error_rate": 0.0,
        "metrics": {name: dict(m) for name in rcp.METRICS},
    }


def _meta(ts="2026-02-15T00:00:00+00:00"):
    return {
        "generated_at": ts, "patient_id": "fixture-large-chart-patient-0001",
        "fixture_events": 500, "profile": "desktop", "network": "normal",
        "build_hash": None, "warmup": 3,
        "result_label": "Measured — threshold approval required",
    }


def _draft_block(run_id="run-1", ts="2026-02-15T00:00:00+00:00"):
    return rcp.build_draft_block(
        run_id=run_id, meta=_meta(ts), summary=_make_summary(),
        raw_path=Path("/tmp/raw.json"),
    )


def _approved_block(
    run_id="run-1", ts="2026-02-15T00:00:00+00:00",
    release=500.0, warning=800.0, rollback=1500.0, unit="ms",
    owner="Reviewer", date="2026-02-15", rationale="ok",
):
    block = _draft_block(run_id, ts)
    block = block.replace("perf-draft:", "perf-approved:")
    block = block.replace("`AWAITING SIGN-OFF`", "`APPROVED`")
    block = block.replace(
        "REVIEW REQUIRED | REVIEW REQUIRED | REVIEW REQUIRED",
        f"{release}{unit} | {warning}{unit} | {rollback}{unit}",
    )
    block = block.replace(
        "**Approval owner:** ____________________",
        f"**Approval owner:** {owner}",
    ).replace(
        "**Approval date:** ____________________",
        f"**Approval date:** {date}",
    ).replace(
        "**Rationale:** ____________________",
        f"**Rationale:** {rationale}",
    )
    return block


def _seed_env(tmp_path: Path, blocks_text: str, *, cite: bool = True) -> tuple[Path, Path]:
    memory = tmp_path / "memory"
    memory.mkdir()
    thresholds = memory / "CLINICAL_PERFORMANCE_THRESHOLDS.md"
    thresholds.write_text(
        "# Clinical Performance Thresholds — Approval Record\n\n"
        + blocks_text
    )
    for name in pp.DOWNSTREAM_DOCS:
        body = f"# {name}\n\n"
        if cite:
            body += "See `CLINICAL_PERFORMANCE_THRESHOLDS.md`.\n"
        (memory / name).write_text(body)
    return thresholds, memory


# --------------------------------------------------------------------
# Read-only guarantee
# --------------------------------------------------------------------
class TestReadOnly:
    def test_check_does_not_modify_any_file(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _approved_block("run-1"))
        mtimes = {}
        for name in pp.DOWNSTREAM_DOCS + (thresholds.name,):
            f = memory / name if (memory / name).exists() else thresholds
            mtimes[f] = f.stat().st_mtime
        gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        for f, mt in mtimes.items():
            assert f.stat().st_mtime == mt


# --------------------------------------------------------------------
# Approved / pending / expired / approaching-expiry / not-applicable
# --------------------------------------------------------------------
class TestBlockStatuses:
    def test_valid_approved_row(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _approved_block("run-1"))
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.run_id == "run-1" and b.status == gov.STATUS_VALID for b in report.blocks)
        assert gov.evaluate_exit_code(report, strict=True, allow_pending_drafts=False) == 0

    def test_valid_pending_draft(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _draft_block("draft-1"))
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.run_id == "draft-1" and b.status == gov.STATUS_PENDING for b in report.blocks)
        # Permissive → pass
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 0
        # Strict → fail
        assert gov.evaluate_exit_code(report, strict=True, allow_pending_drafts=False) == 2

    def test_stale_draft_marked_expired(self, tmp_path):
        old_ts = (datetime(2026, 2, 15, tzinfo=timezone.utc)
                  - timedelta(days=200)).isoformat()
        thresholds, memory = _seed_env(tmp_path, _draft_block("stale-1", ts=old_ts))
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.run_id == "stale-1" and b.status == gov.STATUS_EXPIRED for b in report.blocks)
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 2

    def test_expired_approval(self, tmp_path):
        old_ts = (datetime(2026, 2, 15, tzinfo=timezone.utc)
                  - timedelta(days=200)).isoformat()
        thresholds, memory = _seed_env(tmp_path, _approved_block("old-1", ts=old_ts))
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.run_id == "old-1" and b.status == gov.STATUS_EXPIRED for b in report.blocks)
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 2

    def test_approaching_expiry_within_warn_window(self, tmp_path):
        # 170 days old with 30-day warn window → approaching, not expired.
        near_ts = (datetime(2026, 2, 15, tzinfo=timezone.utc)
                   - timedelta(days=170)).isoformat()
        thresholds, memory = _seed_env(tmp_path, _approved_block("aging-1", ts=near_ts))
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            warn_before_expiry_days=30,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        block = next(b for b in report.blocks if b.run_id == "aging-1")
        assert block.status == gov.STATUS_APPROACHING_EXPIRY
        # Approaching expiry never fails.
        assert gov.evaluate_exit_code(report, strict=True, allow_pending_drafts=False) == 0

    def test_not_applicable_template_rows(self, tmp_path):
        template = (
            "### Combination 2 — desktop / throttled / 500-event\n\n"
            "**Status:** not yet approved. Do not extrapolate.\n\n"
        )
        thresholds, memory = _seed_env(tmp_path, template)
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.status == gov.STATUS_NOT_APPLICABLE for b in report.blocks)
        assert gov.evaluate_exit_code(report, strict=True, allow_pending_drafts=False) == 0


# --------------------------------------------------------------------
# Structural failures → invalid
# --------------------------------------------------------------------
class TestInvalidBlocks:
    def test_duplicate_run_ids(self, tmp_path):
        blocks = _draft_block("dup") + "\n\n" + _draft_block("dup")
        thresholds, memory = _seed_env(tmp_path, blocks)
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.status == gov.STATUS_INVALID
                   and "duplicate run_id" in " ".join(b.problems)
                   for b in report.blocks)
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 2

    def test_malformed_marker_flagged(self, tmp_path):
        text = "# X\n\n<!-- perf-draft:garbage-line -->\n\n"
        thresholds, memory = _seed_env(tmp_path, text)
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert any(b.status == gov.STATUS_INVALID
                   and any("malformed marker" in p for p in b.problems)
                   for b in report.blocks)

    def test_invalid_ordering_in_approved_row(self, tmp_path):
        thresholds, memory = _seed_env(
            tmp_path,
            _approved_block("bad-order", release=1500, warning=800, rollback=500),
        )
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        block = next(b for b in report.blocks if b.run_id == "bad-order")
        assert block.status == gov.STATUS_INVALID
        assert any("ordering violated" in p for p in block.problems)

    def test_mixed_units_in_approved_row(self, tmp_path):
        block = _approved_block("mix-1", unit="ms")
        block = block.replace(
            "| `wall_clock_ms` | 500.0ms | 800.0ms | 1500.0ms |",
            "| `wall_clock_ms` | 500.0s | 800.0ms | 1500.0ms |",
        )
        thresholds, memory = _seed_env(tmp_path, block)
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        block = next(b for b in report.blocks if b.run_id == "mix-1")
        assert block.status == gov.STATUS_INVALID
        assert any(("unit mismatch" in p) or ("unexpected unit" in p) for p in block.problems)

    def test_missing_approval_fields(self, tmp_path):
        block = _approved_block("miss-1")
        block = block.replace(
            "**Approval owner:** Reviewer",
            "**Approval owner:** ____________________",
        )
        thresholds, memory = _seed_env(tmp_path, block)
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        block = next(b for b in report.blocks if b.run_id == "miss-1")
        assert block.status == gov.STATUS_INVALID
        assert any("Approval owner" in p for p in block.problems)


# --------------------------------------------------------------------
# Downstream references + duplicated numbers
# --------------------------------------------------------------------
class TestDownstream:
    def test_broken_reference_fails_in_any_mode(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _approved_block("run-1"))
        (memory / "CLINICAL_MONITORING_PLAN.md").write_text("# no citation here\n")
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert report.downstream_broken
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 2

    def test_duplicated_number_downstream_fails_in_strict(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _approved_block("run-1", release=500))
        # Add the approved 500ms into a downstream doc as if someone had
        # copy-pasted the release budget.
        p = memory / "CLINICAL_MONITORING_PLAN.md"
        p.write_text(
            p.read_text() + "\n\nTimeline P95 rollback: 500ms sustained 30 min.\n"
        )
        report = gov.check(
            thresholds_path=thresholds, memory_dir=memory,
            now=datetime(2026, 2, 15, tzinfo=timezone.utc),
        )
        assert report.downstream_duplicated_numbers
        # Permissive → 0. Strict → fail.
        assert gov.evaluate_exit_code(report, strict=False, allow_pending_drafts=True) == 0
        assert gov.evaluate_exit_code(report, strict=True, allow_pending_drafts=False) == 2


# --------------------------------------------------------------------
# JSON output + CLI + modes
# --------------------------------------------------------------------
class TestJSONOutput:
    def test_json_written(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _approved_block("run-1"))
        out = tmp_path / "gov.json"
        rc = gov.main([
            "--thresholds-file", str(thresholds),
            "--memory-dir", str(memory),
            "--json-output", str(out),
        ])
        assert rc == 0
        payload = json.loads(out.read_text())
        assert payload["thresholds_file"] == str(thresholds)
        assert any(b["run_id"] == "run-1" for b in payload["blocks"])


class TestModes:
    def test_default_permissive_pending_ok(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _draft_block("d-1"))
        rc = gov.main([
            "--thresholds-file", str(thresholds),
            "--memory-dir", str(memory),
        ])
        assert rc == 0

    def test_strict_fails_on_pending(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _draft_block("d-1"))
        rc = gov.main([
            "--thresholds-file", str(thresholds),
            "--memory-dir", str(memory),
            "--strict",
        ])
        assert rc == 2

    def test_allow_pending_drafts_passes(self, tmp_path):
        thresholds, memory = _seed_env(tmp_path, _draft_block("d-1"))
        rc = gov.main([
            "--thresholds-file", str(thresholds),
            "--memory-dir", str(memory),
            "--allow-pending-drafts",
        ])
        assert rc == 0
