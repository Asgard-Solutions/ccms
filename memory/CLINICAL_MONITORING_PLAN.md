# Clinical Redesign — Monitoring Plan

**Purpose:** Which signals to watch during and after rollout, without exposing PHI.

## Approved signals (PHI-safe)

All signals are counters or rates derived from existing telemetry / audit / server logs. **None** of the following include patient IDs, record IDs, names, diagnosis codes, dates of service, outcome scores, search text, or URLs containing identifiers.

| # | Signal | Source | Aggregation | Alerting |
|:-:|---|---|---|---|
| 1 | Clinical page load failures | Frontend `clinical.section.load_failed` telemetry | Rate per hour per tenant | Alert if > 10 per hour or > 3× baseline |
| 2 | Section-error-boundary activation | Frontend `clinical.section.load_failed` telemetry filtered by section | Count per section per hour | Alert if any section > 5 per hour |
| 3 | API error rate | Backend request logs (status ≥ 500) on `/api/patients/*/clinical/*` | Rate per minute | Alert if > 1% for 5 min |
| 4 | Preference-save failure rate | Backend request logs on `PATCH /api/auth/me/preferences` | Rate per minute | Alert if > 0.5% for 10 min |
| 5 | Timeline-load latency | Backend request logs on `/clinical/timeline/grouped` | P50 / P95 per minute | Alert if P95 > 900 ms for 10 min (pending threshold approval) |
| 6 | Outcome-render failures | Frontend `clinical.section.load_failed{section=outcomes}` | Count per hour | Alert if > 3 per hour |
| 7 | Feature-flag fallback activations | Legacy layout `clinical.layout.activated{layout=v1}` telemetry | Count per hour | Alert on any spike vs baseline |
| 8 | Legacy-layout activation rate | Same as above, but as a % of total | Rate per hour | Track only (not alertable) |
| 9 | Next Action CTA attempts | Telemetry `clinical_next_action_interaction` | Count per action_id per day | Track only |
| 10 | Billing-review CTA attempts | Telemetry `clinical_care_status_action_selected{action_slug=review-billing-issues}` | Count per tenant per day | Track only |
| 11 | Role-mode changes | Preference PATCH audit — `default_workspace_mode` field only | Count per role per day | Track only |
| 12 | UAT defect recurrence | Defect tracker (external) | Count per week | Track only |

## Explicitly excluded from monitoring (PHI or PHI-adjacent)

- Patient IDs
- Record IDs (encounter, note, appointment, claim, plan, outcome entry)
- Names
- Diagnosis codes (ICD-10, CPT, HCPCS)
- Dates of service
- Outcome scores or clinical response values
- Search text
- URLs containing identifiers
- Free-text notes / addenda
- Provider free-text names (only structured `provider_id` UUIDs are used, and never in PHI-safe telemetry)

## Dashboards

- **Rollout health dashboard** — signals 1–4 side-by-side. Refresh 1 min.
- **Feature-flag rollout dashboard** — signals 7–8. Refresh 5 min.
- **Product engagement dashboard** — signals 9–11. Refresh 1 hour.

## Alert routing

- Signals 1–4 → paging channel `#clinical-oncall`.
- Signals 5–6 → clinical platform on-call + platform reliability.
- Signals 7–8 → clinical platform lead (email digest).
- Signals 9–12 → weekly product report (no paging).

## Baseline capture

Before Stage 1 starts:

1. Capture 14 days of baseline metrics with the redesign flag `on` (current state).
2. Compute per-signal median + P95.
3. Document baseline values in `CLINICAL_GA_READINESS.md` when approved.

## Stop-condition thresholds (require approval)

| Signal | Proposed stop threshold | Approved? |
|---|---|:-:|
| Clinical page load failures | > 20 / hour sustained 15 min | Pending |
| Section-error-boundary activation | > 10 / hour on any single section sustained 15 min | Pending |
| API error rate | > 2% sustained 10 min | Pending |
| Preference-save failure rate | > 1% sustained 30 min | Pending |
| Timeline P95 | > 3000 ms sustained 30 min | Pending |

Awaiting platform-reliability sign-off. Until approved, the release-gate G6 remains `READY FOR AUTHORIZED STAGED ROLLOUT`, not `COMPLETE`.

## Data retention

- Telemetry rows: existing 90-day retention policy (see `PRIVACY_AND_RETENTION.md`).
- Aggregated monitoring counters: 180 days.
- Alert history: 1 year for compliance evidence.

## Privacy validation

- Every signal was reviewed against the PHI probe in `test_telemetry_phi_probe.py` — no signal originates from a payload field that could smuggle PHI.
- Every backend request log excludes request/response bodies by policy.
