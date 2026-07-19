# Phase 3 UAT — Defect log

**Redesign scope:** Patient Profile > Clinical (Phases 1 + 2 Waves A/B + Phase 3 Slices 1–6).
**Log opened:** 2026-02-15 (fork agent release-gate closeout).

## In-scope defects (verified during the release-gate pass)

None. No new in-scope defects were verified during this release-gate closeout. Automated frontend + backend contract suites are green (117 + 152). Fixtures rendered as expected in the manual smoke walk captured in `/app/memory/screenshots/`.

## Deferred / accepted known limitations (not defects)

| Ref | Item | Classification | Owner | Notes |
|---|---|---|---|---|
| KL-1 | Diagnosis "Set inactive" state absent | Deferred backlog | Clinical platform lead | Requires backend status-model decision. Do **not** alias to "resolved". Tracked in `ROADMAP.md`. |
| KL-2 | Full 500+ event chart not measured | Deferred to pilot | Platform reliability | Demo seed tops out at ~30 events. Re-measure with production-shape chart during pilot rollout. |
| KL-3 | Preview watermark can occlude back-to-top click in Playwright | Environmental (preview only) | Ops | Does not ship to production tenants. Manual click succeeds. Documented in `PHASE1_TEST_DISPOSITION.md`. |
| KL-4 | `libmagic` recurrence after WatchFiles reload | Container image | Platform | Documented workaround: `sudo apt-get install -y libmagic1 libmagic-dev libmagic-mgc`. Persistent fix requires adding to the base image. |
| KL-5 | First-open workspace-mode discoverability | Deferred (post-freeze usage data) | Product | No onboarding toast until pilot data justifies it. |
| KL-6 | `SectionErrorBoundary` not reused around AI Scribe / Billing Ledger | Deliberately deferred | Platform reliability | Documented rationale in `CLINICAL_REDESIGN_FREEZE.md` §Deferred backlog (6 open design questions). |

## Defect handling protocol (for the human UAT pass)

For each failed UAT scenario the tester must:

1. Reproduce the failure on the fresh seed twice.
2. Classify severity: **Blocker** · **Critical** · **Major** · **Minor**.
3. Classify scope: **In frozen scope** · **Out of scope** · **Environmental** · **Test-data issue**.
4. Only in-scope defects → open a ticket referencing `CLINICAL_REDESIGN_FREEZE.md`.
5. Out-of-scope requests → route to follow-up backlog.
6. Environmental → route to platform reliability, do not block sign-off.
7. Test-data → refresh seed, retest.

Blockers or Criticals in-scope MUST be resolved before G1 sign-off.

## Rules the fix-flow MUST follow

- Reference the release gate the defect blocks (`G1` etc.).
- Reference the specific failing UAT scenario id (1–50).
- Add or update an automated regression test.
- Retest the exact scenario after the fix.
- Attach the retest evidence path (screenshot or Playwright report).
- Do not mutate signed records or preferences to make a scenario pass.
- Do not add new telemetry categories or new preference fields without approval.
- Do not delete a scenario to make the pass count go up.
