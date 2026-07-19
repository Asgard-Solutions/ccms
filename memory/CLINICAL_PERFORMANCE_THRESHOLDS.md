# Clinical Performance Thresholds — Approval Record

**Purpose:** The single source of truth for approved Clinical performance thresholds. Populated only after platform reliability signs off on a harness run. Every downstream document (`PHASE3_PERFORMANCE_TEST_PLAN.md`, `CLINICAL_MONITORING_PLAN.md`, `CLINICAL_STAGED_ROLLOUT_PLAN.md`, `CLINICAL_ROLLOUT_CHECKLIST.md`, `CLINICAL_GA_READINESS.md`) references this file — do not re-declare thresholds elsewhere.

**Freeze rule:** Warning and rollback thresholds are **not** derived from the first measurement run. They require explicit approval with headroom sized to avoid noisy rollback decisions.

**Context rule:** A threshold approved on `desktop / normal / 500-event / Chromium` never silently governs mobile, throttled, or larger datasets. Each combination requires its own approval row.

**Status:** `AWAITING FIRST APPROVED RUN`. All threshold cells below are placeholders. Do not treat placeholder values as approved.

---

## Approval identity (fill in during sign-off)

| Field | Value |
|---|---|
| First-run harness invocation | `python -m scripts.run_clinical_perf --patient fixture-large-chart-patient-0001 --runs 20 --warmup 3 --profile desktop --network normal --confirm-non-production` |
| First-run raw JSON | `/app/memory/performance/PHASE3_PERFORMANCE_RAW_RESULTS.json` |
| First-run report | `/app/memory/performance/PHASE3_PERFORMANCE_REPORT.md` |
| Approval owner | ____________________ (Platform reliability lead) |
| Approval date | ____________________ |
| Approval channel | ____________________ (ticket / email / meeting record) |
| Superseded by | — |

## Approved combinations

Every row below is a **separate approval**. Do not extrapolate.

### Combination 1 — desktop / normal network / 500-event fixture

| Field | Value |
|---|---|
| Measurement profile | `desktop` (viewport 1920×900) |
| Dataset size | 500 timeline events (fixture-large-chart-patient-0001, seeded via `scripts/seed_large_chart.py --events 500`) |
| Network profile | `normal` (preview HTTPS, no CDP throttling) |
| Browser / device | Chromium via Playwright (production build, `yarn build`) |
| Approval owner | ____________________ |
| Approval date | ____________________ |

**Threshold rows** — populated only after the approving reviewer records the values. Each metric MUST have all three thresholds (release, warning, rollback) filled explicitly. Do NOT copy the first observed value into all three columns.

| Metric | Release budget (P95 unless noted) | Warning alert | Rollback trigger |
|---|---|---|---|
| Clinical initial render — `wall_clock_ms` P95 | ______ ms | ______ ms | ______ ms |
| Response end (HTML) — `response_end_ms` P95 | ______ ms | ______ ms | ______ ms |
| DOM content loaded — `dom_content_loaded_ms` P95 | ______ ms | ______ ms | ______ ms |
| Load event — `load_event_ms` P95 | ______ ms | ______ ms | ______ ms |
| Timeline load — `backend_timeline_ms` P95 | ______ ms | ______ ms | ______ ms |
| Encounters load — `backend_encounters_ms` P95 | ______ ms | ______ ms | ______ ms |
| Billing-readiness aggregate — `backend_billing_ms` P95 | ______ ms | ______ ms | ______ ms |
| Error rate (per-run failure) | ______ % | ______ % | ______ % (sustained) |

**Sustain window** (how long a warning/rollback threshold must hold before the alert fires or the rollback is triggered):

| Class | Window |
|---|---|
| Warning | ______ minutes (recommended default: 15) |
| Rollback | ______ minutes (recommended default: 30) |

### Combination 2 — desktop / throttled network / 500-event fixture

**Status:** not yet approved. Do not extrapolate from Combination 1.

| Field | Value |
|---|---|
| Measurement profile | `desktop` |
| Dataset size | 500 events |
| Network profile | `throttled` (750 kbps / 100 ms latency via Chromium CDP) |
| Browser / device | Chromium (production build) |
| Approval owner | (pending) |
| Approval date | (pending) |
| Notes | Represents the worst plausible clinic connectivity. Run this before pilot Stage 2 in any region where mobile-tether / satellite is common. |

*(Threshold rows identical structure to Combination 1; populate on approval.)*

### Combination 3 — mobile / normal network / 500-event fixture

**Status:** not yet approved. Do not extrapolate from Combination 1.

| Field | Value |
|---|---|
| Measurement profile | `mobile` (viewport 375×667) |
| Dataset size | 500 events |
| Network profile | `normal` |
| Browser / device | Chromium (production build) |
| Approval owner | (pending) |
| Approval date | (pending) |
| Notes | Required only if a supported clinical role uses the Clinical page on mobile. Not required for GA-desktop. |

### Combination 4 — stress: desktop / normal / 1000-event fixture

**Status:** not yet approved. Use only after Combination 1 is signed and the pilot exposes a real 1000-event chart.

| Field | Value |
|---|---|
| Measurement profile | `desktop` |
| Dataset size | 1000 events (`scripts/seed_large_chart.py --events 1000`) |
| Network profile | `normal` |
| Browser / device | Chromium (production build) |
| Approval owner | (pending) |
| Approval date | (pending) |

## What each threshold means

- **Release budget** — the value the harness run must clear (or come within a documented tolerance of) for the release manager to declare G2 closed as `COMPLETE — MEETS APPROVED BUDGET`. Not the same as the warning threshold; a release budget is a **snapshot** decision made once per release, whereas warning + rollback thresholds govern the **runtime** window.
- **Warning alert** — level at which monitoring pages the on-call. Must include enough headroom over the release budget that a normal week's variance does not fire the pager.
- **Rollback trigger** — sustained level at which the on-call is authorized to execute the R1 emergency rollback in `CLINICAL_ROLLBACK_RUNBOOK.md`. Must be strictly higher than the warning threshold and require the sustain window to hold.

**Ordering guarantee:** for every metric, `Release budget < Warning alert < Rollback trigger`. Any approval that violates this ordering is rejected on file.

## What this file does NOT authorize

- A threshold in one combination row does not authorize measurements in a different combination.
- A rollback trigger does not authorize rolling back a single-user report. Rollback authority stays with clinical platform lead / platform reliability lead per `CLINICAL_ROLLBACK_RUNBOOK.md`.
- A release-budget miss does not automatically block release. It escalates to product-owner discretion + a documented residual-risk statement.
- These thresholds do not replace the freeze document's change-control rules.

## Renewal

Every approval expires at the earlier of:
- 180 days from approval date, OR
- Any change to the harness measurement method, OR
- Any change to the Clinical redesign scope (post-freeze lift).

Renewal re-uses the same table structure; the previous row moves to a "History" section (below).

## History

*(Empty — no approvals recorded yet.)*
