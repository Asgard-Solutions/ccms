# Phase 3 Performance — Test plan

**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze date:** 2026-02-15.

## Purpose

Establish reproducible P50/P75/P95 measurements for the frozen Clinical page across the representative chart-size profiles. Provide the raw data that supports the release-gate G2 decision.

## Datasets

| Profile | Timeline events | Source | Notes |
|---|---:|---|---|
| Small | < 25 | Riverbend demo — Aria Johnson (`patient_id=…`) | Bulk of demo personas |
| Medium | 50–100 | Riverbend demo — Isabella Cho | PIP follow-up chart |
| Large | 200–500 | Synthetic — `python -m scripts.seed_large_chart --confirm-non-production --events 500` (available) | Fixture landed 2026-02-15; deterministic patient id `fixture-large-chart-patient-0001` |
| Stress | > 500 | Synthetic — `python -m scripts.seed_large_chart --confirm-non-production --events 1000` (available) | Same fixture; 1000 events verified end-to-end |

## Instrumentation

Two layers of measurement, both PHI-safe:

1. **Playwright timing.** `page.goto(...)` + `waitForSelector('[data-testid=clinical-patient-context-header]')` boundary. Captures wall-clock DOM-ready time.
2. **Browser Performance API.** `performance.getEntriesByType('navigation')` for `responseEnd`, `domContentLoadedEventEnd`, `loadEventEnd`. Separates network latency from client render.

Additional per-section marks are documented for optional adoption (behind a `?perf=1` guard so production is unaffected):

- `clinical.currentCareStatus.render`
- `clinical.nextActions.render`
- `clinical.groupedEncounters.load`
- `clinical.groupedTimeline.load`
- `clinical.timeline.filter.apply`
- `clinical.outcomeSnapshot.render`
- `clinical.imaging.render`
- `clinical.dataQuality.render`
- `clinical.workspaceMode.switch`
- `clinical.summaryConfig.save`
- `clinical.returnState.restore`
- `clinical.deepLink.navigate`

These marks are **not yet shipped in-tree**. Adding them requires a release-gate-scoped follow-up because the freeze accepts only verified defects; performance instrumentation counts as a hardening addition, not a defect fix.

## Measurement rules

- 3 runs minimum, 20 runs preferred. This pass captured **3 runs** per profile because of environment limits.
- Warm-up: 1 discard run per browser session.
- Production-like build not exercised (dev server is running in this preview). Repeat under `yarn build` before final pilot decision.
- Network profile: preview network (no artificial throttling this pass). Recommended follow-up: throttle to `Slow 3G` and `Regular 3G` in Chromium DevTools before pilot.
- Device profile: default. Recommended follow-up: `CPU throttling: 4× slowdown` in Chromium DevTools before pilot.
- Record the browser, OS, and viewport.

## Approved performance thresholds

**None currently approved.** No existing project-level threshold document sets a Clinical-page budget. This pass therefore reports raw measurements plus **proposed** thresholds that require platform-reliability approval before the gate can be marked `COMPLETE — MEETS APPROVED BUDGET`.

### Proposed thresholds (require approval)

| Metric | Proposed P50 | Proposed P95 | Rationale |
|---|---:|---:|---|
| Time-to-first-meaningful-Clinical-content | ≤ 1500 ms | ≤ 3000 ms | Matches EHR benchmarks and existing dashboard perf goals |
| Timeline load (grouped) | ≤ 400 ms | ≤ 900 ms | Local backend; MongoDB with indexed reads |
| Timeline filter apply | ≤ 150 ms | ≤ 400 ms | Client-only filter over pre-fetched events |
| Outcome trend chart render | ≤ 100 ms | ≤ 300 ms | SVG rendering, ≤ 24 months of points |
| Workspace-mode switch | ≤ 200 ms | ≤ 500 ms | Preference PATCH + section reorder |

**Approval required from:** Platform reliability lead. Until approved, no gate is marked `COMPLETE — MEETS APPROVED BUDGET`.

## Virtualization decision

Do not introduce a virtualization library unless P95 shows unacceptable timeline render at 200+ events. Existing `INITIAL_RENDER_CAP = 100` + `Load more` button already paginates. Slice 2 also emits a `console.info` `perf: long timeline` hint at 200+ events so ops can decide.

**This pass:** the fixture seeder `scripts/seed_large_chart.py` now ships and can generate a deterministic 250 / 500 / 1000 event patient chart on demand (non-production only, `--confirm-non-production` required every run). The measurement pass against these charts is the last release-gate step before pilot Stage 2. See §Rerun protocol below.

## Rerun protocol (mandatory before pilot Stage 2)

1. Confirm `APP_ENV != production`. The seeder hard-refuses on production.
2. Seed the large chart:
   ```
   cd /app/backend && APP_ENV=development python -m scripts.seed_large_chart \
     --confirm-non-production --events 500
   ```
   The seeder prints the deterministic patient id (`fixture-large-chart-patient-0001`) to the operator console only. Do not paste it into telemetry.
3. Build the frontend in production mode: `cd /app/frontend && yarn build`.
4. Serve the built bundle behind the standard nginx / static host (not the craco dev server).
5. Execute the Playwright timing harness with **at least 20 warm runs per profile**, capturing:
   - Wall-clock `page.goto → waitForSelector('[data-testid=clinical-patient-context-header]')`.
   - `performance.getEntriesByType('navigation')` — `responseEnd`, `domContentLoadedEventEnd`, `loadEventEnd`.
   - Backend request timings via `curl -w %{time_starttransfer}` on `/api/patients/<fixture_id>/clinical/{timeline,encounters,billing-readiness}/grouped`.
6. Repeat with `--events 1000` for the stress profile.
7. Record P50 / P75 / P95 / max / min / error rate per metric.
8. Compare against the proposed thresholds. Escalate to platform reliability for approval.
9. Run `--cleanup` after the measurement pass so the fixture rows do not linger on staging.
10. Update `PHASE3_PERFORMANCE_REPORT.md` with the large-chart figures and file the platform-reliability decision.

Until platform reliability signs off, gate G2 remains `COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED`.

## Automation

The rerun protocol is scripted as an instrument, not a ceremony:

    cd /app/backend && APP_ENV=development python -m scripts.run_clinical_perf \
      --patient fixture-large-chart-patient-0001 \
      --runs 20 --warmup 3 \
      --profile desktop --network normal \
      --seed-fixture --fixture-events 500 \
      --confirm-non-production

The harness (backed by 29 unit tests in `backend/tests/test_run_clinical_perf.py`):

- Refuses to run when `APP_ENV=production`.
- Refuses to run without `--confirm-non-production`.
- Refuses to run when `/app/frontend/build/index.html` is missing (`yarn build` is a prerequisite).
- Verifies fixture patient exists and carries ≥ 250 timeline events; else raises `MissingFixtureError` / `UndersizedFixtureError`.
- Executes `--warmup` iterations (default 3, discarded), then `--runs` measured iterations (minimum 20).
- Captures wall-clock (Playwright), navigation timings (`responseEnd`, `domContentLoadedEventEnd`, `loadEventEnd`), and per-endpoint backend latencies (`timeline/grouped`, `encounters/grouped`, `billing-readiness/aggregate`) separately.
- Emits `PHASE3_PERFORMANCE_RAW_RESULTS.json` + `PHASE3_PERFORMANCE_REPORT.md` in `--output-dir` (default `/app/memory/performance`).
- Labels the report **Measured — threshold approval required**. The harness never asserts pass/fail without approved thresholds.
- Fails the whole run (exit code 2) when fewer than `--runs` successful iterations complete, when the Clinical page never reaches its ready marker, when a timing field is missing, or when any backend endpoint returns ≥ 500.
- Never emits patient identifiers to telemetry — Mongo access is local-only; the patient id appears only in the operator's own JSON + Markdown report.

Supported profiles: `desktop` (1920×900), `tablet` (900×1200), `mobile` (375×667). Supported networks: `normal`, `throttled` (750 kbps / 100 ms latency via Chromium CDP).
