# Phase 3 Slice 1 — Frozen Contracts

Signed off 2026-02-15. Any change below requires a fresh design pass +
Slice-boundary review + coordinated backend/frontend/telemetry update.

## 1. Next-action priority order (frozen)

The `nextActionsEngine.deriveNextActions()` function MUST emit actions
in exactly this order:

| Position | Rule id | Severity band | Dismissible? |
|:-:|---|---|:-:|
| 1 | `sign-unsigned-note` | warning | no |
| 2 | `complete-missing-documentation` | warning | no |
| 3 | `attach-or-link-diagnosis` | warning | no |
| 4 | `open-blocked-billing-readiness` | destructive | no |
| 5 | `review-billing-warning` | warning | no |
| 6 | `schedule-due-or-overdue-reexam` | warning / destructive | no |
| 7 | `schedule-remaining-planned-visits` | info | **yes** |
| 8 | `review-missing-required-intake` | warning | no |
| 9 | `record-configured-outcome-measure` | info | **yes** |

**Deduplication rule**: `review-billing-warning` MUST be suppressed
whenever `open-blocked-billing-readiness` fires on the same evaluation.
The dedupe is enforced in the engine, not in the UI.

**Permission gates**:

- `canWrite=false` suppresses every write-scoped rule (all rules except
  the billing ones).
- `billingAggregate=null` (permission-denied server-side) suppresses
  both billing rules.

**Explanations** (`why` string) MUST be structured single-sentence
strings referring to structured chart signals only — no PHI, no free
text derived from user input, no clinical vocabulary. Any deviation
requires linter-caught in `nextActionsEngine.test.js`
(`labels and why strings stay non-clinical`).

## 2. Telemetry contract (frozen)

`POST /api/telemetry/ui-action` — two event shapes, mutually exclusive
via Pydantic post-init validation.

### 2a. Care-status shape (unchanged since Phase 1)

```json
{
  "event_name":     "clinical_care_status_action_selected",
  "section_slug":   "current-care-status",
  "source_surface": "patient-clinical",
  "layout_version": "v1" | "v2",
  "action_slug":    "<one of the ActionSlug literals>"
}
```

### 2b. Next-action shape (new in Slice 1)

```json
{
  "event_name":     "clinical_next_action_interaction",
  "section_slug":   "next-actions",
  "source_surface": "patient-clinical",
  "layout_version": "v1" | "v2",
  "action_id":      "<one of the 9 NextActionId literals>",
  "interaction":    "opened" | "dismissed"
}
```

**Frozen rules:**

- `extra="forbid"` — any extra field returns **422 `extra_forbidden`**.
- Cross-field mix — a next-action shape MUST NOT carry `action_slug`
  and a care-status shape MUST NOT carry `action_id`/`interaction`.
  Violations return **422**.
- `interaction` values are attempt-only. Downstream workflow success
  is inferred from the existing audit trail, never from UX telemetry.
- Auto-attached server-side (never client-provided): `tenant_id`,
  `actor_id`, `actor_role`, `ts`, `ua` (first 200 chars).
- Response: **204 No Content**.

### 2c. Allow-listed action-id / interaction / action-slug vocabularies

| Enum | Allowed values |
|---|---|
| `NextActionId` | 9 values from §1 above |
| `NextActionInteraction` | `opened`, `dismissed` |
| `ActionSlug` (care-status) | `open-encounter`, `add-note`, `record-outcome`, `schedule-visit`, `schedule-reexam`, `review-billing-issues`, `edit-missing-information` |

Adding a new value to *any* enum requires updates to:

1. `backend/services/telemetry/router.py` (Literal).
2. `backend/services/telemetry/SCHEMA.md` (docs).
3. `backend/tests/test_next_action_telemetry.py` (test coverage).
4. `frontend/src/utils/telemetry.js` (client allow-list guard).
5. `frontend/src/pages/clinical/nextActionsEngine.js` (rule + tests) —
   for next-action ids only.

## 3. Route-instance token contract

- Storage: `history.state.ccms_route_token`, opaque `r_<random><timestamp>` string.
- Never derived from patient ids, encounter ids, or any PHI substring.
- Fresh token issued on direct URL entry (history.state is empty).
- Reused across browser back/forward, refresh, and same-token remounts.
- Test: `useClinicalReturnState.test.js::token is opaque`.

## 4. Session-store contract

- Key: `sessionStorage["ccms.clinical.returnState.v1"]`.
- Value shape: `{ [routeToken::section]: { data: {...}, exp: <epoch ms> } }`.
- TTL: **30 minutes** (`TTL_MS = 30 * 60 * 1000`). Reads past `exp`
  drop the entry from both memory and sessionStorage.
- Never touches `localStorage`.
- Cleared on:
  1. Explicit call to `emitSessionReset()` (dispatches
     `ccms-session-reset` custom event to any subscriber).
  2. `AuthContext.logout()` calls `emitSessionReset()`.
  3. `PermissionsContext` fires `ccms-session-reset` when the effective
     permission set (`role_keys` join or `tenant_id`) changes.
  4. Per-section `clear()` from consumers that opt out of returning.

## 5. Feature flag contract

- Slice 1 lives behind `clinicalRedesignPhase3` (default `on`).
- `clinicalRedesignPhase3` has parent `clinicalRedesign`. When any
  ancestor is off, `getFlag('clinicalRedesignPhase3')` returns `off`
  regardless of the child's local override — nested rollback works
  from the root.
- Legacy pre-Phase-3 layout MUST render correctly when the flag is off
  (see `PHASE3_SLICE1_FLAG_MATRIX.md`).

## 6. Backwards-compatibility guarantees

- `TreatmentPlanProgress` gains `visits_scheduled` (default `0`) —
  additive, does not break existing consumers (Pydantic `extra="ignore"`).
- `ClinicalSectionCount` gains `last_recorded_at` (default `null`) —
  additive; existing UIs that ignore the field are unaffected.
- `TreatmentPlan*` gains `configured_outcome_measures` (default `[]`)
  — additive, structured slug list, never accepts free text.

Any breaking change to these shapes requires a new response schema
version and a coordinated deprecation window.
