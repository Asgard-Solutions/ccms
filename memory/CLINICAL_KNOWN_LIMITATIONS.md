# Clinical Redesign — Known limitations

**Freeze date:** 2026-02-15.

## Environmental (do not affect production tenants)

- **Preview watermark occlusion.** The "Made with Emergent" watermark in the preview environment can occlude the `clinical-back-to-top` button in Playwright automation. Documented in `PHASE1_TEST_DISPOSITION.md`. Ships only to the preview cluster.
- **libmagic recurrence.** After container `WatchFiles` reloads, the `libmagic` C library can be lost and the backend fails to start. Workaround: `sudo apt-get install -y libmagic1 libmagic-dev libmagic-mgc`. Persistent fix requires baking the packages into the base image.

## Measurement gaps

- **200+ event chart** has not been measured on production-shape data. Demo seed tops out at ~30 events per chart. Re-measure during pilot with a real chart.
- **Production build** performance not exercised in this release-gate pass — dev-server build was used. Re-measure under `yarn build` before pilot.

## Product decisions accepted as-is

- **No first-open workspace-mode onboarding toast.** Users must discover the mode switcher via the summary rail. Deferred until post-freeze usage data justifies it.
- **No diagnosis "Set inactive" state.** Backend status model supports `active` / `resolved` only. Do NOT alias `inactive` → `resolved`.
- **No `SectionErrorBoundary` around AI Scribe / Billing Ledger.** These surfaces have different failure/recovery semantics (recording state, unsaved drafts, financial posting) and need a separately scoped resilience review before adoption.
- **Registry-layer flag-matrix test only.** Full-render coverage is delegated to the browser-based UAT — the `craco`/Jest resolver does not understand `react-router-dom` v7's `exports` field cleanly.

## Deliberate deferrals (documented backlog)

- Case-type-based outcome-suggestion mappings.
- Chart-at-a-glance print sheet.
- My Worklist dashboard widget.
- Today's Chart Preview dashboard widget.
- Billing digest.
- Clinic-wide data-quality aggregate endpoint (Ops dashboard).
- Change Healthcare / Optum production transport.
- AI cost estimator on the AI models page.
- Admin-facing feature-flag management panel.
- Application-wide theme overhaul.

## What the release does NOT guarantee

- Does not eliminate all documentation-workflow errors — Next Actions is a checklist of deterministic reminders, not a compliance guarantee.
- Does not detect every billing issue — surfacing depends on existing billing-readiness engine rules.
- Does not enforce clinical significance — outcome snapshots and trend markers are neutral, non-interpretive.
