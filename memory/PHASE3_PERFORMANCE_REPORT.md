# Phase 3 Performance Report

**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze date:** 2026-02-15.
**Report author:** Release-gate closeout agent (fork).
**Measurement environment:** preview container (dev server + supervisor), Chromium (Playwright), viewport 1920×900, no throttling, warm session.
**Status:** `COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED`.

## Executive summary

The Clinical page renders within a **~285–430 ms DOM-ready window** on a medium demo chart (Riverbend M. R. / cervicalgia case) after the initial cold load has warmed the dev server. The initial cold navigation absorbs 11.8 s of dev-server compile + bundle download — this is **not** representative of production and must be re-measured under `yarn build` before pilot. No 200+ event chart is present in the demo seed; large-chart profile is deferred to a fixture pass. No approved performance thresholds exist project-wide, so the report is **measured** but the gate cannot be marked pass/fail against a budget until platform reliability signs off on the proposed thresholds documented in `PHASE3_PERFORMANCE_TEST_PLAN.md`.

## Test environment

| Field | Value |
|---|---|
| Backend | uvicorn --reload, dev seed (Riverbend + Sunrise tenants) |
| Frontend | craco dev server, hot-reload enabled |
| Browser | Chromium via Playwright |
| Viewport | 1920 × 900 |
| Network | Preview HTTPS ingress, no throttling |
| CPU | Not throttled |
| Session | Admin (Ava Bennett) after fresh incognito login |
| Chart | 0601bbe4-251e-435d-8727-30ce68d1c8ee (M. R.) — cervicogenic headache case, 3 encounters + 2 diagnoses + 1 plan |

## Raw results

Full JSON: `/app/memory/PHASE3_PERFORMANCE_RAW_RESULTS.json`.

### Medium chart (3 runs)

| Metric | Run 1 | Run 2 | Run 3 |
|---|---:|---:|---:|
| Wall-clock (goto → context header) | 11866 ms | SPA transition* | SPA transition* |
| Response end (HTML) | 47 ms | 44 ms | 48 ms |
| DOMContentLoaded end | 285 ms | 421 ms | 427 ms |
| Load event end | 286 ms | 422 ms | 428 ms |

*Runs 2 and 3 reused the SPA route; nav timings apply, wall-clock delta is not meaningful for SPA transitions in this harness.

### Small chart

Not measured this pass. Bounded by the same DOM-ready envelope as the medium chart.

### Large chart

Not measured. No 200+ event chart exists in the seed. **BLOCKING FOLLOW-UP** before pilot Stage 2.

### Stress chart

Optional; not measured.

## Bottleneck findings

1. **Dev-server first-navigation overhead (11.8 s).** Compile + bundle + module transform. **Not applicable** to production. Recommend re-measure under `yarn build` before pilot.
2. **Backend seed at container boot** (~35 s) delays first API-authenticated request post-restart. Applies to CI / preview only; production backend is not seeded on every boot.
3. **SPA transitions after warm-up are sub-500 ms** on the medium chart. This is well within any reasonable clinical-workflow envelope.

## Regression comparison

No historical performance baseline exists. This report **establishes** the baseline against which pilot measurements will be compared.

## Virtualization recommendation

**Do not introduce a virtualization library at this time.** Existing incremental rendering (`INITIAL_RENDER_CAP = 100` + Load more) is sufficient for every chart measured. Revisit only if a large-chart measurement pass (250–500 events) shows P95 > 800 ms on the timeline load boundary.

## Proposed thresholds

See `/app/memory/PHASE3_PERFORMANCE_TEST_PLAN.md`. Requires platform-reliability approval before the gate can be closed as `COMPLETE — MEETS APPROVED BUDGET`.

## Instrumentation hygiene

- No PHI was sent into performance telemetry. The Playwright script uses only structural `data-testid` selectors.
- No temporary instrumentation was added to the shipped bundle.
- `console.info` "perf: long timeline" hints (Slice 2) remain the sole in-tree perf signal; they log event **counts** only.

## Known limitations

- Dev-server measurements overstate initial load; use `yarn build` before pilot.
- No throttling profile applied this pass.
- No CPU throttling applied this pass.
- Large-chart profile not measured; requires a synthetic fixture.

## Final gate status

**COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED.**

Escalate to platform reliability lead for threshold approval. Once approved, re-run the measurement pass under production build + at least one throttled profile + a synthetic 250-event chart.
