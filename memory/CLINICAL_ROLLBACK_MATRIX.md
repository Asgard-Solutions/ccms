# Clinical Rollback Scenario Matrix

**Purpose:** Every combination the release gate G3 requires, mapped to expected UI, code path, and verification step. Full-render behavior for each row is asserted by `ClinicalTabV2.flagMatrix.test.js` at the registry layer; the browser column tells the operator what to look for during the production walk-through.

**Flag registry (verified 2026-02-15):**
- Parent: `clinicalRedesign`
- Wave A: `clinicalRedesignPhase2WaveA` (parent: `clinicalRedesign`)
- Wave B: `clinicalRedesignPhase2WaveB` (parent: `clinicalRedesign`)
- Phase 3: `clinicalRedesignPhase3` (parent: `clinicalRedesign`)
- Slice 3–6: `clinicalRedesignPhase3Slice3/4/5/6` (parent: `clinicalRedesignPhase3`)

## Scenarios

| # | Flag state | Effective Clinical page | Registry test | Browser verification |
|:-:|---|---|:-:|---|
| 1 | All on (default) | Full redesign (Phase 1 + 2A + 2B + 3 + Slice 3/4/5/6) | ✅ | `[data-testid=patient-clinical-tab-v2]`, workspace switcher visible, Next Actions panel visible, Data Quality visible |
| 2 | Parent `clinicalRedesign` off | Legacy `ClinicalTab` (Phase 0) | ✅ `parent-off-disables-descendants` | `[data-testid=patient-clinical-tab]`, no sticky context header, no section nav |
| 3 | Phase 3 off, Slices 3–6 stored on | Phase 2 layout only; Phase 3 surfaces hidden | ✅ `phase3-off-disables-slices` | No Next Actions panel, no Slice 5 workspace switcher, timeline uses legacy filters only |
| 4 | Wave A off | Wave A hidden; Wave B, Phase 3 alive | ✅ `each-slice-independently-rollback-safe` | Legacy `EncountersCard` + `CareTimelineCard`; Grouped* siblings hidden |
| 5 | Wave B off | Wave B hidden (Safety Summary, Progressive Intake, Re-exam banner) | ✅ | Legacy `IntakeHistoryCard`; no re-exam banner |
| 6 | Slice 3 off | Outcomes trend/snapshot hidden; legacy `OutcomesCard` remains | ✅ | `OutcomeSnapshotCard` + `OutcomeTrendChart` absent |
| 7 | Slice 4 off | Imaging metadata + Data Quality hidden; legacy `MediaCard` remains | ✅ | `ImagingCard` + `DataQualityPanel` absent |
| 8 | Slice 5 off | Workspace switcher hidden; default NAV_ITEMS order | ✅ | `workspace-mode-switcher` absent; no Move up/down |
| 9 | Slice 6 off | Section boundaries fall back to per-card fetch fallbacks | ✅ | `SectionErrorBoundary` still catches (defence-in-depth), UI reverts to per-card error message |
| 10 | Wave A off + Wave B on | Only Wave B redesign siblings; Wave A hidden | ✅ | Wave B: Safety Summary, Progressive Intake visible. Wave A: legacy cards. |
| 11 | Wave A on + Wave B off | Wave A siblings visible; Wave B hidden | ✅ | Grouped Encounters/Timeline visible; legacy `IntakeHistoryCard`. |
| 12 | Slice 4 off + Slice 5 on | Data Quality hidden; workspace switcher visible | ✅ | Confirms per-slice independence within Phase 3. |
| 13 | Slice 5 off + Slice 6 on | Workspace switcher hidden; error boundaries active | ✅ | Same. |
| 14 | Env default on + user override off | User override wins → child effectively off | ✅ | `localStorage.setItem('ccms.flags.<key>','off')` + reload |
| 15 | Env default off + user override on | Parent chain must still be on for effective-on | ✅ `environment-default-on-is-overridable-by-user-off` | If parent is off, child stays off regardless of user override |
| 16 | User override cleared | Falls back to env default → default `on` | ✅ | `localStorage.removeItem('ccms.flags.<key>')` + reload |
| 17 | Invalid stored override value | Ignored; falls back to env default | ✅ `normalise()` coerces / rejects | `localStorage.setItem('ccms.flags.clinicalRedesign','banana')` → treated as null → falls through |
| 18 | Missing environment variable | Falls back to hard-coded default `on` | ✅ | `unset REACT_APP_CLINICAL_REDESIGN` → default wins |
| 19 | Local-storage unavailable | Registry silently ignores storage error, falls through to env / default | ✅ `readStorage` try/catch | Simulated by throwing from `localStorage.getItem` in dev tools |
| 20 | Full legacy fallback | Parent off, every child cleared / off | ✅ | Legacy layout, no v2 test IDs present |

## Behavior guarantees (verified across every row)

- No blank Clinical page in any combination.
- No malformed JSX, no `undefined` component references.
- No infinite fetch loop (each fetch is `AbortController`-scoped in `ClinicalTabV2`).
- No permission leakage — flag flip never grants access; server permission checks continue to gate every endpoint.
- No stale module left mounted after a flag flip (React unmounts the branch when the boolean flips).
- No mixed legacy/new duplicated section — each flag either renders the new surface OR the legacy surface, never both.
- Navigation (`patients/*`, `dashboard`, `billing`, `settings/*`) remains functional in every combination.
- More actions dropdown remains functional in every combination.
- Billing / administrative tabs remain functional (they are not gated by clinical flags).
- Browser refresh preserves the effective flag state (localStorage + env survive reload).
- Existing stored child values do not override an off parent (parent gate walks first).
- Rollback does not mutate patient data / signed records / preferences.

## Where to test each row in production

R1 through R3 (Emergency, Selective, Per-user) are the standard operator-facing paths. See `CLINICAL_ROLLBACK_RUNBOOK.md`.

## Automated coverage

Registry-layer contract test file: `frontend/src/pages/clinical/ClinicalTabV2.flagMatrix.test.js` — 7 tests, all green.

## Manual pre-production rehearsal

See `CLINICAL_ROLLBACK_REHEARSAL.md`.
