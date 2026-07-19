# Phase 3 Slice 4 — Frozen Contracts

Signed off 2026-02-15. Patient-chart-scoped imaging metadata + data-quality indicators. **No** cross-patient aggregation, tenant-level counters, or operational dashboard behavior lives in this slice.

## 1. Feature flag

- Independent nested flag `clinicalRedesignPhase3Slice4` (child of `clinicalRedesignPhase3`, grandchild of `clinicalRedesign`).
- Default `on`. Legacy `MediaCard` remains mounted below the new `ImagingCard` so the capture workflow keeps working with the flag off — Slice 4 is intentionally read-only.

## 2. Data-quality rules (patient-chart scoped)

| Rule id | Severity | Fires when | Resolution target | Perm-scoped |
|---|:-:|---|---|:-:|
| `missing-primary-diagnosis` | warning | Active work exists + no primary diagnosis linked | `diagnoses` | write |
| `unsigned-note-older-than-7d` | warning | Any encounter draft > 7 days old | `encounters` | write |
| `missing-note-on-encounter` | info | Encounter marked `documentation: missing` | `encounters` | write |
| `encounter-missing-provider` | info | Encounter without `provider_id` and `provider_name` | `encounters` | – |
| `imaging-missing-classification` | info | Media record without `imaging_modality` or `kind` | `imaging` | – |
| `episode-without-encounters` | info | Open episode with no linked visits | `history` | – |
| `active-plan-without-configured-outcomes` | info | Active plan with empty `configured_outcome_measures` | `care-plan` | write |
| `duplicate-outcome-day` | info | Same (instrument, calendar_date) has > 1 entry | `outcomes` | – |

Guarantees:

- **Deterministic**: same input → same output. Tests lock rule accuracy.
- **Severity ladder**: `error` (0) → `warning` (1) → `info` (2). Sort by severity first, priority (position in `RULE_IDS`) second.
- **Non-mutating**: engine only reads chart data — a test asserts input arrays are byte-identical after derivation.
- **Non-clinical language**: labels + why strings pass a regex guardrail (no "improv/deterior/significan/worse/better").
- **Not dismissible**: data-quality rows disappear only when the underlying structured data is fixed.
- **Tenant isolation**: rules operate on chart-scoped inputs already fetched by permission-checked endpoints. No cross-tenant / cross-patient access surface is added.

## 3. Imaging card

- Reads `/api/patients/{id}/clinical/media` (existing endpoint, permission-checked).
- Modality inference: allow-listed set `{xray, mri, ct, ultrasound, other}`. Records without modality/kind surface a "Missing modality" pill that maps 1:1 to the `imaging-missing-classification` data-quality rule.
- Filters (`modalities`) live in transient `useClinicalReturnState({ section: "imaging" })`. They are **not** eligible for durable presets — Slice 2's sanitizer would reject them.
- Empty / no-results / error / permission-denied states each carry an explicit `data-testid`.

## 4. Operational dashboard (out of scope)

Aggregate counters do **not** ship inside `filter_meta` or any patient-scoped response. When an operational dashboard is authorised, it must live behind a **separate versioned** contract:

```jsonc
// FUTURE — not implemented in Slice 4:
GET /api/operations/data-quality/aggregate
{
  "schema_version": "1.0",
  "computed_at": "2026-02-15T12:00:00Z",
  "metrics": {
    "imaging_missing_classification": 3,
    "encounters_missing_provider": 2
  }
}
```

Rules for that future surface (documented here so we don't accidentally leak Slice 4 into it):

- No patient identifiers, record identifiers, free text, diagnosis codes, or per-user performance breakdowns.
- Only allow-listed metric keys with documented denominators.
- Explicit `schema_version`, separate tenant-level permission gate, audit trail, and retention policy.

## 5. Files

| Purpose | File |
|---|---|
| Rule engine | `frontend/src/pages/clinical/dataQualityEngine.js` |
| Rule tests | `frontend/src/pages/clinical/dataQualityEngine.test.js` |
| Panel | `frontend/src/pages/clinical/DataQualityPanel.jsx` |
| Imaging card | `frontend/src/pages/clinical/ImagingCard.jsx` |
| Wiring + flag | `frontend/src/pages/clinical/ClinicalTabV2.jsx`, `frontend/src/utils/featureFlags.js` |
