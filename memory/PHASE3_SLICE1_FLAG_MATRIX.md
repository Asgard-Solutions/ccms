# Phase 3 Slice 1 — Feature-flag matrix & rollback evidence

`clinicalRedesignPhase3` is nested under `clinicalRedesign`. Nested
flag semantics: **a child flag can never render as `on` when any
ancestor is off**, regardless of the local override.

## Matrix

| # | `clinicalRedesign` | `clinicalRedesignPhase3` | Effective (`getFlag('clinicalRedesignPhase3')`) | NextActionsPanel rendered? | Slice-2 timeline behaviour |
|:-:|:-:|:-:|:-:|:-:|:-:|
| 1 | on (default) | on (default) | **on** | ✅ yes | Slice 2 UI live under same parent |
| 2 | on | off | off | ❌ no | Slice 2 UI hidden — legacy timeline shown |
| 3 | off | on | off (parent gate) | ❌ no | Entire Phase-3 surface hidden |
| 4 | off | off | off | ❌ no | Entire Phase-3 surface hidden |

## How to test each row

1. Baseline (row 1): default state after login. Panel visible; passes.
2. Row 2: `localStorage.setItem('ccms.flags.clinicalRedesignPhase3', 'off')` + reload.
3. Row 3: `localStorage.setItem('ccms.flags.clinicalRedesign', 'off')` + reload. Note this also hides Phase 1 + Phase 2 surfaces (expected — parent flag is the master gate for the whole redesign).
4. Row 4: both keys set to `off` + reload.
5. Reset: `localStorage.removeItem('ccms.flags.clinicalRedesign'); localStorage.removeItem('ccms.flags.clinicalRedesignPhase3'); location.reload();`

## Rollback evidence

Verified by `testing_agent_v3_fork` iteration 90 (2026-02-15):

- Row 1: `panel_present_on_clinical_tab: PASS` — Derrick Stone patient renders `[data-testid="next-actions-panel"]` with actionable rows.
- Row 2: `feature_flag_phase3_off: PASS` — child flag alone toggled off cleanly hides the panel while sibling Phase-2 surfaces stay active.
- Row 3: `feature_flag_parent_off: PASS` — flipping the parent `clinicalRedesign` off cascades to the child even when phase3 is default-on, confirming nested-parent gating.

## Code path

Nested gating lives in `frontend/src/utils/featureFlags.js` (`getFlag`
walks the `FLAG_PARENTS` chain and returns `off` as soon as any
ancestor evaluates to `off`, before ever consulting the child's own
storage/env/default). Adding a Slice-2 flag downstream requires only
extending the `FLAG_PARENTS` map — no consumer changes.

## Backend-side rollback

None. The Phase-3 features are pure additive UI on top of endpoints
that already ship in production. Rolling back Phase-3 does not require
any migration, seed change, or endpoint deprecation — the flag is
sufficient.
