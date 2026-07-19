"""Tests for the `--write-threshold-draft` opt-in in
`scripts/run_clinical_perf`.

The harness may carry the clipboard; it must not sign the form.
These tests enforce that contract:

  * Draft blocks are only appended when the reviewer opts in.
  * Measured values are surfaced as evidence but never copied into
    threshold columns.
  * Duplicate insertion for the same run id is rejected.
  * Existing approved rows for the same run id are protected.
  * Stale drafts (>180 days) are detectable.
  * Malformed run context is rejected up-front.
  * Promotion-time ordering (Release < Warning < Rollback) is enforced.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts import run_clinical_perf as rcp


# --------------------------------------------------------------------
# Helpers
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


def _seed_thresholds_file(tmp_path):
    """Minimal thresholds document mirroring the real one enough for
    marker parsing."""
    path = tmp_path / "CLINICAL_PERFORMANCE_THRESHOLDS.md"
    path.write_text(
        "# Clinical Performance Thresholds — Approval Record\n\n"
        "**Status:** `AWAITING FIRST APPROVED RUN`.\n"
    )
    return path


# --------------------------------------------------------------------
# Marker parsing
# --------------------------------------------------------------------
class TestMarkerParsing:
    def test_finds_draft_marker(self):
        text = (
            "some prefix\n"
            "<!-- perf-draft:run-id=20260215T000000000000 timestamp=2026-02-15T00:00:00+00:00 -->\n"
            "block body\n"
        )
        markers = rcp._parse_existing_markers(text)
        assert markers == [{
            "kind": "draft",
            "run_id": "20260215T000000000000",
            "timestamp": "2026-02-15T00:00:00+00:00",
        }]

    def test_finds_approved_marker(self):
        text = (
            "<!-- perf-approved:run-id=abc123 timestamp=2026-02-15T00:00:00+00:00 -->\n"
        )
        markers = rcp._parse_existing_markers(text)
        assert markers[0]["kind"] == "approved"
        assert markers[0]["run_id"] == "abc123"

    def test_ignores_unrelated_html_comments(self):
        text = "<!-- unrelated comment -->\n<!-- perf-note:hello -->\n"
        assert rcp._parse_existing_markers(text) == []


# --------------------------------------------------------------------
# Draft block content
# --------------------------------------------------------------------
class TestBuildDraftBlock:
    def test_status_is_awaiting_signoff(self, tmp_path):
        block = rcp.build_draft_block(
            run_id="abc123", meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        assert "AWAITING SIGN-OFF" in block

    def test_context_tuple_preserved(self, tmp_path):
        block = rcp.build_draft_block(
            run_id="abc123", meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        assert "desktop" in block and "normal" in block
        assert "500 timeline events" in block
        assert "fixture-large-chart-patient-0001" in block
        assert "Chromium (production build)" in block

    def test_measured_values_and_review_required_kept_separate(self, tmp_path):
        block = rcp.build_draft_block(
            run_id="abc123", meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        # Measured section carries actual numbers.
        assert "300.0" in block  # measured P95
        # Threshold section is ALL "REVIEW REQUIRED" — no measured value
        # is copied into any threshold column.
        threshold_section = block.split("Proposed thresholds")[1]
        assert "300.0" not in threshold_section, (
            "measured values must NOT appear in the threshold section"
        )
        # And every metric row in the threshold section is REVIEW REQUIRED.
        for name in rcp.METRICS:
            assert f"| `{name}` | REVIEW REQUIRED | REVIEW REQUIRED | REVIEW REQUIRED |" in threshold_section

    def test_carries_ordering_guarantee_reminder(self, tmp_path):
        block = rcp.build_draft_block(
            run_id="abc123", meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        assert "Release budget < Warning threshold < Rollback threshold" in block

    def test_stale_notice_included(self, tmp_path):
        block = rcp.build_draft_block(
            run_id="abc123", meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        assert "180 days" in block


# --------------------------------------------------------------------
# append_threshold_draft
# --------------------------------------------------------------------
class TestAppendThresholdDraft:
    def test_appends_when_file_valid_and_no_existing(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        block = rcp.append_threshold_draft(
            thresholds_path=path,
            run_id="20260215T000000000000",
            meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        assert "AWAITING SIGN-OFF" in block
        text = path.read_text()
        assert "perf-draft:run-id=20260215T000000000000" in text

    def test_duplicate_draft_rejected(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        rcp.append_threshold_draft(
            thresholds_path=path, run_id="dup-run",
            meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        with pytest.raises(rcp.DuplicateDraftError, match="dup-run"):
            rcp.append_threshold_draft(
                thresholds_path=path, run_id="dup-run",
                meta=_make_meta(), summary=_make_summary(),
                raw_path=tmp_path / "raw.json",
            )

    def test_approved_row_protection(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        # Simulate a pre-existing approved row for the same run id.
        path.write_text(
            path.read_text()
            + "\n<!-- perf-approved:run-id=already-signed timestamp=2026-01-01T00:00:00+00:00 -->\n"
        )
        with pytest.raises(rcp.ApprovedRowProtectionError, match="already-signed"):
            rcp.append_threshold_draft(
                thresholds_path=path, run_id="already-signed",
                meta=_make_meta(), summary=_make_summary(),
                raw_path=tmp_path / "raw.json",
            )

    def test_missing_thresholds_file_rejected(self, tmp_path):
        missing = tmp_path / "does-not-exist.md"
        with pytest.raises(rcp.MalformedRunContextError, match="not found"):
            rcp.append_threshold_draft(
                thresholds_path=missing, run_id="abc123",
                meta=_make_meta(), summary=_make_summary(),
                raw_path=tmp_path / "raw.json",
            )

    def test_malformed_run_context_missing_meta(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        bad_meta = _make_meta()
        del bad_meta["patient_id"]
        with pytest.raises(rcp.MalformedRunContextError, match="patient_id"):
            rcp.append_threshold_draft(
                thresholds_path=path, run_id="abc123",
                meta=bad_meta, summary=_make_summary(),
                raw_path=tmp_path / "raw.json",
            )

    def test_malformed_run_id_rejected(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        with pytest.raises(rcp.MalformedRunContextError, match="run_id is required"):
            rcp.append_threshold_draft(
                thresholds_path=path, run_id="",
                meta=_make_meta(), summary=_make_summary(),
                raw_path=tmp_path / "raw.json",
            )

    def test_second_distinct_run_appends_cleanly(self, tmp_path):
        path = _seed_thresholds_file(tmp_path)
        rcp.append_threshold_draft(
            thresholds_path=path, run_id="run-a",
            meta=_make_meta(), summary=_make_summary(),
            raw_path=tmp_path / "raw.json",
        )
        rcp.append_threshold_draft(
            thresholds_path=path, run_id="run-b",
            meta=_make_meta(generated_at="2026-02-16T00:00:00+00:00"),
            summary=_make_summary(),
            raw_path=tmp_path / "raw2.json",
        )
        text = path.read_text()
        assert "perf-draft:run-id=run-a" in text
        assert "perf-draft:run-id=run-b" in text


# --------------------------------------------------------------------
# Stale drafts
# --------------------------------------------------------------------
class TestStaleDraft:
    def test_fresh_draft_is_not_stale(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        ts = (now - timedelta(days=10)).isoformat()
        assert rcp.is_stale_draft(ts, now=now) is False

    def test_draft_exactly_at_window_is_not_stale(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        ts = (now - timedelta(days=180)).isoformat()
        assert rcp.is_stale_draft(ts, now=now) is False

    def test_draft_past_window_is_stale(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        ts = (now - timedelta(days=181)).isoformat()
        assert rcp.is_stale_draft(ts, now=now) is True

    def test_naive_timestamp_is_treated_as_utc(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        ts = (now - timedelta(days=181)).replace(tzinfo=None).isoformat()
        assert rcp.is_stale_draft(ts, now=now) is True

    def test_bogus_timestamp_raises(self):
        with pytest.raises(rcp.MalformedRunContextError, match="unparseable"):
            rcp.is_stale_draft("not-a-timestamp")


# --------------------------------------------------------------------
# Promotion-time ordering validator
# --------------------------------------------------------------------
class TestValidatePromotionOrdering:
    def test_valid_ordering_passes(self):
        rcp.validate_promotion_ordering(release=100, warning=200, rollback=400)

    def test_equal_values_rejected(self):
        with pytest.raises(rcp.InvalidThresholdOrderingError, match="ordering violated"):
            rcp.validate_promotion_ordering(release=100, warning=100, rollback=200)

    def test_reversed_rejected(self):
        with pytest.raises(rcp.InvalidThresholdOrderingError):
            rcp.validate_promotion_ordering(release=400, warning=200, rollback=100)

    def test_warning_above_rollback_rejected(self):
        with pytest.raises(rcp.InvalidThresholdOrderingError):
            rcp.validate_promotion_ordering(release=100, warning=500, rollback=200)


# --------------------------------------------------------------------
# CLI flag
# --------------------------------------------------------------------
class TestCLIThresholdDraftFlag:
    def test_flag_defaults_off(self):
        args = rcp._parse_args(["--confirm-non-production"])
        assert args.write_threshold_draft is False

    def test_flag_opt_in(self):
        args = rcp._parse_args([
            "--confirm-non-production", "--write-threshold-draft",
        ])
        assert args.write_threshold_draft is True
