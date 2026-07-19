"""Tests for `scripts/run_clinical_perf` — the G2 measurement harness.

Covers the pure helper surface (percentile math, warm-up exclusion,
error rate, malformed timing, report generation, production guard,
missing build). Playwright + Mongo layers are exercised by the harness
against a live environment; the tests here focus on everything that
runs without a browser.
"""
from __future__ import annotations

import json

import pytest

from scripts import run_clinical_perf as rcp


class TestProductionGuard:
    def test_app_env_production_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(rcp.ProductionGuardError, match="APP_ENV=production"):
            rcp.enforce_guard(confirm_non_production=True)

    def test_app_env_prod_alias_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "prod")
        with pytest.raises(rcp.ProductionGuardError, match="APP_ENV=production"):
            rcp.enforce_guard(confirm_non_production=True)

    def test_missing_confirm_flag_raises(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        with pytest.raises(rcp.ProductionGuardError, match="--confirm-non-production"):
            rcp.enforce_guard(confirm_non_production=False)

    def test_confirm_flag_and_non_production_passes(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "development")
        rcp.enforce_guard(confirm_non_production=True)


class TestBuildGuard:
    def test_missing_build_raises(self, tmp_path):
        with pytest.raises(rcp.MissingBuildError, match="Production frontend build"):
            rcp.verify_production_build(tmp_path)

    def test_present_build_passes(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        rcp.verify_production_build(tmp_path)


class TestPercentile:
    def test_p50_of_odd_series(self):
        assert rcp.percentile([1, 2, 3, 4, 5], 50) == 3.0

    def test_p50_of_even_series_interpolates(self):
        assert rcp.percentile([1, 2, 3, 4], 50) == pytest.approx(2.5)

    def test_p95_upper_bound(self):
        assert rcp.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)

    def test_p0_is_min(self):
        assert rcp.percentile([5, 3, 8, 1], 0) == 1.0

    def test_p100_is_max(self):
        assert rcp.percentile([5, 3, 8, 1], 100) == 8.0

    def test_single_value_series(self):
        assert rcp.percentile([42], 50) == 42.0
        assert rcp.percentile([42], 95) == 42.0

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="requires at least one sample"):
            rcp.percentile([], 50)

    def test_p_out_of_range_raises(self):
        with pytest.raises(ValueError, match=r"p must be in \[0, 100\]"):
            rcp.percentile([1, 2, 3], -1)
        with pytest.raises(ValueError, match=r"p must be in \[0, 100\]"):
            rcp.percentile([1, 2, 3], 101)


class TestAggregate:
    def test_aggregate_keys_present(self):
        r = rcp.aggregate([1, 2, 3, 4, 5])
        assert set(r) == {"p50", "p75", "p95", "min", "max", "count"}
        assert r["min"] == 1 and r["max"] == 5 and r["count"] == 5


def _ok(**overrides):
    metrics = {name: overrides.get(name, 100.0) for name in rcp.METRICS}
    return {"ok": True, "metrics": metrics}


class TestSummariseRuns:
    def test_warmup_exclusion_is_the_callers_responsibility(self):
        summary = rcp.summarise_runs([_ok() for _ in range(3)])
        assert summary["successful"] == 3 and summary["run_count"] == 3

    def test_error_rate_calculation(self):
        runs = [_ok() for _ in range(18)] + [{"ok": False, "error": "boom"} for _ in range(2)]
        s = rcp.summarise_runs(runs)
        assert s["successful"] == 18 and s["errors"] == 2
        assert s["error_rate"] == pytest.approx(0.10)

    def test_all_failed_returns_empty_metrics(self):
        s = rcp.summarise_runs([{"ok": False, "error": "boom"} for _ in range(5)])
        assert s["successful"] == 0 and s["metrics"] == {}
        assert s["error_rate"] == 1.0

    def test_empty_run_list_is_neutral(self):
        s = rcp.summarise_runs([])
        assert s["successful"] == 0 and s["run_count"] == 0
        assert s["error_rate"] == 0.0

    def test_missing_timing_field_raises(self):
        bad = _ok()
        del bad["metrics"]["wall_clock_ms"]
        with pytest.raises(rcp.MissingTimingError, match="wall_clock_ms"):
            rcp.summarise_runs([bad, _ok()])

    def test_none_timing_field_raises(self):
        bad = _ok()
        bad["metrics"]["backend_timeline_ms"] = None
        with pytest.raises(rcp.MissingTimingError, match="backend_timeline_ms"):
            rcp.summarise_runs([bad])

    def test_percentiles_computed_across_runs(self):
        runs = [_ok(wall_clock_ms=100 + i) for i in range(20)]
        s = rcp.summarise_runs(runs)
        wc = s["metrics"]["wall_clock_ms"]
        assert wc["min"] == 100 and wc["max"] == 119
        assert wc["p50"] == pytest.approx(109.5)
        assert wc["p95"] == pytest.approx(118.05)


class TestReportGeneration:
    def _meta(self):
        return {
            "generated_at": "2026-02-15T00:00:00+00:00",
            "patient_id": "fixture-large-chart-patient-0001",
            "fixture_events": 500,
            "profile": "desktop", "network": "normal",
            "build_hash": None, "warmup": 3,
            "result_label": "Measured — threshold approval required",
        }

    def test_write_outputs_creates_json_and_md(self, tmp_path):
        runs = [_ok() for _ in range(20)]
        summary = rcp.summarise_runs(runs)
        raw_path, report_path = rcp.write_outputs(tmp_path, runs, summary, self._meta())
        assert raw_path.exists() and report_path.exists()
        raw = json.loads(raw_path.read_text())
        assert raw["summary"]["successful"] == 20
        assert raw["meta"]["result_label"] == "Measured — threshold approval required"
        md = report_path.read_text()
        assert "Measured — threshold approval required" in md
        assert "wall_clock_ms" in md
        assert "backend_timeline_ms" in md
        assert "fixture-large-chart-patient-0001" in md

    def test_report_never_asserts_pass_without_thresholds(self, tmp_path):
        runs = [_ok(wall_clock_ms=999999.0) for _ in range(20)]
        summary = rcp.summarise_runs(runs)
        _, report_path = rcp.write_outputs(tmp_path, runs, summary, self._meta())
        md = report_path.read_text()
        assert "PASS" not in md
        assert "FAIL" not in md


class TestCLI:
    def test_defaults(self):
        args = rcp._parse_args(["--confirm-non-production"])
        assert args.runs == rcp.MIN_MEASURED_RUNS
        assert args.warmup == rcp.DEFAULT_WARMUP_RUNS
        assert args.profile == "desktop"
        assert args.network == "normal"
        assert args.patient == rcp.DEFAULT_FIXTURE_PATIENT_ID

    def test_runs_below_minimum_rejected(self, capsys):
        with pytest.raises(SystemExit):
            rcp._parse_args(["--confirm-non-production", "--runs", "10"])

    def test_throttled_network(self):
        args = rcp._parse_args(["--confirm-non-production", "--network", "throttled"])
        assert args.network == "throttled"

    def test_cleanup_and_seed_flags(self):
        args = rcp._parse_args([
            "--confirm-non-production", "--seed-fixture", "--cleanup-fixture",
        ])
        assert args.seed_fixture is True and args.cleanup_fixture is True

    def test_custom_output_dir(self, tmp_path):
        args = rcp._parse_args([
            "--confirm-non-production", "--output-dir", str(tmp_path),
        ])
        assert args.output_dir == tmp_path
