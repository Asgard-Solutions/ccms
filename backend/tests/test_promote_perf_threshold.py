"""Tests for `scripts/promote_perf_threshold` — the companion to the
harness's `--write-threshold-draft` opt-in. Covers valid promotion,
dry-run, missing/duplicate/stale/already-approved blocks, unresolved
placeholders, invalid ordering, mixed units, missing reviewer fields,
atomic-write with backup, downstream-reference validation, measured
evidence preservation, and idempotent second invocation rejection.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import promote_perf_threshold as pp
from scripts import run_clinical_perf as rcp


# --------------------------------------------------------------------
# Helpers — build a minimal thresholds file with a draft block that
# reviewers have "signed" (all REVIEW REQUIRED replaced with numeric
# values, reviewer fields filled) unless a test asks for otherwise.
# --------------------------------------------------------------------
def _make_summary(**overrides):
    metric = {"p50": 100.0, "p75": 150.0, "p95": 300.0, "min": 80.0, "max": 400.0, "count": 20}
    metrics = {name: dict(metric) for name in rcp.METRICS}
    metrics.update(overrides.get("metrics", {}))
    return {
        "run_count": 20, "successful": 20, "errors": 0,
        "error_rate": 0.0, "metrics": metrics,
    }


def _make_meta(**overrides):
    meta = {
        "generated_at": "2026-02-15T00:00:00+00:00",
        "patient_id": "fixture-large-chart-patient-0001",
        "fixture_events": 500,
        "profile": "desktop", "network": "normal",
        "build_hash": None, "warmup": 3,
        "result_label": "Measured — threshold approval required",
    }
    meta.update(overrides)
    return meta


def _signed_block(
    *,
    run_id: str = "run-1",
    timestamp: str | None = None,
    release: float = 500.0,
    warning: float = 800.0,
    rollback: float = 1500.0,
    unit: str = "ms",
    owner: str = "Platform Reliability Owner",
    date: str = "2026-02-15",
    rationale: str = "Meets P95 budget with 30% headroom.",
) -> str:
    ts = timestamp or "2026-02-15T00:00:00+00:00"
    meta = _make_meta(generated_at=ts)
    block = rcp.build_draft_block(
        run_id=run_id, meta=meta, summary=_make_summary(),
        raw_path=Path("/tmp/raw.json"),
    )
    # Reviewer replaces REVIEW REQUIRED with real values.
    block = block.replace(
        "REVIEW REQUIRED | REVIEW REQUIRED | REVIEW REQUIRED",
        f"{release}{unit} | {warning}{unit} | {rollback}{unit}",
    )
    # Reviewer fills the fields.
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


def _seed_full_env(tmp_path: Path, block: str) -> tuple[Path, Path]:
    """Create a thresholds file with the given block AND every
    downstream doc mentioning the thresholds file so the reference
    validator passes."""
    memory = tmp_path / "memory"
    memory.mkdir()
    thresholds = memory / "CLINICAL_PERFORMANCE_THRESHOLDS.md"
    thresholds.write_text(
        "# Clinical Performance Thresholds — Approval Record\n\n"
        + "**Status:** placeholder\n\n"
        + block
    )
    for name in pp.DOWNSTREAM_DOCS:
        (memory / name).write_text(
            f"# {name}\n\nSee `CLINICAL_PERFORMANCE_THRESHOLDS.md` for approved thresholds.\n"
        )
    return thresholds, memory


# --------------------------------------------------------------------
# Valid promotion + dry run
# --------------------------------------------------------------------
class TestValidPromotion:
    def test_promotes_draft_to_approved(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        result = pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="ignored (reviewer already filled)",
            approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
        )
        text = thresholds.read_text()
        assert "perf-approved:run-id=run-1" in text
        assert "perf-draft:run-id=run-1" not in text
        assert "`APPROVED`" in text
        assert "`AWAITING SIGN-OFF`" not in text
        assert result.backup_path is not None and result.backup_path.exists()
        assert result.thresholds["wall_clock_ms"] == (500.0, 800.0, 1500.0)

    def test_dry_run_does_not_write(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        before = thresholds.read_text()
        result = pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=True,
        )
        assert result.dry_run is True and result.backup_path is None
        assert thresholds.read_text() == before
        assert "@@" in result.diff  # diff was computed

    def test_measured_evidence_table_unchanged(self, tmp_path):
        block = _signed_block(run_id="run-1")
        thresholds, memory = _seed_full_env(tmp_path, block)
        # Grab the measured section from the pre-promotion file.
        before = thresholds.read_text()
        before_evidence = before.split("Measured values (evidence")[1].split("Proposed thresholds")[0]
        pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
        )
        after = thresholds.read_text()
        after_evidence = after.split("Measured values (evidence")[1].split("Proposed thresholds")[0]
        assert before_evidence == after_evidence, "measured evidence table must be byte-identical"

    def test_promotion_stamp_recorded(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
            now=datetime(2026, 2, 15, 12, 0, tzinfo=timezone.utc),
        )
        text = thresholds.read_text()
        assert "**Promoted at:**" in text and "2026-02-15T12:00:00" in text
        assert "**Promoted by:** Platform Reliability Owner" in text
        assert "Immutable history" in text


# --------------------------------------------------------------------
# Locate / dedupe / stale / already-approved
# --------------------------------------------------------------------
class TestLocateBlock:
    def test_missing_run_id_raises(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        with pytest.raises(pp.DraftNotFoundError, match="does-not-exist"):
            pp.promote(
                thresholds_path=thresholds, run_id="does-not-exist",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_duplicate_run_id_raises(self, tmp_path):
        # Two blocks with the same run id — unusual but must be detected.
        block1 = _signed_block(run_id="dup")
        block2 = _signed_block(run_id="dup")
        thresholds, memory = _seed_full_env(tmp_path, block1)
        thresholds.write_text(thresholds.read_text() + "\n\n" + block2)
        with pytest.raises(pp.DuplicateBlockError, match="multiple blocks"):
            pp.promote(
                thresholds_path=thresholds, run_id="dup",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_stale_draft_rejected(self, tmp_path):
        old_ts = (datetime(2026, 2, 15, tzinfo=timezone.utc) - timedelta(days=200)).isoformat()
        thresholds, memory = _seed_full_env(
            tmp_path, _signed_block(run_id="run-1", timestamp=old_ts))
        with pytest.raises(pp.UnresolvedPlaceholderError, match="stale"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
                now=datetime(2026, 2, 15, tzinfo=timezone.utc),
            )

    def test_already_approved_rejected(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        # First promotion succeeds.
        pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
        )
        # Second invocation must be idempotent-rejected.
        with pytest.raises(pp.ApprovedRowProtectionError, match="already promoted"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )


# --------------------------------------------------------------------
# Validation failures on the block content
# --------------------------------------------------------------------
class TestBlockValidation:
    def test_unresolved_placeholder_rejected(self, tmp_path):
        block = _signed_block(run_id="run-1")
        # Put REVIEW REQUIRED back into one row.
        block = block.replace("500.0ms | 800.0ms", "REVIEW REQUIRED | 800.0ms", 1)
        thresholds, memory = _seed_full_env(tmp_path, block)
        with pytest.raises(pp.UnresolvedPlaceholderError, match="REVIEW REQUIRED"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_invalid_ordering_rejected(self, tmp_path):
        block = _signed_block(run_id="run-1", release=1000, warning=200, rollback=500)
        thresholds, memory = _seed_full_env(tmp_path, block)
        with pytest.raises(rcp.InvalidThresholdOrderingError, match="ordering violated"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_mixed_units_rejected(self, tmp_path):
        block = _signed_block(run_id="run-1", release=500.0, warning=800.0, rollback=1500.0, unit="ms")
        block = block.replace(
            "| `wall_clock_ms` | 500.0ms | 800.0ms | 1500.0ms |",
            "| `wall_clock_ms` | 500.0s | 800.0ms | 1500.0ms |",
        )
        thresholds, memory = _seed_full_env(tmp_path, block)
        with pytest.raises(pp.MixedUnitsError, match="unit mismatch|non-numeric|unexpected unit"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_missing_reviewer_field_rejected(self, tmp_path):
        block = _signed_block(run_id="run-1")
        block = block.replace(
            "**Approval owner:** Platform Reliability Owner",
            "**Approval owner:** ____________________",
        )
        thresholds, memory = _seed_full_env(tmp_path, block)
        with pytest.raises(pp.ReviewerFieldError, match="Approval owner"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )


# --------------------------------------------------------------------
# Downstream-reference validation
# --------------------------------------------------------------------
class TestDownstreamReferences:
    def test_missing_reference_rejected(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        # Strip the reference from ONE downstream doc.
        target = memory / "CLINICAL_MONITORING_PLAN.md"
        target.write_text("# CLINICAL_MONITORING_PLAN.md\n\n(no reference here)\n")
        with pytest.raises(pp.DownstreamReferenceError, match="CLINICAL_MONITORING_PLAN"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_missing_downstream_file_rejected(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        (memory / "CLINICAL_ROLLOUT_CHECKLIST.md").unlink()
        with pytest.raises(pp.DownstreamReferenceError, match="file missing"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )

    def test_downstream_docs_never_edited_by_promote(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        before = {name: (memory / name).read_text() for name in pp.DOWNSTREAM_DOCS}
        pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
        )
        after = {name: (memory / name).read_text() for name in pp.DOWNSTREAM_DOCS}
        assert before == after, "promotion must never modify downstream docs"


# --------------------------------------------------------------------
# Atomic write + backup
# --------------------------------------------------------------------
class TestAtomicWrite:
    def test_backup_created(self, tmp_path):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        original = thresholds.read_text()
        result = pp.promote(
            thresholds_path=thresholds, run_id="run-1",
            approved_by="X", approval_date="2026-02-15", rationale=None,
            memory_dir=memory, dry_run=False,
        )
        assert result.backup_path.exists()
        assert result.backup_path.read_text() == original
        assert result.backup_path.name.startswith("CLINICAL_PERFORMANCE_THRESHOLDS.md.backup-")

    def test_atomic_write_failure_surfaces(self, tmp_path, monkeypatch):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        original = thresholds.read_text()

        def _boom(*a, **kw):
            raise OSError("simulated write failure")
        monkeypatch.setattr(pp, "atomic_write", lambda p, c: (_ for _ in ()).throw(pp.AtomicWriteError("simulated")))
        with pytest.raises(pp.AtomicWriteError, match="simulated"):
            pp.promote(
                thresholds_path=thresholds, run_id="run-1",
                approved_by="X", approval_date="2026-02-15", rationale=None,
                memory_dir=memory, dry_run=False,
            )
        # File must be untouched.
        assert thresholds.read_text() == original


# --------------------------------------------------------------------
# CLI + production guard
# --------------------------------------------------------------------
class TestCLI:
    def test_production_guard(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(pp.ProductionOperationError, match="APP_ENV=production"):
            pp._refuse_production()

    def test_confirm_or_dry_run_required(self, tmp_path, monkeypatch):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        monkeypatch.setenv("APP_ENV", "development")
        rc = pp.main([
            "--run-id", "run-1",
            "--thresholds-file", str(thresholds),
            "--approved-by", "X",
            "--approval-date", "2026-02-15",
            "--memory-dir", str(memory),
        ])
        assert rc == 2

    def test_cli_dry_run_ok(self, tmp_path, monkeypatch):
        thresholds, memory = _seed_full_env(tmp_path, _signed_block(run_id="run-1"))
        monkeypatch.setenv("APP_ENV", "development")
        rc = pp.main([
            "--run-id", "run-1",
            "--thresholds-file", str(thresholds),
            "--approved-by", "X",
            "--approval-date", "2026-02-15",
            "--memory-dir", str(memory),
            "--dry-run",
        ])
        assert rc == 0
