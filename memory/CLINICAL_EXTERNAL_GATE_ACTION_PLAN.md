# Clinical Redesign — External Release-Gate Action Plan

**Generated:** 2026-02-15 (fork agent — external-gate preparation pass)
**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Freeze:** 2026-02-15 — see `/app/memory/CLINICAL_REDESIGN_FREEZE.md`.
**Change control:** Only verified defects may change code. This document changes no frozen contracts, telemetry, preferences, feature flags, permissions, masking, audit behavior, or signed-record behavior.

The redesign is code-complete and frozen. What remains is entirely human-driven work that cannot be executed from this container: real signatures, a production-build measurement pass, a production rollback rehearsal, an authorized screenshot capture, and a staged rollout decision. This plan tells each owner exactly what to do, in what order, with what evidence, and what blocks them.

---

## Gate summary

| Gate | Owner | Current status | Exact next action | Evidence required | Blocker |
|---|---|---|---|---|---|
| G1 | Clinical Operations | READY FOR CLINICAL AND OPERATIONS SIGN-OFF | Execute the 50-scenario UAT walkthrough (`PHASE3_UAT.md`), log pass/fail counts + any defects, capture signatures on `PHASE3_UAT_SIGNOFF.md`. | Filled `PHASE3_UAT_SIGNOFF.md` result table, filled defect log, four wet/electronic signatures (Clinical lead, Operations lead, Product owner, Platform reliability), selected release recommendation. | None from engineering side. Needs authorized environment + human signatories. |
| G2 | Platform Reliability | COMPLETE — MEASURED, BUDGET APPROVAL REQUIRED (fixture + harness shipped; production-build 500-event run + threshold approval outstanding) | Follow the operator command sequence in §G2 below. Reviewer must fill Release / Warning / Rollback per metric with explicit headroom, promote the draft, and pass the strict governance check. | Signed combination row in `CLINICAL_PERFORMANCE_THRESHOLDS.md`, promotion stamp on that row, raw JSON + report artifact in `/app/memory/performance/`, `check_perf_governance.py --strict` exit 0. | Requires production frontend build + fresh harness run on the 500-event fixture. Do NOT invent threshold numbers. |
| G3 | Clinical Platform Lead + Platform Reliability | READY FOR PRODUCTION WALK-THROUGH | Execute the R1 → R2 → R3 sequence in §G3 against production (or the approved production-like environment), capture time-to-rollback and time-to-restore, secure observer signatures. | Filled `CLINICAL_ROLLBACK_REHEARSAL.md` including timings + observer sign-off, incident ticket references, CDN cache invalidation evidence. | Requires production access + rollback authority + change-control approval. |
| G5 | Product / Design / Release Evidence Owner | READY FOR SCREENSHOT CAPTURE (3 proof-of-life inline shots done; full 25-shot set outstanding) | Run the capture checklist in §G5 in an authorized staging tenant with the Riverbend seed. Persist to `/app/memory/screenshots/release/`. Reviewer initials in `screenshots/release/APPROVAL.md`. | 25 filenamed screenshots per index, PHI grep clean, reviewer initials, updated `CLINICAL_RELEASE_NOTES.md` with the final image links. | None from engineering side. Needs authorized capture pass. |
| G6 | Release Manager | READY FOR AUTHORIZED STAGED ROLLOUT — Stage 1 **blocked** until G1, G2, and G3 are closed. | Assemble Stage 1 readiness packet (§G6) once G1/G2/G3 close. Confirm cohort, owners, backups, monitoring, stop conditions, communications; open the internal Slack channel; send the kickoff email. | Signed Stage 1 readiness packet, monitoring dashboards live, on-call schedule confirmed, rollback path re-verified against production settings (dry-run). | Blocked on G1 + G2 + G3. |

**G4 (Contract freeze)** is `COMPLETE` and is not in this plan; it stays closed unless a verified defect requires reopening.

---

## G1 — Prepare stakeholder UAT sign-off

**Owner:** Clinical Operations.
**Backup owner:** Clinical Platform Lead.
**Ambient reference:** `/app/memory/PHASE3_UAT.md`, `/app/memory/PHASE3_UAT_SIGNOFF.md`, `/app/memory/PHASE3_UAT_EVIDENCE_INDEX.md`, `/app/memory/PHASE3_UAT_DEFECTS.md`.

### Preflight confirmations (engineering already verified)

- All 50 scenarios are listed in `PHASE3_UAT.md` (scenarios 1–50, sequential, no gaps).
- Evidence links are present for all 50 in `PHASE3_UAT_EVIDENCE_INDEX.md` (4 ✅ captured, 22 📋 automated-covered, 20 🎬 to-capture, 4 ⚙️ fixture-required).
- Sign-off fields exist on `PHASE3_UAT_SIGNOFF.md` for Clinical lead, Operations lead, Product owner, and Platform reliability. **No signatures are pre-filled.**
- Defect log (`PHASE3_UAT_DEFECTS.md`) currently shows no in-scope Blocker/Critical defects. Six items are logged as accepted known limitations (KL-1..KL-6) and do not count against the pass rate.

### Checklist for Clinical Operations owner

1. Confirm the persona credentials in `/app/memory/test_credentials.md` still resolve against the target environment. If not, halt and request refresh.
2. Assign QA testers to the 50 scenarios. Every 🎬 TO-CAPTURE scenario needs a live walkthrough; every ⚙️ fixture-required scenario needs the documented fixture reseed first.
3. For each scenario:
   - Reproduce twice on the fresh seed.
   - Record `Pass` / `Fail` / `Blocked`.
   - On failure, classify severity (Blocker/Critical/Major/Minor) AND scope (In frozen scope / Out of scope / Environmental / Test-data issue) per `PHASE3_UAT_DEFECTS.md` §Defect handling protocol.
   - In-scope Blocker/Critical → open a defect ticket referencing `CLINICAL_REDESIGN_FREEZE.md`, fix, retest.
   - Any other classification → log per protocol; do not block sign-off.
4. Fill the result summary table on `PHASE3_UAT_SIGNOFF.md` (`Pass / Fail / Blocked / Retested / Accepted known limitations`).
5. Confirm the residual-risk statement in `PHASE3_UAT_SIGNOFF.md` is reviewed by the clinical lead AND the operations lead.
6. Collect four signatures on `PHASE3_UAT_SIGNOFF.md`: Clinical lead, Operations lead, Product owner, Platform reliability. Wet or electronic; do not fabricate.
7. Product owner selects one release recommendation checkbox.
8. Archive `PHASE3_UAT_SIGNOFF.md` alongside the release ticket.
9. Update `CLINICAL_RELEASE_GATE_STATUS.md` G1 row: `COMPLETE — signed <YYYY-MM-DD>` with a link to the signed doc.

### Missing evidence — clearly identified

Twenty scenarios are 🎬 TO-CAPTURE and four are ⚙️ fixture-required in `PHASE3_UAT_EVIDENCE_INDEX.md`. None block G1 sign-off on their own — the executed live walkthrough IS the evidence. If any scenario cannot be reached (e.g., no fixture for scenario 44 large history), reseed with `python -m scripts.seed_large_chart --confirm-non-production --events 500` and rerun. Only after two failed reseeds should the scenario be marked `Blocked` and escalated.

### Do not mark G1 complete until

- Every scenario is `Pass` / `Fail` / `Blocked` (no blanks).
- No open in-scope Blocker or Critical.
- All four signatures captured on `PHASE3_UAT_SIGNOFF.md`.
- Product owner has selected a release recommendation.

---

## G2 — Prepare production performance run and threshold approval

**Owner:** Platform Reliability.
**Backup owner:** Clinical Platform Lead.
**Ambient reference:** `/app/backend/scripts/{seed_large_chart,run_clinical_perf,promote_perf_threshold,check_perf_governance}.py`, `/app/memory/PHASE3_PERFORMANCE_TEST_PLAN.md`, `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md`, `/app/memory/CLINICAL_PERFORMANCE_THRESHOLD_PROMOTION.md`.

### Operator command sequence — DO NOT INVENT THRESHOLD VALUES

Run in a non-production environment. Every step assumes `APP_ENV != production` (the scripts refuse otherwise). Do not skip the warm-up. Do not copy measured values into threshold columns.

**Step 1 — Build production frontend.**

```
cd /app/frontend && yarn build
```

Expected: `/app/frontend/build/index.html` present. `run_clinical_perf.py` refuses to run without it.

**Step 2 — Seed or verify the 500-event fixture.**

```
cd /app/backend && APP_ENV=development python -m scripts.seed_large_chart \
  --confirm-non-production --events 500
```

Expected: `fixture-large-chart-patient-0001` printed to operator console only. Idempotent — safe to re-run.

**Step 3 — Run 20 measured iterations after 3 warm-ups.**

```
cd /app/backend && APP_ENV=development python -m scripts.run_clinical_perf \
  --patient fixture-large-chart-patient-0001 \
  --seed-fixture --fixture-events 500 \
  --runs 20 --warmup 3 --profile desktop --network normal \
  --confirm-non-production
```

Expected artefacts (default `--output-dir /app/memory/performance`):
- `PHASE3_PERFORMANCE_RAW_RESULTS.json`
- `PHASE3_PERFORMANCE_REPORT.md` (labelled *Measured — threshold approval required*).

If fewer than 20 successful runs complete, or any endpoint returns ≥ 500, exit code 2 fires. Do not promote a run that exited non-zero.

**Step 4 — Append a threshold draft (opt-in).**

```
cd /app/backend && APP_ENV=development python -m scripts.run_clinical_perf \
  --patient fixture-large-chart-patient-0001 \
  --runs 20 --warmup 3 --profile desktop --network normal \
  --write-threshold-draft \
  --confirm-non-production
```

Expected: a new `perf-draft` block appended to `/app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md`. Measured P50/P75/P95 land in the evidence table; every Release / Warning / Rollback cell reads `REVIEW REQUIRED`.

**Step 5 — Platform Reliability reviewer fills the draft.**

Edit the appended block IN PLACE. For every metric:
- Set Release budget explicitly (do NOT copy the observed P95 verbatim; anchor to user tolerance).
- Set Warning alert strictly higher, with headroom to absorb normal variance.
- Set Rollback trigger strictly higher than Warning, with a sustain window.
- Fill Approval owner (name), Approval date (YYYY-MM-DD), Rationale (single sentence anchoring headroom to a real signal).
- Fill sustain windows (recommended defaults: Warning 15 min, Rollback 30 min).
- Never leave any `REVIEW REQUIRED` cell.

**Step 6 — Dry-run promotion.**

```
cd /app/backend && APP_ENV=development python -m scripts.promote_perf_threshold \
  --run-id <run-id-from-draft-marker> \
  --thresholds-file /app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md \
  --approved-by "<Platform Reliability Lead full name>" \
  --approval-date <YYYY-MM-DD> \
  --rationale "<one-line rationale>" \
  --dry-run --confirm-promotion
```

Expected: unified diff preview, exit 0. If the dry-run rejects, the reviewer must fix the draft (missing field, wrong ordering, mixed units) and re-dry-run.

**Step 7 — Promote the approved row.**

```
cd /app/backend && APP_ENV=development python -m scripts.promote_perf_threshold \
  --run-id <run-id-from-draft-marker> \
  --thresholds-file /app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md \
  --approved-by "<Platform Reliability Lead full name>" \
  --approval-date <YYYY-MM-DD> \
  --rationale "<one-line rationale>" \
  --confirm-promotion
```

Expected: marker flips `perf-draft` → `perf-approved`; status flips `AWAITING SIGN-OFF` → `APPROVED`; immutable promotion stamp appended; `.backup-<UTC>` copy written next to the thresholds file.

**Step 8 — Governance check (strict mode).**

```
cd /app/backend && APP_ENV=development python -m scripts.check_perf_governance \
  --thresholds-file /app/memory/CLINICAL_PERFORMANCE_THRESHOLDS.md \
  --strict \
  --json-output /app/memory/performance/governance-check.json
```

Expected: exit 0. Any structural violation, expired approval, duplicated downstream number, or broken citation exits 2.

**Step 9 — Cross-document promotion (manual).** Per `CLINICAL_PERFORMANCE_THRESHOLD_PROMOTION.md` §Step 4, replace the pending references in `CLINICAL_MONITORING_PLAN.md`, `PHASE3_PERFORMANCE_TEST_PLAN.md`, `PHASE3_PERFORMANCE_REPORT.md`, `CLINICAL_STAGED_ROLLOUT_PLAN.md`, `CLINICAL_ROLLOUT_CHECKLIST.md`, and `CLINICAL_GA_READINESS.md`. **Only reference — never duplicate — the approved values.**

**Step 10 — Cleanup fixture (optional).**

```
cd /app/backend && APP_ENV=development python -m scripts.seed_large_chart \
  --confirm-non-production --cleanup
```

### Do not mark G2 complete until

- The production-build measurement pass exists (raw JSON + report on disk).
- Platform reliability has approved the thresholds (row status `APPROVED`, promotion stamp present).
- The approved row has been promoted via `promote_perf_threshold.py --confirm-promotion` (never by manual edit alone).
- `check_perf_governance.py --strict` exits 0.
- Cross-document references have been updated (§Step 9).

Do not invent threshold values. Do not copy P95 into all three tiers. Do not extrapolate a desktop/normal/500 approval to throttled, mobile, or 1000-event conditions.

---

## G3 — Prepare production rollback rehearsal

**Owner:** Clinical Platform Lead + Platform Reliability.
**Ambient reference:** `/app/memory/CLINICAL_ROLLBACK_RUNBOOK.md`, `/app/memory/CLINICAL_ROLLBACK_MATRIX.md`, `/app/memory/CLINICAL_ROLLBACK_REHEARSAL.md`.

### Operator checklist

1. Notify `#clinical-oncall` + on-call Clinical Platform Lead + on-call Platform Reliability + change-control approver. Post a scheduled rehearsal window.
2. Confirm you hold approved rollback authority (Clinical Platform Lead OR Platform Reliability Lead).
3. Snapshot current env-var state of every clinical flag in the target environment. Attach to the rehearsal ticket.
4. Confirm the legacy `ClinicalTab` fallback is still mounted (verified 2026-02-15; re-verify in target env).
5. Pre-open three demo patient charts as admin / doctor / staff to establish a pre-rehearsal baseline.
6. Execute scenarios in the sequence below, capturing timings after each step:
   - **R3 (per-user)** on one internal test account: browser console `localStorage.setItem('ccms.flags.clinicalRedesign','off'); location.reload();`. Verify legacy layout. Reverse. Verify redesign layout restored.
   - **R2 (selective slice)** on the target env: set `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5=off`, rebuild, redeploy, invalidate CDN. Verify workspace switcher hidden. Reverse.
   - **R1 (emergency full rollback)** on the target env: set `REACT_APP_CLINICAL_REDESIGN=off`, rebuild, redeploy, invalidate CDN. Verify legacy `[data-testid=patient-clinical-tab]` renders across three persona sessions. Reverse.
7. For every scenario capture time-to-rollback (moment of env change → moment legacy layout observed) AND time-to-restore (moment of env restore → moment redesign layout observed).
8. Confirm no patient data / signed record / preference / audit row was mutated (spot-check `updated_at` on 3 patient records before/after).
9. Confirm the `ccms-flag-change` cross-tab listener behaves (open the same chart in a second tab, flip the per-user override, verify the second tab reacts on next render).
10. Open the standard incident ticket format, referencing the rehearsal timestamp.

### Observer checklist

1. Independently confirm every timing captured by the operator.
2. Confirm the announced blast radius matches the actual flag state at each step.
3. Confirm the CDN invalidation propagated within the documented 5–15 min window.
4. Confirm no rollback step touched patient data, signed records, preferences, or audit rows.
5. Confirm the rehearsal did not extend beyond the announced window.
6. Sign the observer row in `CLINICAL_ROLLBACK_REHEARSAL.md`.

### Exact flag sequence

For each of R3 → R2 → R1: flip → verify (using `data-testid` signals in §Rollback matrix rows 2, 3, 8) → reverse → verify restore. R1 must be executed last because it clears the entire redesign surface.

### Expected result — Rollback Matrix reference

| Rehearsal step | Flag state after step | Expected DOM signal | Registry-test cross-ref |
|---|---|---|---|
| Baseline (before R3) | All on | `[data-testid=patient-clinical-tab-v2]`, workspace switcher, Next Actions, Data Quality | Matrix row 1 |
| R3 applied | Per-user parent off; env defaults still on | Legacy `[data-testid=patient-clinical-tab]` for that user only | Matrix row 2 |
| R3 reversed | Baseline | Restored | — |
| R2 applied | `REACT_APP_CLINICAL_REDESIGN_PHASE3_SLICE5=off` | Workspace switcher absent, default NAV_ITEMS order, redesign otherwise intact | Matrix row 8 |
| R2 reversed | Baseline | Restored | — |
| R1 applied | `REACT_APP_CLINICAL_REDESIGN=off` | Full legacy fallback across admin / doctor / staff | Matrix row 2 + row 20 |
| R1 reversed | Baseline | Redesign restored across the same three personas | — |

### Time-to-rollback and time-to-restore fields (record for each scenario)

| Scenario | Time-to-rollback | Time-to-restore | Verified by observer |
|---|---:|---:|:-:|
| R3 (per-user) | ____ s | ____ s | ⬜ |
| R2 (selective slice) | ____ min | ____ min | ⬜ |
| R1 (emergency full) | ____ min | ____ min | ⬜ |

### Evidence capture requirements

- Screenshot (or Playwright report) of `data-testid` presence for each rehearsal step (7 shots minimum). Fictional data only.
- CDN cache-invalidation confirmation IDs.
- Rebuild pipeline run IDs (env-var diff attached).
- Pre/post `updated_at` spot-checks for three patient records.
- Signed observer row in `CLINICAL_ROLLBACK_REHEARSAL.md`.
- Signed operator row (rehearsal owner).

### Escalation procedure

Halt the rehearsal and page Clinical Platform Lead + Platform Reliability + Compliance immediately if any of:

- Blank Clinical page after any flag flip.
- Users logged out (identity oncall).
- Audit-log emission failed during rollback (compliance oncall).
- Data-mutation suspicion (compliance + platform reliability).
- CDN invalidation exceeds 30 min without restore.

### Do not mark G3 complete until

- All three rollback scenarios executed on production (or approved production-like environment).
- Every timing recorded and independently verified by the observer.
- Observer sign-off captured on `CLINICAL_ROLLBACK_REHEARSAL.md`.
- Rehearsal ticket closed with root cause of any deviation + retest.
- No production change is executed without documented change-control authorization.

---

## G5 — Prepare authorized screenshot capture

**Owner:** Product / Design / Release Evidence Owner.
**Backup owner:** Clinical Platform Lead.
**Ambient reference:** `/app/memory/CLINICAL_RELEASE_SCREENSHOT_INDEX.md`, `/app/memory/CLINICAL_RELEASE_NOTES.md`, `/app/memory/CLINICAL_SUPPORT_BRIEF.md`.

### 25-shot capture checklist

Persist every image to `/app/memory/screenshots/release/<row>_<persona>_<viewport>_<theme>_<flag>.jpg`. All fixtures use the Riverbend Chiropractic & Wellness demo tenant. No production PHI.

| # | Shot | Required role | Fixture | Viewport | Theme | Flag state | Masking state | Filename | PHI redaction |
|:-:|---|---|---|---|---|---|---|---|---|
| 1 | Workspace mode `general` — full desktop | Admin (Ava Bennett) | Riverbend demo (masked) | 1920×900 | dark | all on | masked | `1_general_admin_1920x900_dark_all-on.jpg` | Fictional; verify no email/phone/DOB visible |
| 2 | Workspace mode `provider` — full desktop | Doctor (Noah Carter) | Same | 1920×900 | dark | all on | masked | `2_provider_doctor_1920x900_dark_all-on.jpg` | Same |
| 3 | Workspace mode `front_desk` — full desktop | Staff (Mia Ramirez) | Same | 1920×900 | dark | all on | masked | `3_frontdesk_staff_1920x900_dark_all-on.jpg` | Same |
| 4 | Workspace mode `billing` — full desktop | Staff (Mia Ramirez) | Same | 1920×900 | dark | all on | masked | `4_billing_staff_1920x900_dark_all-on.jpg` | Same |
| 5 | Workspace mode `administrator` — full desktop | Admin | Same | 1920×900 | dark | all on | masked | `5_administrator_admin_1920x900_dark_all-on.jpg` | Same |
| 6 | Top orientation strip (sticky header) | Admin | Same | 1920×400 crop | dark | all on | masked | `6_orientation_admin_1920x400_dark_all-on.jpg` | Same |
| 7 | Current Care Status panel | Admin | Same | 1920×500 crop | dark | all on | masked | `7_care-status_admin_1920x500_dark_all-on.jpg` | Same |
| 8 | Next Actions panel | Admin | Same | 1920×400 crop | dark | all on | masked | `8_next-actions_admin_1920x400_dark_all-on.jpg` | Same |
| 9 | Workspace-mode switcher open | Admin | Same | 800×300 crop | dark | all on | masked | `9_switcher_admin_800x300_dark_all-on.jpg` | Same |
| 10 | Configurable summary drawer open | Admin | Same | 1920×500 crop | dark | all on | masked | `10_summary-config_admin_1920x500_dark_all-on.jpg` | Same |
| 11 | Timeline filters with saved preset icon strip | Admin | Same | 1920×500 crop | dark | all on | masked | `11_timeline-filters_admin_1920x500_dark_all-on.jpg` | Same |
| 12 | Grouped encounters (needs action) | Admin | Same | 1920×500 crop | dark | all on | masked | `12_encounters_admin_1920x500_dark_all-on.jpg` | Same |
| 13 | Data Quality panel | Admin | Same | 1920×500 crop | dark | all on | masked | `13_data-quality_admin_1920x500_dark_all-on.jpg` | Same |
| 14 | Imaging with complete metadata | Admin | Fixture: 2+ media rows | 1920×500 crop | dark | all on | masked | `14_imaging_admin_1920x500_dark_all-on.jpg` | Same |
| 15 | Outcome snapshot card | Admin | Fixture: NDI baseline+latest | 1920×500 crop | dark | all on | masked | `15_outcome-snapshot_admin_1920x500_dark_all-on.jpg` | Same |
| 16 | Outcome trend chart + accessible table toggle | Admin | Same | 1920×500 crop | dark | all on | masked | `16_outcome-trend_admin_1920x500_dark_all-on.jpg` | Same |
| 17 | Positive red-flag state | Admin | Fixture: `history.red_flag_screening.fever=true` | 1920×600 crop | dark | all on | masked | `17_red-flag_admin_1920x600_dark_all-on.jpg` | Same |
| 18 | Billing warning tone | Admin | Fixture: encounter with warning | 1920×400 crop | dark | all on | masked | `18_billing-warning_admin_1920x400_dark_all-on.jpg` | Same |
| 19 | Billing blocked tone | Admin | Fixture: blocked encounter | 1920×400 crop | dark | all on | masked | `19_billing-blocked_admin_1920x400_dark_all-on.jpg` | Same |
| 20 | Re-exam overdue banner | Admin | Fixture: re-exam past due | 1920×300 crop | dark | all on | masked | `20_reexam-overdue_admin_1920x300_dark_all-on.jpg` | Same |
| 21 | Small-screen (mobile) layout | Admin | Same | 375×667 | dark | all on | masked | `21_mobile_admin_375x667_dark_all-on.jpg` | Same |
| 22 | Tablet layout | Admin | Same | 900×1200 | dark | all on | masked | `22_tablet_admin_900x1200_dark_all-on.jpg` | Same |
| 23 | Keyboard focus on section nav | Admin | Same | 1920×900 | dark | all on | masked | `23_kb-focus_admin_1920x900_dark_all-on.jpg` | Same |
| 24 | Legacy fallback (parent flag off) | Admin | Same | 1920×900 | dark | `clinicalRedesign=off` | masked | `24_legacy_admin_1920x900_dark_parent-off.jpg` | Same |
| 25 | Slice 5 disabled (workspace switcher absent) | Admin | Same | 1920×900 | dark | `clinicalRedesignPhase3Slice5=off` | masked | `25_slice5-off_admin_1920x900_dark_slice5-off.jpg` | Same |

**PHI-redaction requirements for every shot:**

- Fictional identities only (Riverbend personas). No production PHI.
- Blur or crop any address, phone, email, or DOB even in fictional shots.
- Preview watermark ("Made with Emergent") must be documented as environment artifact, not published in release notes.
- Filename must not contain a patient identifier.
- Reviewer initials required on every shot in `/app/memory/screenshots/release/APPROVAL.md`.
- Final PHI check: `grep -RIE '(SSN|@[a-z]+\.(com|app|org)|[0-9]{3}-[0-9]{3}-[0-9]{4})' /app/memory/screenshots/release/` returns only fictional values (or nothing).

### Do not mark G5 complete until

- All 25 shots exist under `/app/memory/screenshots/release/`.
- `APPROVAL.md` carries reviewer initials for every shot.
- PHI grep is clean.
- `CLINICAL_RELEASE_NOTES.md` links have been updated to point at the persisted images (once distribution channel is chosen).

Do not fabricate screenshots. Do not reuse production tenant data.

---

## G6 — Stage 1 readiness packet

**Owner:** Release Manager.
**Backup owner:** Clinical Platform Lead.
**Ambient reference:** `/app/memory/CLINICAL_STAGED_ROLLOUT_PLAN.md`, `/app/memory/CLINICAL_ROLLOUT_CHECKLIST.md`, `/app/memory/CLINICAL_MONITORING_PLAN.md`, `/app/memory/CLINICAL_GA_READINESS.md`, `/app/memory/CLINICAL_INCIDENT_RUNBOOK.md`.

**G6 Stage 1 is blocked until G1, G2, and G3 are closed. Do not open the internal channel or send the kickoff email until all three gates have signed evidence on file.**

### Required closed gates

- G1 signed (four signatures on `PHASE3_UAT_SIGNOFF.md`).
- G2 promoted (`APPROVED` row in `CLINICAL_PERFORMANCE_THRESHOLDS.md`, strict governance check exit 0).
- G3 rehearsed (observer signature on `CLINICAL_ROLLBACK_REHEARSAL.md`).

### Internal cohort definition

10–15 internal accounts on the staging tenant, drawn from Product, QA, Clinical Informatics, Support, and Operations. Each account is pinned to a real clinical role that maps to at least one workspace mode. Cohort roster is stored with the release ticket, not in this document.

### Owners and backups

| Role | Owner | Backup |
|---|---|---|
| Release manager (incident commander) | (name) | Clinical Platform Lead |
| Clinical Platform Lead | (name) | Platform Reliability |
| Platform Reliability on-call | (name) | Clinical Platform Lead |
| Support lead | (name) | On-call Support |
| Compliance officer | (name) | Legal (as needed) |

### Enabled flags

All eight clinical flags `on`:

- `clinicalRedesign`
- `clinicalRedesignPhase2WaveA`
- `clinicalRedesignPhase2WaveB`
- `clinicalRedesignPhase3`
- `clinicalRedesignPhase3Slice3`
- `clinicalRedesignPhase3Slice4`
- `clinicalRedesignPhase3Slice5`
- `clinicalRedesignPhase3Slice6`

### Monitoring signals

Live dashboards for signals 1–4 (paging) and 5–6 (routed) per `CLINICAL_MONITORING_PLAN.md`. Baseline metrics captured 14 days pre-Stage 1 and recorded in `CLINICAL_GA_READINESS.md`.

### Stop conditions

Per `CLINICAL_STAGED_ROLLOUT_PLAN.md` §Rollout stop conditions. Any Blocker → R1 emergency rollback. Sustained error rate or P95 breach against the approved combination row in `CLINICAL_PERFORMANCE_THRESHOLDS.md` → same.

### Rollback authority

Clinical Platform Lead OR Platform Reliability Lead. Documented in `CLINICAL_ROLLBACK_RUNBOOK.md` §Ownership.

### Support coverage

Business hours + on-call Slack (`#clinical-oncall`). Support brief (`CLINICAL_SUPPORT_BRIEF.md`) distributed to every support tier before the kickoff email.

### Communication template

Kickoff (Day 0):
```
Subject: Clinical redesign — Stage 1 (internal) starting <YYYY-MM-DD>

Team,

Beginning today we are enabling the Clinical redesign for the internal
cohort (~10–15 accounts on staging). Full rollout stops at Stage 1
until we clear the exit criteria: 5 business days without a Blocker
or Critical, internal survey ≥ 8/10.

If you observe anything unexpected on the Clinical tab, post in
#clinical-redesign-internal with tenant + role + section slug (no
patient IDs, no PHI). Rollback is armed.

Release manager: <name>
Clinical platform lead: <name>
Platform reliability on-call: <name>
Support lead: <name>
```

Daily standup (Days 1–5): shortest possible standup covering signals 1–4, open tickets, decision to continue / hold / rollback.

Exit (Day 5+): summary email covering signal deltas vs baseline, survey score, list of accepted known limitations, decision on Stage 2 selection.

### Start checklist

- [ ] G1 signed + linked to release ticket
- [ ] G2 approved combination row promoted + governance strict check exit 0
- [ ] G3 observer sign-off captured
- [ ] Cohort roster confirmed + pinned to release ticket
- [ ] Owners + backups confirmed with each person
- [ ] Monitoring dashboards live + baseline captured (14 days)
- [ ] Rollback path re-verified against production settings (dry-run — no user impact)
- [ ] Support brief distributed
- [ ] Slack channel `#clinical-redesign-internal` created
- [ ] Kickoff email sent
- [ ] Daily standup scheduled

### Exit checklist

- [ ] 5 consecutive business days with no Blocker / Critical
- [ ] Internal survey ≥ 8/10
- [ ] All in-scope defects closed (retested + regression added)
- [ ] Signals 1–4 within baseline envelope
- [ ] Stage 2 pilot clinic selected + agreed
- [ ] Pilot feedback form ready (`CLINICAL_PILOT_FEEDBACK_FORM.md`)
- [ ] Stage 2 approval recorded (Product owner + Clinical Platform Lead)

### Do not start Stage 1 until

Every item in the Start checklist is ticked with a real timestamp. Do not proceed to Stage 2 without at least one 200+ event chart measured under the approved combination row in `CLINICAL_PERFORMANCE_THRESHOLDS.md`.

---

## Change-control commitment

This action plan is preparatory documentation. It changes no code, no frozen contract, no telemetry surface, no preference schema, no feature flag, no permission, no masking behavior, no audit behavior, no signed-record behavior. Any future modification made to close a gate must reference `CLINICAL_REDESIGN_FREEZE.md` and demonstrate a verified defect.

## Cross-references

- `/app/memory/CLINICAL_RELEASE_GATE_STATUS.md` — current status table (updated to reference this plan).
- `/app/memory/CLINICAL_REDESIGN_FREEZE.md` — freeze scope + change-control rules.
- `/app/memory/CLINICAL_ROLLBACK_RUNBOOK.md` — R1/R2/R3 procedures for G3 and any Stage 1 incident.
- `/app/memory/TICKET_REMOVE_PERF_EXPORTS.md` — post-freeze maintenance (blocked; not part of the release gates).
