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
| Large | 200–500 | Synthetic — must be seeded via `scripts/seed_large_chart.py` (proposed) | Not present in current demo seed; requires fixture pass |
| Stress | > 500 | Synthetic — same fixture pass | Optional / not measured this pass |

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

**This pass:** insufficient data — no 200+ event chart exists in the demo seed. Recommendation: run the fixture seeder and re-measure before pilot.
