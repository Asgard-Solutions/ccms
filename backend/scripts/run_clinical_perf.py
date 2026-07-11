"""G2 Clinical performance measurement harness.

Turns the "20-run measurement pass" documented in
``/app/memory/PHASE3_PERFORMANCE_TEST_PLAN.md`` §Rerun protocol into a
single reproducible command:

    python scripts/run_clinical_perf.py \
      --patient fixture-large-chart-patient-0001 \
      --runs 20 --profile desktop --network normal \
      --confirm-non-production

The harness deliberately does NOT decide whether performance "passes".
Without approved thresholds, its output is labeled
``Measured — threshold approval required``. Threshold approval remains
a platform-reliability decision recorded outside this tool.

Design constraints (per the release-gate closeout brief):

  * Hard-refuse when ``APP_ENV=production``.
  * Require ``--confirm-non-production`` on every run.
  * Require a production frontend build (``/app/frontend/build``).
  * Verify the fixture patient exists and carries enough timeline
    events (delegates to ``scripts.seed_large_chart``).
  * Optionally reseed / cleanup via ``--seed-fixture`` and
    ``--cleanup-fixture``.
  * Run ``--warmup`` iterations (default 3), then ``--runs`` measured
    iterations (default 20, minimum 20).
  * Capture Playwright wall-clock timing AND backend request timing
    separately.
  * Aggregate P50 / P75 / P95 / min / max / error-rate per metric.
  * Write ``PHASE3_PERFORMANCE_RAW_RESULTS.json`` + regenerate
    ``PHASE3_PERFORMANCE_REPORT.md`` in the operator's ``--output-dir``.
  * Never emit patient IDs or record identifiers to telemetry.
  * Fail clearly when the fixture is missing, the build is missing,
    authentication fails, the Clinical page never reaches its ready
    marker, timing fields are missing, or fewer than the requested
    number of successful runs complete.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics  # noqa: F401  (kept for future percentile alternatives)
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

DEFAULT_FIXTURE_PATIENT_ID = "fixture-large-chart-patient-0001"
DEFAULT_FIXTURE_MIN_EVENTS = 250
MIN_MEASURED_RUNS = 20
DEFAULT_WARMUP_RUNS = 3
BACKEND_URL_ENV = "REACT_APP_BACKEND_URL"
FRONTEND_BUILD_DIR = Path("/app/frontend/build")
DEFAULT_OUTPUT_DIR = Path("/app/memory/performance")

METRICS = [
    "wall_clock_ms",              # goto -> waitForSelector(ready marker)
    "response_end_ms",            # nav timing responseEnd
    "dom_content_loaded_ms",      # nav timing domContentLoadedEventEnd
    "load_event_ms",              # nav timing loadEventEnd
    "backend_timeline_ms",        # /clinical/timeline/grouped time
    "backend_encounters_ms",      # /clinical/encounters/grouped time
    "backend_billing_ms",         # /clinical/billing-readiness/aggregate time
]


# --------------------------------------------------------------------
# Custom errors (tests assert on these types)
# --------------------------------------------------------------------
class HarnessError(RuntimeError):
    """Base — every failure path from the harness raises a subclass."""


class ProductionGuardError(HarnessError): ...
class MissingBuildError(HarnessError): ...
class MissingFixtureError(HarnessError): ...
class UndersizedFixtureError(HarnessError): ...
class AuthenticationError(HarnessError): ...
class ReadyMarkerError(HarnessError): ...
class MissingTimingError(HarnessError): ...
class InsufficientRunsError(HarnessError): ...
class DuplicateDraftError(HarnessError): ...
class ApprovedRowProtectionError(HarnessError): ...
class MalformedRunContextError(HarnessError): ...
class InvalidThresholdOrderingError(HarnessError): ...


# --------------------------------------------------------------------
# Threshold-draft management — opt-in via --write-threshold-draft.
#
# The harness carries the clipboard; it does NOT sign the form. It
# appends a draft block containing measured values and REVIEW REQUIRED
# placeholders for the three threshold tiers. Measured values remain
# visually separate from thresholds. Reviewers must fill Release budget
# / Warning / Rollback manually.
# --------------------------------------------------------------------
THRESHOLDS_PATH = Path("/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md")
STALE_WINDOW_DAYS = 180

_MARKER_RE = re.compile(
    r"<!--\s*perf-(draft|approved):run-id=([^\s]+)\s+timestamp=(\S+)\s*-->"
)


def _parse_existing_markers(text: str) -> list[dict]:
    """Scan `text` for perf-draft / perf-approved anchor comments and
    return `{"kind": "draft"|"approved", "run_id": ..., "timestamp": ...}`.
    """
    return [
        {"kind": m.group(1), "run_id": m.group(2), "timestamp": m.group(3)}
        for m in _MARKER_RE.finditer(text)
    ]


def _validate_run_context(meta: dict, run_id: str, raw_path: Path) -> None:
    required = ("patient_id", "fixture_events", "profile", "network", "generated_at")
    if not run_id or not isinstance(run_id, str):
        raise MalformedRunContextError("run_id is required (non-empty string)")
    if not raw_path or not str(raw_path):
        raise MalformedRunContextError("raw_path is required")
    for key in required:
        if meta.get(key) in (None, "", 0):
            raise MalformedRunContextError(f"meta missing required field {key!r}")


def build_draft_block(
    *,
    run_id: str,
    meta: dict,
    summary: dict,
    raw_path: Path,
) -> str:
    """Build the appended markdown block. Measured values live in a
    dedicated section; the threshold tiers are all ``REVIEW REQUIRED``.
    """
    metrics = summary.get("metrics") or {}
    lines = [
        f"<!-- perf-draft:run-id={run_id} timestamp={meta['generated_at']} -->",
        "### Draft proposal (harness-appended, awaiting sign-off)",
        "",
        "**Status:** `AWAITING SIGN-OFF`",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Source run id | `{run_id}` |",
        f"| Generated at | {meta['generated_at']} |",
        f"| Raw results | `{raw_path}` |",
        f"| Profile | `{meta['profile']}` |",
        f"| Network | `{meta['network']}` |",
        f"| Dataset | {meta['fixture_events']} timeline events (`{meta['patient_id']}`) |",
        f"| Browser / device | Chromium (production build) |",
        "",
        "#### Measured values (evidence — not thresholds)",
        "",
        "| Metric | P50 | P75 | P95 | min | max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in METRICS:
        m = metrics.get(name)
        if not m:
            lines.append(f"| `{name}` | — | — | — | — | — |")
            continue
        lines.append(
            f"| `{name}` | {m['p50']:.1f} | {m['p75']:.1f} | {m['p95']:.1f} | "
            f"{m['min']:.1f} | {m['max']:.1f} |"
        )
    lines += [
        "",
        "#### Proposed thresholds (reviewer to fill; measured values are NOT auto-applied)",
        "",
        "| Metric | Proposed release budget | Proposed warning threshold | Proposed rollback threshold |",
        "|---|---|---|---|",
    ]
    for name in METRICS:
        lines.append(f"| `{name}` | REVIEW REQUIRED | REVIEW REQUIRED | REVIEW REQUIRED |")
    lines += [
        "",
        "**Approval owner:** ____________________",
        "",
        "**Approval date:** ____________________",
        "",
        "**Rationale:** ____________________",
        "",
        "**Ordering guarantee:** for every metric, `Release budget < Warning threshold < Rollback threshold`. Any promotion violating this ordering is rejected by `validate_promotion_ordering()`.",
        "",
        "**Notes:**",
        "- Measured values above are evidence, not policy.",
        "- Do NOT copy measured P95 into the release budget column without review.",
        "- Warning and rollback thresholds must include headroom over the release budget to avoid noisy alerts.",
        "- This draft expires 180 days after the timestamp above unless promoted.",
        "",
    ]
    return "\n".join(lines)


def append_threshold_draft(
    *,
    thresholds_path: Path,
    run_id: str,
    meta: dict,
    summary: dict,
    raw_path: Path,
) -> str:
    """Append a draft block to the thresholds document. Rejects
    duplicate run_ids and prevents insertion when the same run_id is
    already recorded as approved."""
    _validate_run_context(meta, run_id, raw_path)
    if not thresholds_path.exists():
        raise MalformedRunContextError(
            f"thresholds document not found at {thresholds_path}"
        )
    text = thresholds_path.read_text()
    for m in _parse_existing_markers(text):
        if m["run_id"] != run_id:
            continue
        if m["kind"] == "approved":
            raise ApprovedRowProtectionError(
                f"run_id {run_id!r} is already recorded as APPROVED; "
                "harness refuses to overwrite an approved row"
            )
        raise DuplicateDraftError(
            f"run_id {run_id!r} already has a draft block; refusing to duplicate"
        )
    block = build_draft_block(run_id=run_id, meta=meta, summary=summary, raw_path=raw_path)
    thresholds_path.write_text(text.rstrip() + "\n\n" + block)
    return block


def is_stale_draft(
    timestamp_iso: str,
    now: datetime | None = None,
    window_days: int = STALE_WINDOW_DAYS,
) -> bool:
    """Return True if a draft's timestamp is older than the review
    window."""
    ref = now or datetime.now(timezone.utc)
    try:
        stamp = datetime.fromisoformat(timestamp_iso)
    except ValueError as exc:
        raise MalformedRunContextError(f"unparseable timestamp {timestamp_iso!r}") from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (ref - stamp).days > window_days


def validate_promotion_ordering(
    release: float, warning: float, rollback: float,
) -> None:
    """Called by the reviewer's promotion step. Ensures the ordering
    guarantee `release < warning < rollback` holds."""
    if not (release < warning < rollback):
        raise InvalidThresholdOrderingError(
            f"threshold ordering violated: release={release} warning={warning} "
            f"rollback={rollback}; required release < warning < rollback"
        )


# --------------------------------------------------------------------
# Production guard
# --------------------------------------------------------------------
def enforce_guard(confirm_non_production: bool) -> None:
    env = (os.environ.get("APP_ENV") or "").strip().lower()
    if env in {"production", "prod"}:
        raise ProductionGuardError(
            "REFUSING TO RUN: APP_ENV=production. The perf harness generates "
            "synthetic traffic against the Clinical page and is not permitted "
            "in production."
        )
    if not confirm_non_production:
        raise ProductionGuardError(
            "REFUSING TO RUN: pass --confirm-non-production to acknowledge "
            "this environment is not production."
        )


# --------------------------------------------------------------------
# Build + fixture verification
# --------------------------------------------------------------------
def verify_production_build(build_dir: Path = FRONTEND_BUILD_DIR) -> None:
    idx = build_dir / "index.html"
    if not idx.exists():
        raise MissingBuildError(
            f"Production frontend build not found at {idx}. "
            "Run `cd /app/frontend && yarn build` before this harness."
        )


async def verify_fixture(
    patient_id: str,
    min_events: int,
) -> dict[str, int]:
    """Count timeline events on the fixture patient and enforce the
    minimum. Never emits patient identifiers to any telemetry endpoint —
    Mongo access is local-only."""
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        db = client[os.environ["DB_NAME"]]
        patient = await db.patients.find_one({"id": patient_id}, {"_id": 0, "id": 1})
        if not patient:
            raise MissingFixtureError(
                f"Fixture patient {patient_id!r} not found. Run "
                "`python -m scripts.seed_large_chart --confirm-non-production` "
                "first."
            )
        counts: dict[str, int] = {}
        for coll, weight in [
            ("appointments", 1), ("clinical_encounters", 1),
            ("clinical_follow_up_notes", 1), ("clinical_diagnoses", 1),
            ("clinical_treatment_plans", 1), ("clinical_initial_exams", 1),
            ("clinical_reexams", 1), ("clinical_outcome_entries", 1),
            ("clinical_media", 1),
        ]:
            counts[coll] = await db[coll].count_documents({"patient_id": patient_id})
        total = sum(counts.values())
        counts["_total_timeline_events"] = total
        if total < min_events:
            raise UndersizedFixtureError(
                f"Fixture has only {total} timeline events; "
                f"need >= {min_events}. Reseed with a larger --events value."
            )
        return counts
    finally:
        client.close()


def seed_or_cleanup_fixture(
    *, seed: bool = False, cleanup: bool = False, events: int = 500,
) -> None:
    if not (seed or cleanup):
        return
    cmd = [sys.executable, "-m", "scripts.seed_large_chart", "--confirm-non-production"]
    if cleanup:
        cmd.append("--cleanup")
    else:
        cmd.extend(["--events", str(events)])
    subprocess.run(cmd, check=True, cwd="/app/backend")


# --------------------------------------------------------------------
# Pure helpers — tested independently of Playwright / Mongo / network.
# --------------------------------------------------------------------
def percentile(values: list[float], p: float) -> float:
    """Percentile using linear interpolation. `p` in [0, 100]."""
    if not values:
        raise ValueError("percentile requires at least one sample")
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    sorted_vals = sorted(values)
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def aggregate(values: list[float]) -> dict[str, float]:
    """P50/P75/P95/min/max over a non-empty list."""
    if not values:
        raise ValueError("aggregate requires at least one sample")
    return {
        "p50": percentile(values, 50),
        "p75": percentile(values, 75),
        "p95": percentile(values, 95),
        "min": float(min(values)),
        "max": float(max(values)),
        "count": len(values),
    }


def summarise_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute per-metric aggregates + error rate over a run list.

    Each run is either ``{"ok": True, "metrics": {...}}`` or
    ``{"ok": False, "error": "..."}``.
    """
    successful = [r for r in runs if r.get("ok")]
    errors = [r for r in runs if not r.get("ok")]
    if not successful:
        return {
            "run_count": len(runs), "successful": 0, "errors": len(errors),
            "error_rate": 1.0 if runs else 0.0, "metrics": {},
        }
    per_metric: dict[str, dict[str, float]] = {}
    for name in METRICS:
        values: list[float] = []
        for r in successful:
            v = r["metrics"].get(name)
            if v is None:
                raise MissingTimingError(f"run missing timing field {name!r}")
            values.append(float(v))
        per_metric[name] = aggregate(values)
    return {
        "run_count": len(runs),
        "successful": len(successful),
        "errors": len(errors),
        "error_rate": len(errors) / len(runs) if runs else 0.0,
        "metrics": per_metric,
    }


def build_report_markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        "# Phase 3 Performance Report — G2 measurement (harness)",
        "",
        f"**Generated:** {meta['generated_at']}",
        f"**Fixture patient:** `{meta['patient_id']}`",
        f"**Fixture timeline events:** {meta['fixture_events']}",
        f"**Profile:** {meta['profile']}",
        f"**Network profile:** {meta['network']}",
        f"**Frontend build:** {meta['build_hash'] or 'production build (hash not captured)'}",
        f"**Warm-up runs (discarded):** {meta['warmup']}",
        f"**Measured runs:** {summary['run_count']} "
        f"(successful={summary['successful']}, errors={summary['errors']})",
        f"**Error rate:** {summary['error_rate'] * 100:.2f}%",
        "",
        "## Result label",
        "",
        f"**{meta['result_label']}**",
        "",
        "## Aggregate metrics",
        "",
        "| Metric | P50 | P75 | P95 | min | max |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in METRICS:
        m = summary["metrics"].get(name)
        if not m:
            lines.append(f"| `{name}` | — | — | — | — | — |")
            continue
        lines.append(
            f"| `{name}` | {m['p50']:.1f} | {m['p75']:.1f} | {m['p95']:.1f} | "
            f"{m['min']:.1f} | {m['max']:.1f} |"
        )
    lines += [
        "",
        "## Threshold approval",
        "",
        "This report does not compare the measured values against a pass/fail "
        "budget. Threshold approval is a platform-reliability decision — the "
        "operator must record the approval in `CLINICAL_RELEASE_GATE_STATUS.md` "
        "before gate G2 can be closed as `COMPLETE — MEETS APPROVED BUDGET`.",
        "",
        "## Raw results",
        "",
        f"Raw per-run JSON: `{meta['raw_path']}`",
        "",
    ]
    return "\n".join(lines)


def write_outputs(
    output_dir: Path,
    raw_runs: list[dict[str, Any]],
    summary: dict[str, Any],
    meta: dict[str, Any],
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / "PHASE3_PERFORMANCE_RAW_RESULTS.json"
    report_path = output_dir / "PHASE3_PERFORMANCE_REPORT.md"
    meta = {**meta, "raw_path": str(raw_path)}
    raw_payload = {"meta": meta, "summary": summary, "runs": raw_runs}
    raw_path.write_text(json.dumps(raw_payload, indent=2))
    report_path.write_text(build_report_markdown(summary, meta))
    return raw_path, report_path


# --------------------------------------------------------------------
# Playwright measurement (imported lazily so tests don't require it)
# --------------------------------------------------------------------
async def _measure_runs(
    *,
    patient_id: str,
    runs: int,
    warmup: int,
    profile: str,
    network: str,
) -> list[dict[str, Any]]:
    """Launch Playwright, sign in as the demo admin, measure ``warmup +
    runs`` navigations to ``?tab=clinical`` on the fixture chart.
    Warm-up runs are executed but excluded from the returned list."""
    try:
        from playwright.async_api import async_playwright  # noqa: WPS433
    except ImportError as exc:  # pragma: no cover
        raise HarnessError(
            "playwright is not installed. `pip install playwright && playwright install chromium`."
        ) from exc

    backend_url = os.environ.get(BACKEND_URL_ENV)
    if not backend_url:
        raise HarnessError(f"{BACKEND_URL_ENV} not set")
    admin_email = os.environ.get("PERF_ADMIN_EMAIL", "admin@ccms.app")
    admin_password = os.environ.get("PERF_ADMIN_PASSWORD", "Admin@ComplianceClinic1")

    results: list[dict[str, Any]] = []
    viewport = {"desktop": {"width": 1920, "height": 900},
                "tablet": {"width": 900, "height": 1200},
                "mobile": {"width": 375, "height": 667}}.get(profile,
                {"width": 1920, "height": 900})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport=viewport)
        if network == "throttled":
            client = await context.new_cdp_session(await context.new_page())
            await client.send("Network.emulateNetworkConditions", {
                "offline": False,
                "downloadThroughput": 750 * 1024 / 8,  # 750 kbps
                "uploadThroughput": 250 * 1024 / 8,
                "latency": 100,
            })
        page = await context.new_page()

        # Sign in.
        await page.goto(f"{backend_url}/login", wait_until="networkidle", timeout=30000)
        await page.fill('input[type=email]', admin_email)
        await page.fill('input[type=password]', admin_password)
        await page.click('button:has-text("Sign in")')
        try:
            await page.wait_for_url("**/**", timeout=15000)
        except Exception as exc:  # pragma: no cover
            raise AuthenticationError(f"login failed: {exc}") from exc

        chart_url = f"{backend_url}/patients/{patient_id}?tab=clinical"

        total_iters = warmup + runs
        for i in range(total_iters):
            run_meta: dict[str, Any] = {"iteration": i + 1, "warmup": i < warmup}
            t0 = await page.evaluate("() => performance.now()")
            try:
                await page.goto(chart_url, wait_until="networkidle", timeout=30000)
                try:
                    await page.wait_for_selector(
                        '[data-testid=clinical-patient-context-header]',
                        timeout=15000,
                    )
                except Exception as exc:
                    raise ReadyMarkerError(
                        "clinical-patient-context-header never appeared"
                    ) from exc
                t1 = await page.evaluate("() => performance.now()")
                nav = await page.evaluate("""() => {
                    const [nav] = performance.getEntriesByType('navigation');
                    if (!nav) return null;
                    return {
                        responseEnd: nav.responseEnd,
                        domContentLoadedEventEnd: nav.domContentLoadedEventEnd,
                        loadEventEnd: nav.loadEventEnd,
                    };
                }""")
                if nav is None:
                    raise MissingTimingError("navigation timing missing")
                # Backend timings via fetch inside the page (avoids
                # separate auth cookie plumbing).
                backend = await page.evaluate(f"""async () => {{
                    async function timed(path) {{
                        const t0 = performance.now();
                        const r = await fetch(path, {{ credentials: 'include' }});
                        const t1 = performance.now();
                        return {{ status: r.status, ms: t1 - t0 }};
                    }}
                    const pid = {json.dumps(patient_id)};
                    return {{
                        timeline: await timed(`/api/patients/${{pid}}/clinical/timeline/grouped`),
                        encounters: await timed(`/api/patients/${{pid}}/clinical/encounters/grouped`),
                        billing: await timed(`/api/patients/${{pid}}/clinical/billing-readiness/aggregate`),
                    }};
                }}""")
                for label in ("timeline", "encounters", "billing"):
                    if backend[label]["status"] >= 500:
                        raise HarnessError(
                            f"backend {label} returned {backend[label]['status']}"
                        )
                run_meta["metrics"] = {
                    "wall_clock_ms": t1 - t0,
                    "response_end_ms": nav["responseEnd"],
                    "dom_content_loaded_ms": nav["domContentLoadedEventEnd"],
                    "load_event_ms": nav["loadEventEnd"],
                    "backend_timeline_ms": backend["timeline"]["ms"],
                    "backend_encounters_ms": backend["encounters"]["ms"],
                    "backend_billing_ms": backend["billing"]["ms"],
                }
                run_meta["ok"] = True
            except (ReadyMarkerError, MissingTimingError, HarnessError) as exc:
                run_meta["ok"] = False
                run_meta["error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                run_meta["ok"] = False
                run_meta["error"] = f"unexpected: {exc!s}"
            results.append(run_meta)

        await context.close()
        await browser.close()

    return results


# --------------------------------------------------------------------
# CLI + console summary
# --------------------------------------------------------------------
@dataclass
class HarnessArgs:
    patient: str
    runs: int
    warmup: int
    profile: str
    network: str
    output_dir: Path
    confirm_non_production: bool
    seed_fixture: bool
    cleanup_fixture: bool
    fixture_events: int
    write_threshold_draft: bool


def _parse_args(argv: list[str] | None = None) -> HarnessArgs:
    p = argparse.ArgumentParser(description="G2 Clinical perf measurement harness")
    p.add_argument("--patient", default=DEFAULT_FIXTURE_PATIENT_ID)
    p.add_argument("--runs", type=int, default=MIN_MEASURED_RUNS)
    p.add_argument("--warmup", type=int, default=DEFAULT_WARMUP_RUNS)
    p.add_argument("--profile", choices=["desktop", "tablet", "mobile"], default="desktop")
    p.add_argument("--network", choices=["normal", "throttled"], default="normal")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--confirm-non-production", action="store_true")
    p.add_argument("--seed-fixture", action="store_true",
                   help="Seed the fixture before measuring.")
    p.add_argument("--cleanup-fixture", action="store_true",
                   help="Cleanup the fixture after measuring.")
    p.add_argument("--fixture-events", type=int, default=500)
    p.add_argument(
        "--write-threshold-draft", action="store_true",
        help=(
            "OPT-IN: after a valid run, append a DRAFT proposal block to "
            "CLINICAL_PERFORMANCE_THRESHOLDS.md. All three threshold tiers "
            "will read 'REVIEW REQUIRED'; measured values are recorded as "
            "evidence but NOT copied into the threshold columns. Default: off."
        ),
    )
    ns = p.parse_args(argv)
    if ns.runs < MIN_MEASURED_RUNS:
        p.error(f"--runs must be >= {MIN_MEASURED_RUNS}")
    return HarnessArgs(
        patient=ns.patient, runs=ns.runs, warmup=ns.warmup,
        profile=ns.profile, network=ns.network,
        output_dir=ns.output_dir,
        confirm_non_production=ns.confirm_non_production,
        seed_fixture=ns.seed_fixture,
        cleanup_fixture=ns.cleanup_fixture,
        fixture_events=ns.fixture_events,
        write_threshold_draft=ns.write_threshold_draft,
    )


def _console_summary(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        f"[clinical-perf] {meta['result_label']}",
        f"[clinical-perf] fixture={meta['patient_id']} events={meta['fixture_events']} "
        f"profile={meta['profile']} network={meta['network']}",
        f"[clinical-perf] runs={summary['run_count']} ok={summary['successful']} "
        f"errors={summary['errors']} error_rate={summary['error_rate']*100:.2f}%",
    ]
    if summary["metrics"]:
        wc = summary["metrics"].get("wall_clock_ms", {})
        tl = summary["metrics"].get("backend_timeline_ms", {})
        lines.append(
            f"[clinical-perf] wall_clock  P50={wc.get('p50',0):.0f}ms "
            f"P95={wc.get('p95',0):.0f}ms max={wc.get('max',0):.0f}ms"
        )
        lines.append(
            f"[clinical-perf] timeline    P50={tl.get('p50',0):.0f}ms "
            f"P95={tl.get('p95',0):.0f}ms max={tl.get('max',0):.0f}ms"
        )
    return "\n".join(lines)



# --------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------
async def _amain(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    enforce_guard(args.confirm_non_production)
    verify_production_build()

    if args.seed_fixture:
        seed_or_cleanup_fixture(seed=True, events=args.fixture_events)

    counts = await verify_fixture(args.patient, min_events=DEFAULT_FIXTURE_MIN_EVENTS)
    fixture_events = counts["_total_timeline_events"]

    runs = await _measure_runs(
        patient_id=args.patient,
        runs=args.runs, warmup=args.warmup,
        profile=args.profile, network=args.network,
    )
    measured = [r for r in runs if not r.get("warmup")]
    if sum(1 for r in measured if r.get("ok")) < args.runs:
        # Enforce the "must record at least --runs successful iterations".
        raise InsufficientRunsError(
            f"only {sum(1 for r in measured if r.get('ok'))} of {args.runs} "
            "measured runs succeeded; refusing to publish partial results"
        )

    summary = summarise_runs(measured)
    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "patient_id": args.patient,
        "fixture_events": fixture_events,
        "profile": args.profile,
        "network": args.network,
        "build_hash": None,
        "warmup": args.warmup,
        "result_label": "Measured — threshold approval required",
    }
    raw_path, report_path = write_outputs(args.output_dir, measured, summary, meta)
    print(_console_summary(summary, {**meta, "raw_path": str(raw_path)}))
    print(f"[clinical-perf] raw:    {raw_path}")
    print(f"[clinical-perf] report: {report_path}")

    if args.write_threshold_draft:
        # run_id derived from the harness timestamp — deterministic per run,
        # never collides across runs, easy to grep for.
        run_id = meta["generated_at"].replace(":", "").replace("-", "")
        try:
            append_threshold_draft(
                thresholds_path=THRESHOLDS_PATH,
                run_id=run_id, meta=meta, summary=summary, raw_path=raw_path,
            )
            print(
                f"[clinical-perf] draft appended: {THRESHOLDS_PATH} "
                f"(run_id={run_id}) — thresholds are REVIEW REQUIRED"
            )
        except (DuplicateDraftError, ApprovedRowProtectionError) as exc:
            print(f"[clinical-perf] draft not appended: {exc}", file=sys.stderr)

    if args.cleanup_fixture:
        seed_or_cleanup_fixture(cleanup=True)

    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(_amain(argv))
    except HarnessError as exc:
        print(f"[clinical-perf] FAIL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
