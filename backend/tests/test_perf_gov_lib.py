"""Direct library tests for `scripts/_perf_gov_lib` — pins the stable
public surface so a future refactor cannot silently change the shared
vocabulary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import _perf_gov_lib as lib


class TestConstants:
    def test_renewal_period(self):
        assert lib.RENEWAL_PERIOD_DAYS == 180

    def test_downstream_documents_frozen(self):
        assert lib.DOWNSTREAM_DOCUMENTS == (
            "CLINICAL_MONITORING_PLAN.md",
            "CLINICAL_STAGED_ROLLOUT_PLAN.md",
            "CLINICAL_ROLLOUT_CHECKLIST.md",
            "CLINICAL_GA_READINESS.md",
            "PHASE3_PERFORMANCE_TEST_PLAN.md",
        )

    def test_downstream_docs_alias(self):
        assert lib.DOWNSTREAM_DOCS is lib.DOWNSTREAM_DOCUMENTS

    def test_thresholds_file_token(self):
        assert lib.THRESHOLDS_FILE_TOKEN == "CLINICAL_PERFORMANCE_THRESHOLDS.md"


class TestMarkerParsing:
    def test_finds_draft(self):
        text = "<!-- perf-draft:run-id=abc timestamp=2026-02-15T00:00:00+00:00 -->"
        assert lib.parse_existing_markers(text) == [
            {"kind": "draft", "run_id": "abc",
             "timestamp": "2026-02-15T00:00:00+00:00"}
        ]

    def test_finds_approved(self):
        text = "<!-- perf-approved:run-id=xyz timestamp=2026-02-15T00:00:00+00:00 -->"
        m = lib.parse_existing_markers(text)
        assert m[0]["kind"] == "approved" and m[0]["run_id"] == "xyz"

    def test_malformed_marker_ignored(self):
        assert lib.parse_existing_markers("<!-- perf-draft:garbage -->") == []


class TestStaleness:
    def test_within_window(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        assert lib.is_stale_draft((now - timedelta(days=10)).isoformat(), now=now) is False

    def test_boundary_not_stale(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        assert lib.is_stale_draft((now - timedelta(days=180)).isoformat(), now=now) is False

    def test_past_window(self):
        now = datetime(2026, 2, 15, tzinfo=timezone.utc)
        assert lib.is_stale_draft((now - timedelta(days=181)).isoformat(), now=now) is True

    def test_bogus_raises(self):
        with pytest.raises(lib.MalformedRunContextError):
            lib.is_stale_draft("not-a-date")


class TestNumericParsing:
    def test_bare(self):
        v, u = lib.parse_number("300")
        assert v == 300.0 and u is None

    def test_with_ms(self):
        assert lib.parse_number("300ms") == (300.0, "ms")

    def test_review_required_rejected(self):
        with pytest.raises(lib.UnresolvedPlaceholderError):
            lib.parse_number("REVIEW REQUIRED")

    def test_mixed_unit_rejected(self):
        with pytest.raises(lib.MixedUnitsError):
            lib.parse_number("300s")

    def test_expected_unit_mismatch(self):
        with pytest.raises(lib.MixedUnitsError):
            lib.parse_number("300s", expected_unit="ms")


class TestOrdering:
    def test_ok(self):
        lib.validate_promotion_ordering(release=1, warning=2, rollback=3)

    def test_equal_rejected(self):
        with pytest.raises(lib.InvalidThresholdOrderingError):
            lib.validate_promotion_ordering(release=1, warning=1, rollback=2)

    def test_reversed_rejected(self):
        with pytest.raises(lib.InvalidThresholdOrderingError):
            lib.validate_promotion_ordering(release=3, warning=2, rollback=1)


class TestReviewerFields:
    def test_all_filled(self):
        block = [
            "**Approval owner:** Person",
            "**Approval date:** 2026-02-15",
            "**Rationale:** because",
        ]
        got = lib.parse_reviewer_fields(block)
        assert got == {"Approval owner": "Person",
                        "Approval date": "2026-02-15",
                        "Rationale": "because"}

    def test_blank_rejected(self):
        block = [
            "**Approval owner:** ____________________",
            "**Approval date:** 2026-02-15",
            "**Rationale:** because",
        ]
        with pytest.raises(lib.ReviewerFieldError, match="Approval owner"):
            lib.parse_reviewer_fields(block)


class TestContextTuple:
    def test_missing_field_raises(self):
        with pytest.raises(lib.MalformedRunContextError):
            lib.parse_context_tuple(["| Source run id | X |"])


class TestDownstreamReferenceValidator:
    def test_broken_reference_raises(self, tmp_path):
        # Only one doc present with citation; others missing.
        (tmp_path / "CLINICAL_MONITORING_PLAN.md").write_text(
            "See CLINICAL_PERFORMANCE_THRESHOLDS.md"
        )
        with pytest.raises(lib.DownstreamReferenceError, match="file missing"):
            lib.validate_downstream_references(tmp_path)

    def test_all_present_and_citing_passes(self, tmp_path):
        for name in lib.DOWNSTREAM_DOCUMENTS:
            (tmp_path / name).write_text("Refers to CLINICAL_PERFORMANCE_THRESHOLDS.md")
        lib.validate_downstream_references(tmp_path)


class TestDeterministicOutput:
    def test_parse_existing_markers_pure(self):
        text = "<!-- perf-draft:run-id=r1 timestamp=2026-01-01T00:00:00+00:00 -->"
        assert lib.parse_existing_markers(text) == lib.parse_existing_markers(text)

    def test_parse_number_pure(self):
        assert lib.parse_number("500ms") == lib.parse_number("500ms")


class TestRunContextValidator:
    def test_happy_path(self, tmp_path):
        lib.validate_run_context(
            meta={"patient_id": "p", "fixture_events": 500,
                  "profile": "desktop", "network": "normal",
                  "generated_at": "2026-02-15T00:00:00+00:00"},
            run_id="r1", raw_path=tmp_path / "raw.json",
        )

    def test_missing_meta_raises(self, tmp_path):
        with pytest.raises(lib.MalformedRunContextError, match="patient_id"):
            lib.validate_run_context(
                meta={"fixture_events": 500, "profile": "desktop",
                      "network": "normal",
                      "generated_at": "2026-02-15T00:00:00+00:00"},
                run_id="r1", raw_path=tmp_path / "raw.json",
            )

    def test_empty_run_id_raises(self, tmp_path):
        with pytest.raises(lib.MalformedRunContextError):
            lib.validate_run_context(
                meta={"patient_id": "p", "fixture_events": 500,
                      "profile": "desktop", "network": "normal",
                      "generated_at": "x"},
                run_id="", raw_path=tmp_path / "raw.json",
            )
