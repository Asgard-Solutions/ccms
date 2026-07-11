# CCMS — PRD (concise)

**Note:** The full historical PRD lives at `/app/memory/PRD.md`. This shorter file is the release-agent handoff snapshot as of **2026-02-15** and captures the current state of the frozen Clinical redesign plus the remaining work.

## Current state

The Patient Profile > Clinical page is **FROZEN** (2026-02-15). The redesign shipped Phases 1 + 2 (Waves A/B) + Phase 3 (Slices 1–6) behind eight nested feature flags. Automated tests are green (frontend 117/117, backend clinical 152/152). Six release gates (G1–G6) are being closed out — see `/app/memory/CLINICAL_RELEASE_GATE_STATUS.md`.

## What is complete

- Frozen Clinical UI (see `CLINICAL_REDESIGN_FREEZE.md`).
- All 17 contract surfaces documented + snapshot-registered (see `CLINICAL_CONTRACT_FREEZE.md`).
- Rollback runbook + matrix + rehearsal (see `CLINICAL_ROLLBACK_*.md`).
- Release notes + support brief + known limitations (see `CLINICAL_RELEASE_NOTES.md`).
- Staged rollout plan + monitoring plan + incident runbook + pilot feedback form + GA readiness (see `CLINICAL_STAGED_ROLLOUT_PLAN.md` etc.).
- Performance report — measured; thresholds pending approval.

## What is next (release-gate work remaining)

1. Human sign-offs on `PHASE3_UAT_SIGNOFF.md`.
2. Platform-reliability approval of proposed performance thresholds.
3. Production rollback walk-through execution.
4. Full 25-shot screenshot capture in an authorized environment.
5. Stage 1 (internal) execution.

## What is deferred (do not act on during freeze)

- Diagnosis "Set inactive" state.
- Case-type outcome mappings.
- Chart-at-a-glance print sheet.
- My Worklist / Today's Chart Preview dashboard widgets.
- Billing digest.
- Clinic-wide data-quality aggregate.
- Change Healthcare / Optum production transport.
- AI cost estimator.
- Admin-facing feature-flag panel.
- Application-wide theme overhaul.
- `SectionErrorBoundary` around AI Scribe / Billing Ledger.

## Non-negotiables

- Clinical redesign remains FROZEN. Verified defects only.
- No new features, roles, telemetry categories, or preference fields.
- Legacy `ClinicalTab` remains mounted at all times.
- `extra=forbid` on every telemetry + preference contract.
- No PHI in telemetry, preferences, screenshots, or monitoring signals.

## Reference

- Full PRD history: `/app/memory/PRD.md`
- CHANGELOG: `/app/memory/CHANGELOG.md`
- Roadmap: `/app/memory/ROADMAP.md`
