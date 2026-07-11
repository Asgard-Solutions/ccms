# Automated test results — Clinical redesign release evidence

**Run date:** 2026-02-15
**Environment:** Preview container (fork agent). Backend on `http://localhost:8001`; tests invoked against `REACT_APP_BACKEND_URL=https://phi-safe-clinical-ui.preview.emergentagent.com`.

## Frontend — Jest (Clinical suite only)

Command: `cd /app/frontend && CI=true yarn test --testPathPattern='pages/clinical' --watchAll=false`

| Suite | Tests | Result |
|---|---:|:-:|
| `timelinePresetsSchema.test.js` | 21 | PASS |
| `dataQualityEngine.test.js` | 8 | PASS |
| `nextActionsEngine.test.js` | 13 | PASS |
| `PresetIconStrip.test.js` | 10 | PASS |
| `outcomeSeriesHelpers.test.js` | 25 | PASS |
| `useClinicalReturnState.test.js` | 12 | PASS |
| `workspaceModes.test.js` | 21 | PASS |
| `ClinicalTabV2.flagMatrix.test.js` | 7 | PASS |
| **Total** | **117** | **PASS (8/8 suites)** |

## Backend — Pytest (Clinical contract + telemetry + preferences)

Command: `cd /app/backend && python -m pytest tests/test_preferences_slice5.py tests/test_telemetry_phi_probe.py tests/test_telemetry_ui_action.py tests/test_next_action_telemetry.py tests/test_outcome_suggestion_telemetry.py tests/test_clinical_grouped_endpoints.py tests/test_grouped_timeline_filters.py tests/test_billing_readiness_aggregate.py tests/test_clinical_ui_defaults.py -q`

| File | Tests | Result | Purpose |
|---|---:|:-:|---|
| `test_preferences_slice5.py` | 45 | PASS | Slice 5C durable prefs — model-layer allow-list + PHI reject |
| `test_telemetry_phi_probe.py` | ~11 | PASS | PHI probe on `UIEventPayload` — extra=forbid across 13 PHI-like keys |
| `test_telemetry_ui_action.py` | ~26 | PASS | Care-status action shape — happy path + reject-unknown + PHI probe + auth |
| `test_next_action_telemetry.py` | 13 | PASS | Slice 1 next-action shape contract |
| `test_outcome_suggestion_telemetry.py` | 13 | PASS | Slice 3 outcome-suggestion shape contract |
| `test_clinical_grouped_endpoints.py` | ~10 | PASS | Encounters + timeline grouped schema, non-mutation, source-id presence |
| `test_grouped_timeline_filters.py` | ~15 | PASS | Slice 2 filter schema, PHI reject, stale-preset behavior |
| `test_billing_readiness_aggregate.py` | ~8 | PASS | Chart-wide billing-readiness aggregate contract + permission |
| `test_clinical_ui_defaults.py` | ~11 | PASS | Slice 5A/B/C durable prefs — HTTP-layer allow-list |
| **Total** | **152** | **PASS** | |
| `test_seed_large_chart.py` (added 2026-02-15) | 14 | PASS | Production guard + idempotency + relationship integrity + cleanup + requested event count + CLI parsing for the new large-chart fixture seeder |
| `test_run_clinical_perf.py` (added 2026-02-15) | 29 | PASS | Production guard (APP_ENV + confirm flag), build guard, percentile math (odd/even/single/empty/out-of-range), aggregate keys, summarise_runs (error rate, all-failed, empty, missing timing field, none timing field, percentiles), report generation (JSON + Markdown, never asserts pass without thresholds), CLI parsing (defaults, min runs, throttled, seed/cleanup, output-dir) |

Combined run (clinical contract + seeder): **166 passed / 166 total** in 115.7 s.
Standalone seeder run: **14 passed / 14 total** in 80.1 s.
Standalone clinical contract run: **152 passed / 152 total** in 78.7 s.

## Manual smoke verification of `scripts/seed_large_chart.py`

| Command | Timeline events observed | Result |
|---|---:|:-:|
| `--confirm-non-production --events 250` | 251 | PASS |
| `--confirm-non-production --events 500` | 500 | PASS |
| `--confirm-non-production --events 1000` | 1001 | PASS |
| `--confirm-non-production --cleanup` (after any seed) | 0 remaining | PASS |
| `APP_ENV=production --confirm-non-production` | refused | PASS (guard) |
| `APP_ENV=development` without `--confirm-non-production` | refused | PASS (guard) |

Fixture patient id (deterministic, non-PHI): `fixture-large-chart-patient-0001`. Printed to operator console only. No telemetry emission.

## ESLint (Clinical files)

Not re-run in this pass — no source code was modified. Last recorded green: 2026-02-15 (see `HANDOFF_SLICE6.md`).

## Notes on prerequisites

1. `libmagic` was missing in the container after a WatchFiles reload; reinstalled to restore backend startup. This is the recurring container-image issue documented in the handoff (`sudo apt-get install -y libmagic1 libmagic-dev libmagic-mgc`). No code change made.
2. Backend tests use `requests.Session()`, so they need the HTTPS `REACT_APP_BACKEND_URL` (cookies are `secure=True`). Localhost `http://` runs 401 on every authenticated request.
