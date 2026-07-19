# Clinical Contract Change Policy

**Purpose:** Govern any future change to a contract listed in `CLINICAL_CONTRACT_FREEZE.md`.

## Applies to

All 17 contracts in the registry, including but not limited to:
- `ClinicalUIDefaults`, `PreferencesUpdate`
- `UIEventPayload`, `UIActionPayload` (three shapes)
- Feature-flag registry, workspace-mode registry
- Timeline grouped / filter / encounters grouped / billing-readiness aggregate endpoint schemas
- Next Actions rule registry
- Outcome-series derivation contract
- Imaging + data-quality vocabularies
- Return-state hook contract
- StatusBadge dimensions

## Change classes

| Class | Definition | Requires |
|---|---|---|
| **Editorial** | Comment, docstring, README, contract-doc typo. No JSON shape change. | 1 reviewer, no version bump |
| **Additive backwards-compatible** | New optional field with a default, new allow-listed enum value. Existing clients unaffected. | Owner approval + version bump (`x.y.z` → `x.y+1.z`) + contract test update + changelog entry |
| **Breaking** | Removed field, changed field type, removed enum value, tightened validation. | Two-owner approval + major-version bump + migration plan + rollback plan + deprecation window + privacy + security review |

## Required approvals

For every non-editorial change:

1. **Contract owner** (see registry).
2. **Privacy review** — confirm the change does not introduce PHI to a durable or telemetry surface.
3. **Backward-compatibility review** — enumerate every consumer and confirm none breaks.
4. **Test update** — every change lands with an updated contract test and the change reflected in `CLINICAL_CONTRACT_REGISTRY.json`.
5. **Migration plan** — for breaking changes only. Must describe how to migrate durable data (e.g., stored `ClinicalUIDefaults` documents) and how to sequence backend + frontend rollout.
6. **Rollback plan** — for breaking changes only. Must describe how to roll back if the release fails post-deploy.
7. **Release-note entry** — every change generates a release-note line describing the user-visible effect (or "no user-visible effect" if internal).

## Not allowed under freeze

The following operations are **not allowed** while the Clinical redesign remains frozen (from 2026-02-15 forward), regardless of change class:

- New telemetry categories.
- New preference fields (Slice 5 field set is frozen).
- New feature flags.
- New workflow-mode roles.
- Removal of the legacy `ClinicalTab` fallback.
- Reduction of `extra=forbid` scope on any contract.
- New Data Quality rules.
- New outcome-recommendation dimensions.
- New workspace modes.
- New role-mode mappings.
- New AI capabilities on the Clinical page.

Any of these requires a separately scoped follow-up outside this freeze.

## Deprecation policy

- **Additive backwards-compatible** deprecations follow a 90-day window: the field/enum stays supported, marked deprecated in the contract doc, and removed in the next major.
- **Breaking** deprecations require a two-release deprecation window with explicit consumer sign-off.
- Every deprecation opens a matching entry in `CHANGELOG.md`.

## Security + privacy checklist (must be filled per breaking change)

- [ ] Does the change accept any new user-provided string?
- [ ] Is that string length-bounded and enum-restricted where possible?
- [ ] Does the change introduce PHI? (If yes → reject.)
- [ ] Does the change persist across sessions? (If yes → confirm `extra=forbid`.)
- [ ] Does the change fire from an unauthenticated context? (If yes → confirm rate-limited + audit-safe.)
- [ ] Does the change touch signed / immutable records? (If yes → confirm rejection path stays 409.)
- [ ] Does the change touch tenant-isolation boundaries? (If yes → require explicit compliance sign-off.)

## Emergency exceptions

The only emergency exception is a **verified security defect**. In that case:
1. Land the smallest possible fix.
2. File a same-day post-mortem citing this policy.
3. Retro-fit tests + docs before the next release.

No feature request qualifies as an emergency exception.
